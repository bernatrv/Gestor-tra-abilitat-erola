from __future__ import annotations

import os
import re
import subprocess
import tempfile
import json
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.printing import (
    RECEIPT_LABEL_HEIGHT_MM,
    RECEIPT_LABEL_WIDTH_MM,
    load_label_settings,
    print_document_file,
    print_label_windows,
)
from src.label_templates import save_template_job


OUTPUT_DIR = os.path.join("output", "labels")
LABEL_METADATA_KEY = "erola.label.v1"


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _new_label_path(product_name: str, lot_number: str, suffix: str) -> str:
    ensure_output_dir()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", product_name).strip(" .")[:64] or "producte"
    lot = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", lot_number).strip(" .")[:40] or "sense_lot"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    descriptor, path = tempfile.mkstemp(prefix=f"{name}_{lot}_{timestamp}_", suffix=suffix, dir=OUTPUT_DIR)
    os.close(descriptor)
    return path


def _label_font(size):
    for filename in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(filename, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _wrap_label_text(text, font, width=840):
    result = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for character in paragraph:
            if current and font.getlength(current + character) > width:
                result.append(current)
                current = ""
            current += character
        result.append(current)
    return result


def _build_product_label_image(product_name, lot_number, lines, barcode_value=None, canvas_size=None, rotate=False):
    width, height = canvas_size or (900, 0)
    margin = max(8, round(width / 30))
    content_width = max(1, width - margin * 2)
    barcode_source = None
    if barcode_value:
        buffer = BytesIO()
        barcode.get("code128", barcode_value, writer=ImageWriter()).write(
            buffer, options={"write_text": False},
        )
        buffer.seek(0)
        with Image.open(buffer) as source:
            barcode_source = source.convert("RGB")

    scale = 1.0
    while True:
        font_title = _label_font(max(14, round(46 * scale)))
        font_body = _label_font(max(12, round(30 * scale)))
        font_small = _label_font(max(10, round(24 * scale)))
        title_lines = _wrap_label_text(product_name, font_title, content_width)
        body_lines = [wrapped for line in lines for wrapped in _wrap_label_text(line, font_body, content_width)]
        title_line_height = max(1, round(font_title.size * 1.22))
        body_line_height = max(1, round(font_body.size * 1.47))
        small_line_height = max(1, round(font_small.size * 1.25))
        body_top = margin + len(title_lines) * title_line_height + max(4, margin // 2)
        body_bottom = body_top + len(body_lines) * body_line_height
        number_lines = _wrap_label_text(barcode_value, font_small, content_width) if barcode_value else []
        barcode_image = None
        if barcode_source:
            barcode_height = max(1, round(content_width * barcode_source.height / barcode_source.width))
            barcode_image = barcode_source.resize((content_width, barcode_height), Image.Resampling.LANCZOS)
            if canvas_size:
                max_barcode_height = max(1, int(height * 0.22))
                if barcode_image.height > max_barcode_height:
                    resize_width = max(1, round(barcode_image.width * max_barcode_height / barcode_image.height))
                    barcode_image = barcode_image.resize((resize_width, max_barcode_height), Image.Resampling.LANCZOS)
        barcode_top = body_bottom + max(6, margin // 2)
        content_bottom = barcode_top
        if barcode_image:
            content_bottom += barcode_image.height + max(3, margin // 3) + len(number_lines) * small_line_height
        required_height = content_bottom + margin
        if not canvas_size:
            height = max(500, required_height)
            break
        if required_height <= height or scale <= 0.55:
            if required_height > height:
                raise ValueError("El text no cap a la mida d'etiqueta seleccionada. Escull un rotlle més llarg o escurça el contingut.")
            break
        scale = max(0.55, scale - 0.05)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(title_lines):
        draw.text((margin, margin + index * title_line_height), line, fill="black", font=font_title)
    for index, line in enumerate(body_lines):
        draw.text((margin, body_top + index * body_line_height), line, fill="black", font=font_body)
    if barcode_image:
        image.paste(barcode_image, ((width - barcode_image.width) // 2, barcode_top))
        barcode_caption_top = barcode_top + barcode_image.height + max(3, margin // 3)
        for index, line in enumerate(number_lines):
            draw.text(((width - font_small.getlength(line)) / 2, barcode_caption_top + index * small_line_height),
                      line, fill="black", font=font_small)
    if rotate:
        image = image.rotate(90, expand=True)
    return image


def _build_receipt_label_image(product_name, lines, barcode_value):
    dpi = 300
    width = round(RECEIPT_LABEL_WIDTH_MM * dpi / 25.4)
    height = round(RECEIPT_LABEL_HEIGHT_MM * dpi / 25.4)
    margin_x = round(2 * dpi / 25.4)
    margin_y = round(3.5 * dpi / 25.4)
    content_width = width - margin_x * 2

    barcode_image = None
    if barcode_value:
        buffer = BytesIO()
        barcode.get("code128", barcode_value, writer=ImageWriter()).write(
            buffer,
            options={"write_text": False, "module_width": 0.2, "module_height": 10.0, "quiet_zone": 1.0, "dpi": dpi},
        )
        buffer.seek(0)
        with Image.open(buffer) as source:
            barcode_image = source.convert("RGB")
        if barcode_image.width > content_width:
            raise ValueError("El codi de barres és massa llarg per a una etiqueta de 62 × 30 mm.")

    scale = 1.0
    while True:
        font_title = _label_font(max(18, round(34 * scale)))
        font_body = _label_font(max(14, round(20 * scale)))
        font_small = _label_font(max(12, round(17 * scale)))
        title_lines = _wrap_label_text(product_name, font_title, content_width)
        columns = [[], []]
        for index, line in enumerate(lines):
            columns[index % 2].append(_wrap_label_text(line, font_body, content_width // 2 - margin_x // 2))
        rows = []
        for index in range(max(map(len, columns), default=0)):
            left = columns[0][index] if index < len(columns[0]) else []
            right = columns[1][index] if index < len(columns[1]) else []
            rows.append((left, right))

        title_line_height = round(font_title.size * 1.18)
        body_line_height = round(font_body.size * 1.25)
        small_line_height = round(font_small.size * 1.2)
        row_heights = [max(len(left), len(right), 1) * body_line_height for left, right in rows]
        title_height = len(title_lines) * title_line_height
        body_top = margin_y + title_height + max(4, margin_y // 3)
        barcode_top = body_top + sum(row_heights) + (max(4, margin_y // 3) if rows else 0)
        required_height = barcode_top + (barcode_image.height if barcode_image else 0)
        if barcode_image:
            required_height += max(3, margin_y // 4) + small_line_height
        required_height += margin_y
        if required_height <= height:
            break
        if scale <= 0.5:
            raise ValueError("El contingut no cap a l’etiqueta fixa de 62 × 30 mm sense perdre llegibilitat.")
        scale = max(0.5, scale - 0.05)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(title_lines):
        draw.text((margin_x, margin_y + index * title_line_height), line, fill="black", font=font_title)
    row_top = body_top
    column_width = content_width // 2
    for (left, right), row_height in zip(rows, row_heights):
        for column, wrapped_lines in enumerate((left, right)):
            x = margin_x + column * (column_width + margin_x)
            for index, line in enumerate(wrapped_lines):
                draw.text((x, row_top + index * body_line_height), line, fill="black", font=font_body)
        row_top += row_height
    if barcode_image:
        image.paste(barcode_image, ((width - barcode_image.width) // 2, barcode_top))
        caption = str(barcode_value)
        if font_small.getlength(caption) > content_width:
            caption = ""
        if caption:
            caption_top = barcode_top + barcode_image.height + max(3, margin_y // 4)
            draw.text(((width - font_small.getlength(caption)) / 2, caption_top), caption, fill="black", font=font_small)
    return image


def _render_product_label(product_name, lot_number, lines, barcode_value=None, receipt=False):
    image = _build_receipt_label_image(product_name, lines, barcode_value) if receipt else _build_product_label_image(
        product_name, lot_number, lines, barcode_value,
    )
    metadata = PngImagePlugin.PngInfo()
    label = {
        "product_name": product_name,
        "lot_number": lot_number,
        "lines": lines,
        "barcode_value": barcode_value,
    }
    label["label_type"] = "receipt" if receipt else "packaged"
    metadata.add_text(LABEL_METADATA_KEY, json.dumps(label, ensure_ascii=False))
    file_path = _new_label_path(product_name, lot_number, ".png")
    image.save(file_path, format="PNG", dpi=(300, 300), pnginfo=metadata)
    return file_path


def create_label_image(
    product_name: str,
    lot_number: str,
    provenance: str,
    expiry_date: str,
    barcode_value: str,
    weight: str | None = None,
    supplier: str | None = None,
    traceability: list[str] | None = None,
) -> str:
    lines = [f"Proveïdor: {supplier or '-'}", f"Lot d'entrada: {lot_number}"]
    if provenance:
        lines.append(f"Origen: {provenance}")
    lines.append(f"Caducitat: {expiry_date}")
    if weight:
        lines.append(f"Pes: {weight}")
    lines.extend(traceability or [])
    return _render_product_label(product_name, lot_number, lines, barcode_value, receipt=True)


def create_packaged_label_image(product_name: str, lot_number: str, provenance: str, expiry_date: str, weight: float | None = None) -> str:
    lines = [f"Lot: {lot_number}", f"Consum preferent: {expiry_date}"]
    lines.append(f"Pes: {weight:g} kg" if weight is not None else "Pes: -")
    if provenance:
        lines.append(f"Origen: {provenance}")
    return _render_product_label(product_name, lot_number, lines)


def create_template_label_job(template, values, copies):
    path = _new_label_path(values["producte"], values["lot"], ".json")
    return save_template_job(path, template, values, copies)


def create_mixture_label_image(name, lot, sku, expiry, barcode_value, prepared_at, ingredients):
    path = _new_label_path(name, lot, ".pdf")
    document = SimpleDocTemplate(path, pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30,
                                 title=f"Full de producció - {name}")
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("IngredientCell", parent=styles["BodyText"], fontSize=9, leading=12, spaceBefore=0, spaceAfter=0)

    def paragraph(text, style=cell_style):
        return Paragraph(escape(str(text or "-")), style)

    story = [paragraph("Full de producció", styles["Heading2"]), paragraph(name, styles["Title"])]
    metadata = Table([
        [paragraph(f"Lot de sortida: {lot}"), paragraph(f"Data de producció: {prepared_at}")],
        [paragraph(f"Consum preferent: {expiry}"), paragraph(f"SKU: {sku or '-'}")],
    ], colWidths=[document.width / 2] * 2)
    metadata.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    story.extend([metadata, Spacer(1, 14)])
    rows = [[paragraph(label) for label in ("Ingredient", "Proveïdor", "Lot d'entrada", "Caducitat", "Origen")]]

    def append_ingredients(items, prefix=""):
        for index, ingredient in enumerate(items, start=1):
            number = f"{prefix}{index}"
            identity = f"{number}. {ingredient.get('name', '')}"
            if ingredient.get("barcode"):
                identity += f"\nCodi: {ingredient['barcode']}"
            if ingredient.get("sku"):
                identity += f"\nSKU: {ingredient['sku']}"
            rows.append([
                Paragraph(escape(identity).replace("\n", "<br/>"), cell_style),
                paragraph(ingredient.get("supplier")), paragraph(ingredient.get("supplier_lot")),
                paragraph(ingredient.get("expiry")), paragraph(ingredient.get("provenance")),
            ])
            if ingredient.get("ingredients"):
                append_ingredients(ingredient["ingredients"], prefix=f"{number}.")

    append_ingredients(ingredients)
    table = Table(rows, colWidths=[document.width * fraction for fraction in (0.28, 0.21, 0.18, 0.15, 0.18)],
                  repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([table, Spacer(1, 18)])
    if barcode_value:
        code = Code128(barcode_value, barHeight=32, barWidth=0.7)
        if code.width > document.width:
            code.barWidth *= document.width / code.width
        story.extend([code, Spacer(1, 6), paragraph(f"Codi de la barreja: {barcode_value}")])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 30, 16, f"Pàgina {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


def create_session_document(rows, session_id, target_path):
    document = SimpleDocTemplate(str(target_path), pagesize=landscape(A4), leftMargin=24, rightMargin=24,
                                 topMargin=30, bottomMargin=30, title=f"Sessió {session_id}")
    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle("SessionCell", parent=styles["BodyText"], fontSize=8, leading=11)

    def cell(value):
        return Paragraph(escape(str(value if value is not None else "")), cell_style)

    data = [[cell(value) for value in ("Nom", "SKU", "Lot envasat", "Lot d'entrada", "Caducitat", "Origen", "Quantitat", "Pes (kg)")]]
    for row in rows:
        data.append([cell(value) for value in (
            row["name"], row["sku"], row["packed_lot"],
            row["supplier_lot"] if row["prepared_mixture_id"] is None else "",
            row["expiry"], row["provenance"], row["quantity"],
            f"{row['weight']:g}" if row["weight"] is not None else "",
        )])
    table = Table(data, colWidths=[document.width * fraction for fraction in (.23, .08, .13, .13, .12, .13, .09, .09)],
                  repeatRows=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), .5, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(landscape(A4)[0] - 24, 16, f"Pàgina {doc.page}")
        canvas.restoreState()

    document.build([Paragraph(f"Sessió {session_id}", styles["Title"]),
                    Paragraph(f"{len(rows)} registres", styles["BodyText"]), Spacer(1, 12), table],
                   onFirstPage=footer, onLaterPages=footer)
    return str(target_path)


def print_label_file(label_path: str, printer_name: str | None = None, document_printer: str | None = None) -> dict:
    if not label_path or not os.path.exists(label_path):
        return {"status": "error", "message": "No s'ha pogut generar l'etiqueta."}

    if os.path.splitext(label_path)[1].lower() == ".pdf":
        return print_document_file(label_path, printer_name=document_printer)

    if not printer_name:
        return {"status": "skipped", "message": f"No hi ha una impressora configurada; fitxer desat a {label_path}"}

    try:
        if os.name == "nt":
            print_label_windows(label_path, printer_name, load_label_settings())
        else:
            settings = load_label_settings()
            command = ["lp", "-d", printer_name, "-n", "1"]
            with Image.open(label_path) as source:
                metadata = source.info.get(LABEL_METADATA_KEY)
            label = json.loads(metadata) if metadata else {}
            if label.get("label_type") == "receipt" or (
                "label_type" not in label and label.get("barcode_value")
            ):
                settings["label_roll_width_mm"] = RECEIPT_LABEL_WIDTH_MM
                settings["label_roll_length_mm"] = RECEIPT_LABEL_HEIGHT_MM
            command.extend(["-o", f"media=Custom.{settings['label_roll_width_mm']:g}x{settings['label_roll_length_mm']:g}mm"])
            subprocess.run(command + [label_path], check=True, capture_output=True, text=True)
        return {"status": "printed", "message": f"Etiqueta enviada a {printer_name}."}
    except Exception as exc:  # pragma: no cover - depends on printer environment
        return {"status": "error", "message": f"No s'ha pogut imprimir: {exc}\nFitxer desat a {label_path}"}
