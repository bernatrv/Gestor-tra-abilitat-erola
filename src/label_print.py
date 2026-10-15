from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


OUTPUT_DIR = os.path.join("output", "labels")


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


def _render_product_label(product_name, lot_number, lines, barcode_value=None):
    font_title = _label_font(46)
    font_body = _label_font(30)
    font_small = _label_font(24)
    title_lines = _wrap_label_text(product_name, font_title)
    body_lines = [wrapped for line in lines for wrapped in _wrap_label_text(line, font_body)]
    body_top = 25 + len(title_lines) * 56 + 15
    body_bottom = body_top + len(body_lines) * 44
    barcode_top = max(body_bottom + 20, 300)
    number_lines = _wrap_label_text(barcode_value, font_small) if barcode_value else []
    height = max(500, barcode_top + 130 + len(number_lines) * 30 + 25) if barcode_value else max(300, body_bottom + 30)
    image = Image.new("RGB", (900, height), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(title_lines):
        draw.text((30, 20 + index * 56), line, fill="black", font=font_title)
    for index, line in enumerate(body_lines):
        draw.text((30, body_top + index * 44), line, fill="black", font=font_body)
    if barcode_value:
        barcode_buffer = BytesIO()
        barcode_obj = barcode.get("code128", barcode_value, writer=ImageWriter())
        barcode_obj.write(barcode_buffer, options={"write_text": False})
        barcode_buffer.seek(0)
        with Image.open(barcode_buffer) as source:
            barcode_image = source.convert("RGB").resize((700, 120))
        image.paste(barcode_image, (100, barcode_top))
        for index, line in enumerate(number_lines):
            draw.text(((900 - font_small.getlength(line)) / 2, barcode_top + 130 + index * 30),
                      line, fill="black", font=font_small)
    file_path = _new_label_path(product_name, lot_number, ".png")
    image.save(file_path, format="PNG", dpi=(300, 300))
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
    return _render_product_label(product_name, lot_number, lines, barcode_value)


def create_packaged_label_image(product_name: str, lot_number: str, provenance: str, expiry_date: str, weight: float | None = None) -> str:
    lines = [f"Lot: {lot_number}", f"Consum preferent: {expiry_date}"]
    lines.append(f"Pes: {weight:g} kg" if weight is not None else "Pes: -")
    if provenance:
        lines.append(f"Origen: {provenance}")
    return _render_product_label(product_name, lot_number, lines)


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


def print_label_file(label_path: str, printer_name: str | None = None) -> dict:
    if not label_path or not os.path.exists(label_path):
        return {"status": "error", "message": "No s'ha pogut generar l'etiqueta."}

    if not printer_name:
        return {"status": "skipped", "message": f"No hi ha una impressora configurada; fitxer desat a {label_path}"}

    try:
        if os.name == "nt":
            escaped_path = os.path.abspath(label_path).replace("'", "''")
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Start-Process -FilePath '{escaped_path}' -Verb Print -ErrorAction Stop",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(["lp", "-d", printer_name, label_path], check=True, capture_output=True, text=True)
        return {"status": "printed", "message": f"Etiqueta enviada a {printer_name}."}
    except Exception as exc:  # pragma: no cover - depends on printer environment
        return {"status": "error", "message": f"No s'ha pogut imprimir: {exc}\nFitxer desat a {label_path}"}
