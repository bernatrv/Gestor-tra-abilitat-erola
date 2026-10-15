from datetime import date

import pytest

from src.core import (
    compute_expiry_from_ingredients,
    generate_barcode_from_sku,
    generate_packed_lot,
    match_best_recipe,
    parse_csv_mapping,
    parse_date,
)


def test_catalog_creation_export_and_reference_merge(tmp_path):
    import csv
    import json
    from src.db import (initialize_db, create_catalog_product, export_catalog_csv, update_catalog_field,
                        add_raw_material, rename_reference_value, list_reference_values)

    conn = initialize_db(str(tmp_path / "catalog.db"))
    try:
        product = create_catalog_product(conn, "001", "Farina", "1,5", "Aliment")
        create_catalog_product(conn, "", "Producte nou")
        with pytest.raises(ValueError):
            create_catalog_product(conn, "001", "Duplicat")
        with pytest.raises(ValueError):
            create_catalog_product(conn, "", " ")
        with pytest.raises(ValueError):
            create_catalog_product(conn, "", "Invàlid", "nan")
        update_catalog_field(conn, product["id"], "weight", 2.5)
        output = tmp_path / "catalog.csv"
        assert export_catalog_csv(conn, output) == 2
        with output.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert rows[0] == {"SKU": "001", "Nom": "Farina", "Pes": "2.5", "Categoria": "Aliment"}
        assert rows[1]["Pes"] == ""
        add_raw_material(conn, "Farina", "Supplier", "LOT", "España", "2027-12-01", "RAW")
        nested = [{"provenance": "España", "supplier": "Supplier", "ingredients": [
            {"provenance": "España", "supplier": "Supplier", "supplier_lot": "España"}]}]
        conn.execute("INSERT INTO prepared_mixtures (name, lot, expiry, barcode, created_at, ingredients_json) "
                     "VALUES ('Mix', 'LOT', '2027-12-01', 'MIX', '2026-10-15', ?)", (json.dumps(nested),))
        conn.execute("INSERT INTO packaged_products (name, packed_lot, provenance, created_at, ingredients_json) "
                     "VALUES ('Mix', 'LOT', 'España', '2026-10-15', ?)", (json.dumps(nested),))
        conn.commit()
        rename_reference_value(conn, "provenance", "España", "Espanya")
        rename_reference_value(conn, "supplier", "Supplier", "Unificat")
        assert list_reference_values(conn, "provenance") == ["Espanya"]
        assert list_reference_values(conn, "supplier") == ["Unificat"]
        for table in ("prepared_mixtures", "packaged_products"):
            saved = json.loads(conn.execute(f"SELECT ingredients_json FROM {table}").fetchone()[0])
            assert saved[0]["ingredients"][0] == {"provenance": "Espanya", "supplier": "Unificat", "supplier_lot": "España"}
        assert conn.execute("SELECT provenance FROM packaged_products").fetchone()[0] == "Espanya"
        with pytest.raises(ValueError):
            rename_reference_value(conn, "name", "Mix", "Other")
    finally:
        conn.close()


def test_generate_packed_lot_format():
    lot = generate_packed_lot("P1")
    assert lot.startswith("P1-")
    assert len(lot.split("-")) == 3


def test_generate_barcode_from_sku_uses_date_and_sku():
    barcode = generate_barcode_from_sku("SKU42", reference_date=date(2026, 9, 29))
    assert barcode == "20260929SKU4201"


def test_generate_barcode_from_sku_appends_sequence():
    barcode = generate_barcode_from_sku("SKU42", reference_date=date(2026, 9, 29), sequence=5)
    assert barcode == "20260929SKU4205"


def test_generate_barcode_from_sku_rejects_sequence_out_of_range():
    with pytest.raises(ValueError):
        generate_barcode_from_sku("SKU42", reference_date=date(2026, 9, 29), sequence=0)
    with pytest.raises(ValueError):
        generate_barcode_from_sku("SKU42", reference_date=date(2026, 9, 29), sequence=100)


