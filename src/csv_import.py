from __future__ import annotations

import csv


def read_csv_file(path: str):
    with open(path, "r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
        headers = reader.fieldnames or []
    return headers, rows


def import_catalog_rows(rows: list[dict], sku_field: str, name_field: str, weight_field: str, source_file: str):
    normalized = []
    for row in rows:
        sku = str(row.get(sku_field, "")).strip()
        name = str(row.get(name_field, "")).strip()
        weight = str(row.get(weight_field, "")).strip().replace(",", ".")
        try:
            weight_value = float(weight)
        except ValueError:
            weight_value = 0.0
        if name:
            normalized.append({"sku": sku, "name": name, "weight": weight_value, "source_file": source_file})
    return normalized
