from __future__ import annotations

import os
import subprocess
from io import BytesIO

import barcode
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont


OUTPUT_DIR = os.path.join("output", "labels")


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


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
    ensure_output_dir()
    try:
        font_title = ImageFont.truetype("arial.ttf", 46)
        font_body = ImageFont.truetype("arial.ttf", 30)
        font_small = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_small = ImageFont.load_default()

    lines = []
    if supplier:
        lines.append(f"Proveïdor: {supplier}")
    lines.append(f"Origen: {provenance}")
    lines.append(f"Caducitat: {expiry_date}")
    if weight:
        lines.append(f"Pes: {weight}")
    lines.extend(traceability or [])
    body_font = font_small if traceability else font_body
    line_height = 34 if traceability else 50
    wrapped_lines = []
    for line in lines:
        current = ""
        for character in line:
            if current and body_font.getlength(current + character) > 840:
                wrapped_lines.append(current)
                current = ""
            current += character
        wrapped_lines.append(current)
    width, height = 900, max(500, 140 + len(wrapped_lines) * line_height + 160)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 20), product_name[:42], fill="black", font=font_title)
    draw.text((30, 90), f"Lot: {lot_number}", fill="black", font=font_body)

    line_y = 140
    for line in wrapped_lines:
        draw.text((30, line_y), line, fill="black", font=body_font)
        line_y += line_height

    barcode_buffer = BytesIO()
    barcode_obj = barcode.get("code128", barcode_value, writer=ImageWriter())
    barcode_obj.write(barcode_buffer, options={"write_text": False})
    barcode_buffer.seek(0)
    barcode_image = Image.open(barcode_buffer).convert("RGB")
    barcode_image = barcode_image.resize((700, 120))
    image.paste(barcode_image, (100, max(line_y + 10, 300)))

    file_path = os.path.join(OUTPUT_DIR, f"{barcode_value if traceability else lot_number}.png")
    image.save(file_path, format="PNG")
    return file_path


def create_mixture_label_image(name, lot, sku, expiry, barcode_value, prepared_at, ingredients):
    lines = ["Barreja sense envasar", f"Producte: {name}", f"SKU: {sku or ''}", f"Preparació: {prepared_at}", f"Codi: {barcode_value}"]

    def append_ingredients(items, prefix=""):
        for index, ingredient in enumerate(items, start=1):
            number = f"{prefix}{index}"
            lines.append(f"Ingredient {number}: {ingredient.get('name', '')} | SKU: {ingredient.get('sku') or ''}")
            lines.append(f"Proveïdor: {ingredient.get('supplier') or ''} | Lot: {ingredient.get('supplier_lot') or ''}")
            lines.append(f"Origen: {ingredient.get('provenance') or ''} | Caducitat: {ingredient.get('expiry') or ''}")
            lines.append(f"Codi: {ingredient.get('barcode') or ''} | Entrada: {ingredient.get('created_at') or ''}")
            if ingredient.get("ingredients"):
                append_ingredients(ingredient["ingredients"], prefix=f"{number}.")

    append_ingredients(ingredients)
    return create_label_image(name, lot, "", expiry, barcode_value, traceability=lines)


def print_label_file(label_path: str, printer_name: str | None = None) -> dict:
    if not label_path or not os.path.exists(label_path):
        return {"status": "error", "message": "No s'ha pogut generar l'etiqueta."}

    if not printer_name:
        return {"status": "skipped", "message": "No hi ha una impressora configurada; l'etiqueta s'ha generat a disc."}

    try:
        if os.name == "nt":
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"& {{ Start-Process -FilePath '{label_path}' -Verb Print -ArgumentList '/p' }}",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            subprocess.run(["lp", "-d", printer_name, label_path], check=True, capture_output=True, text=True)
        return {"status": "printed", "message": f"Etiqueta enviada a {printer_name}."}
    except Exception as exc:  # pragma: no cover - depends on printer environment
        return {"status": "error", "message": f"No s'ha pogut imprimir: {exc}"}