def test_compute_expiry_from_ingredients_returns_earliest_date():
    ingredients = [
        {"expiry": date(2026, 10, 15), "name": "A"},
        {"expiry": date(2026, 9, 30), "name": "B"},
        {"expiry": date(2026, 10, 20), "name": "C"},
    ]
    assert compute_expiry_from_ingredients(ingredients) == date(2026, 9, 30)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("15-10-2026", date(2026, 10, 15)),
        ("15-10-26", date(2026, 10, 15)),
        ("10-2026", date(2026, 10, 1)),
        ("10-26", date(2026, 10, 1)),
        ("15/10/2026", date(2026, 10, 15)),
        ("2026-10-15", date(2026, 10, 15)),
        ("2026-10-15 00:00:00", date(2026, 10, 15)),
        ("", None),
    ],
)
def test_parse_date_accepts_multiple_formats(text, expected):
    assert parse_date(text) == expected


def test_match_best_recipe_ignores_order_and_picks_highest_overlap():
    recipes = [
        {"id": 1, "name": "Amanida", "ingredient_keys": {"tomaquet", "enciam", "oli"}},
        {"id": 2, "name": "Amanida completa", "ingredient_keys": {"oli", "enciam", "tomaquet", "formatge"}},
    ]
    match = match_best_recipe({"oli", "tomaquet", "enciam"}, recipes)
    assert match["id"] == 1
    assert match["score"] == pytest.approx(1.0)


def test_match_best_recipe_allows_partial_match():
    recipes = [{"id": 1, "name": "Amanida", "ingredient_keys": {"tomaquet", "enciam", "oli"}}]
    match = match_best_recipe({"tomaquet", "formatge"}, recipes)
    assert match is not None
    assert match["id"] == 1
    assert 0 < match["score"] < 1


def test_match_best_recipe_returns_none_without_overlap():
    recipes = [{"id": 1, "name": "Amanida", "ingredient_keys": {"tomaquet", "enciam", "oli"}}]
    assert match_best_recipe({"formatge"}, recipes) is None
    assert match_best_recipe(set(), recipes) is None


def test_parse_date_rejects_invalid_format():
    with pytest.raises(ValueError):
        parse_date("no és una data")


def test_parse_csv_mapping_extracts_selected_fields():
    rows = [
        {"sku": "SKU-1", "nom": "Poma", "pes": "1.2", "date": "2026-01-01"},
        {"sku": "SKU-2", "nom": "Plàtan", "pes": "0.8", "date": "2026-01-02"},
    ]
    mapped = parse_csv_mapping(rows, sku_field="sku", name_field="nom", weight_field="pes")
    assert mapped[0]["sku"] == "SKU-1"
    assert mapped[0]["name"] == "Poma"
    assert mapped[0]["weight"] == 1.2
    assert mapped[0]["category"] == ""


def test_parse_csv_mapping_extracts_category_when_given():
    rows = [{"sku": "SKU-1", "nom": "Poma", "pes": "1.2", "cat": "Fruita"}]
    mapped = parse_csv_mapping(rows, sku_field="sku", name_field="nom", weight_field="pes", category_field="cat")
    assert mapped[0]["category"] == "Fruita"


def test_rank_recipes_matches_by_sku_or_name_and_orders_by_score():
    from src.core import rank_recipes_by_match

    recipes = [
        {"id": 1, "name": "A", "ingredients": [{"sku": "1", "name": "Menta"}, {"sku": "2", "name": "Anís"}]},
        {"id": 2, "name": "B", "ingredients": [{"sku": "9", "name": "Sàlvia"}]},
        {"id": 3, "name": "C", "ingredients": [{"sku": "", "name": "menta "}]},
    ]
    ranked = rank_recipes_by_match([{"sku": "1", "name": "Altre nom"}, {"sku": None, "name": "Anís"}], recipes)
    assert [recipe["id"] for _, recipe in ranked][0] == 1
    assert ranked[0][0] == pytest.approx(1.0)
    assert dict((r["id"], s) for s, r in ranked)[2] == 0.0
    by_name = rank_recipes_by_match([{"sku": None, "name": "Menta"}], recipes)
    assert by_name[0][1]["id"] == 3
