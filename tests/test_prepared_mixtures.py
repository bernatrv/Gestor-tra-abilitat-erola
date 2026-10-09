import json
from datetime import date
from unittest.mock import Mock

import pytest
from PIL import Image

import src.label_print as label_print
from src.db import (
    add_packaged_product,
    add_prepared_mixture,
    add_raw_material,
    count_packed_lots_for_sku_on_date,
    get_packaging_ingredient_by_barcode,
    initialize_db,
    list_prepared_mixtures,
    search_packaging_ingredients,
)


def test_prepared_mixture_has_separate_identity_and_traceability(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        ingredients = [{"name": "Farina", "supplier_lot": "ORIGIN-1", "expiry": "2027-12-01", "barcode": "RAW-1"}]
        mixture = add_prepared_mixture(conn, "Barreja", "123", "26100601", "2027-12-01", ingredients)
        assert mixture["barcode"] != mixture["lot"]
        assert json.loads(mixture["ingredients_json"]) == ingredients
        assert len(list_prepared_mixtures(conn)) == 1
        assert conn.execute("SELECT COUNT(*) FROM packaged_products").fetchone()[0] == 0
        found = get_packaging_ingredient_by_barcode(conn, mixture["barcode"])
        assert found["mixture_id"] == mixture["id"]
        assert found["supplier_lot"] == mixture["lot"]
        assert search_packaging_ingredients(conn, "Barr")[0]["barcode"] == mixture["barcode"]
        assert search_packaging_ingredients(conn, "123")[0]["barcode"] == mixture["barcode"]
    finally:
        conn.close()


def test_mixtures_reserve_lots_and_search_keeps_received_materials(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        add_raw_material(conn, "Farina", "Supplier", "ORIGIN-1", "", "2027-12-01", "RAW-1")
        assert search_packaging_ingredients(conn, "Far")[0]["barcode"] == "RAW-1"
        assert get_packaging_ingredient_by_barcode(conn, "RAW-1")["name"] == "Farina"
        add_prepared_mixture(conn, "Barreja", "123", "LOT-1", "2027-12-01", [])
        assert count_packed_lots_for_sku_on_date(conn, "123", date.today().isoformat()) == 1
        add_packaged_product(conn, "Barreja", "LOT-1", "", "", "2027-12-01", "LOT-1", 2, 0, 1, [], "123")
        assert count_packed_lots_for_sku_on_date(conn, "123", date.today().isoformat()) == 1
        initialize_db(str(tmp_path / "test.db")).close()
        assert len(list_prepared_mixtures(conn)) == 1
    finally:
        conn.close()


def test_mixture_label_contains_all_traceability_without_clipping(tmp_path, monkeypatch):
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    captured = {}
    original = label_print.create_label_image

    def capture(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(label_print, "create_label_image", capture)
    ingredients = [
        {"name": f"Ingredient {index}", "supplier_lot": f"ORIGIN-{index}", "supplier": "Supplier",
         "provenance": "Origen", "expiry": "2027-12-01", "barcode": f"RAW-{index}"}
        for index in range(10)
    ]
    path = label_print.create_mixture_label_image("Barreja", "LOT-1", "123", "2027-12-01", "MIX-123", "2026-10-06", ingredients)
    assert "ORIGIN-9" in "\n".join(captured["traceability"])
    assert "RAW-9" in "\n".join(captured["traceability"])
    with Image.open(path) as image:
        assert image.width == 900
        assert image.height > 500
        assert image.crop((100, image.height - 150, 800, image.height - 30)).convert("L").getextrema()[0] < 100


@pytest.fixture
def application(tmp_path, monkeypatch):
    import tkinter as tk
    import src.app as app_module

    monkeypatch.setattr(app_module, "DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path / "labels"))
    printer = Mock(return_value={"status": "printed", "message": "OK"})
    monkeypatch.setattr(app_module, "print_label_file", printer)
    monkeypatch.setattr(app_module.messagebox, "showwarning", Mock())
    monkeypatch.setattr(app_module.messagebox, "showerror", Mock())
    try:
        app = app_module.TraceabilityApp()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter necessita una pantalla: {exc}")
    try:
        yield app, app_module, printer
    finally:
        app.conn.close()
        app.destroy()


def prepare_form(app, quantity=0):
    for name, sku, expiry in (("Farina", "1", "2027-12-01"), ("Oli", "2", "2027-11-01")):
        app._add_package_ingredient({
            "name": name, "sku": sku, "supplier": "Supplier", "supplier_lot": f"ORIGIN-{sku}",
            "provenance": "Origen", "expiry": expiry, "barcode": f"RAW-{sku}",
        })
    app.package_final_product = {"name": "Barreja", "sku": "3"}
    if quantity:
        app.package_quantity_entry.insert(0, str(quantity))


@pytest.mark.parametrize("quantity", [0, 2])
@pytest.mark.parametrize("print_mixture", [False, True])
def test_preparation_and_optional_immediate_packaging(application, monkeypatch, quantity, print_mixture):
    app, app_module, printer = application
    prompt = Mock(return_value=print_mixture)
    monkeypatch.setattr(app_module.messagebox, "askyesno", prompt)
    prepare_form(app, quantity)
    app._finalize_packaging()
    mixture = list_prepared_mixtures(app.conn)[0]
    assert mixture["expiry"] == "2027-11-01"
    assert json.loads(mixture["ingredients_json"])[0]["supplier_lot"] == "ORIGIN-1"
    assert prompt.call_count == 1
    assert printer.call_count == quantity + int(print_mixture)
    packaged = app.conn.execute("SELECT * FROM packaged_products").fetchall()
    assert len(packaged) == int(bool(quantity))
    if quantity:
        assert packaged[0]["quantity"] == quantity
        assert packaged[0]["prepared_mixture_id"] == mixture["id"]
        assert packaged[0]["barcode"] != mixture["barcode"]
    resolved, error = app._resolve_package_ingredient(mixture["barcode"])
    assert error is None and resolved["mixture_id"] == mixture["id"]
    assert app.prepared_mixture_tree.exists(str(mixture["id"]))


def test_history_enter_packages_quantity_and_reuses_preparation(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    mixture = list_prepared_mixtures(app.conn)[0]
    app.notebook.select(app.recipes_tab)
    app.update()
    app.prepared_mixture_tree.selection_set(str(mixture["id"]))
    app.prepared_mixture_tree.focus_force()
    app.prepared_mixture_tree.event_generate("<Return>")
    app.update()
    assert app.focus_get() is app.prepared_mixture_quantity_entry
    app.prepared_mixture_quantity_entry.insert(0, "3")
    app.prepared_mixture_quantity_entry.event_generate("<Return>")
    app.update()
    assert printer.call_count == 3
    packed = app.conn.execute("SELECT * FROM packaged_products").fetchone()
    assert packed["quantity"] == 3
    assert packed["prepared_mixture_id"] == mixture["id"]
    app._load_packaged_product_into_form(packed)
    app.package_quantity_entry.insert(0, "2")
    app._finalize_packaging()
    assert len(list_prepared_mixtures(app.conn)) == 1
    assert printer.call_count == 5
    assert app.conn.execute("SELECT SUM(quantity) FROM packaged_products WHERE prepared_mixture_id = ?", (mixture["id"],)).fetchone()[0] == 5


def test_preparation_survives_print_failure(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=True))
    printer.side_effect = RuntimeError("Printer unavailable")
    prepare_form(app)
    app._finalize_packaging()
    assert len(list_prepared_mixtures(app.conn)) == 1
    assert app_module.messagebox.showwarning.called
    assert app.conn.execute("SELECT COUNT(*) FROM packaged_products").fetchone()[0] == 0


def test_scanned_mixture_preserves_nested_traceability(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    original = list_prepared_mixtures(app.conn)[0]
    material, error = app._resolve_package_ingredient(original["barcode"])
    assert error is None
    app._add_package_ingredient(material)
    assert app.package_reuse_lot == original["lot"]
    assert app.package_prepared_mixture_id == original["id"]
    app._add_package_ingredient({"name": "Sal", "sku": "4", "supplier_lot": "SALT-1", "provenance": "", "expiry": "2027-10-01", "barcode": "RAW-4"})
    app.package_final_product = {"name": "Barreja", "sku": "3"}
    app._finalize_packaging()
    newest = list_prepared_mixtures(app.conn)[0]
    assert newest["lot"] != original["lot"]
    ingredients = json.loads(newest["ingredients_json"])
    assert ingredients[0]["barcode"] == original["barcode"]
    assert ingredients[0]["ingredients"][0]["supplier_lot"] == "ORIGIN-1"


def test_history_validates_quantity_and_keeps_packaging_on_print_failure(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    mixture = list_prepared_mixtures(app.conn)[0]
    app.prepared_mixture_tree.selection_set(str(mixture["id"]))
    app.prepared_mixture_quantity_entry.insert(0, "0")
    app._on_package_prepared_mixture()
    assert app_module.messagebox.showerror.called
    assert app.conn.execute("SELECT COUNT(*) FROM packaged_products").fetchone()[0] == 0
    assert printer.call_count == 0
    app.prepared_mixture_quantity_entry.delete(0, "end")
    app.prepared_mixture_quantity_entry.insert(0, "2")
    printer.side_effect = RuntimeError("Printer unavailable")
    app._on_package_prepared_mixture()
    assert app_module.messagebox.showwarning.called
    packed = app.conn.execute("SELECT * FROM packaged_products").fetchone()
    assert packed["quantity"] == 2
    assert packed["prepared_mixture_id"] == mixture["id"]
    assert len(list_prepared_mixtures(app.conn)) == 1

import csv

from src.db import export_session_csv, replace_catalog_rows, upsert_catalog_rows
from src.history import ActionHistory, Snapshot


def test_export_session_csv_includes_quantity(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        add_packaged_product(conn, "Te", "LOT-1", "", "Cat", "2027-01-01", "LOT-1", 7, 0, 5, [], "1")
        target = tmp_path / "out.csv"
        assert export_session_csv(conn, 5, str(target)) == 1
        with open(target, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["nom", "lot", "caducitat", "origen", "quantitat"]
        assert rows[1] == ["Te", "LOT-1", "2027-01-01", "Cat", "7"]
    finally:
        conn.close()


def test_replace_catalog_rows_discards_previous_import(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        upsert_catalog_rows(conn, [{"sku": "1", "name": "Vell", "weight": 1.0, "category": "c"}], "old.csv")
        replace_catalog_rows(conn, [{"sku": "1", "name": "Nou", "weight": 2.0, "category": "c"}], "new.csv")
        rows = conn.execute("SELECT sku, name FROM product_catalog").fetchall()
        assert [tuple(row) for row in rows] == [("1", "Nou")]
    finally:
        conn.close()


def test_action_history_undo_redo_and_discard():
    history = ActionHistory()
    history.record("a", Snapshot(b"s0", None))
    history.record("b", Snapshot(b"s1", None))
    history.record("c", Snapshot(b"s2", None))
    assert history.move_to(1, lambda: Snapshot(b"s3", None)).db == b"s1"
    assert [entry.status for entry in history.entries] == ["aplicada", "desfeta", "desfeta"]
    assert history.move_to(3, lambda: None).db == b"s3"
    assert history.move_to(0, lambda: None).db == b"s0"
    history.record("d", Snapshot(b"s0b", None))
    assert [entry.status for entry in history.entries] == ["descartada"] * 3 + ["aplicada"]
    assert history.position_of(history.entries[0]) is None
    assert not history.can_redo
