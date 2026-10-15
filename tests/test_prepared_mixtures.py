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
    create_session,
    export_recipes_json,
    get_packaging_ingredient_by_barcode,
    get_recipe_by_id,
    import_recipes,
    initialize_db,
    list_prepared_mixtures,
    list_packaged_products,
    list_history_packaged_products,
    list_recipes,
    next_inventory_barcode,
    read_recipes_json,
    search_catalog_products,
    search_distinct_raw_material_values,
    search_history_suggestions,
    search_packaging_ingredients,
    search_received_materials,
    update_raw_material,
    update_prepared_mixture,
    upsert_recipe,
    validate_recipes_data,
)


def test_packaging_includes_reportlab_dynamic_barcode_modules():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = ast.parse((root / "GestorTracabilitat.spec").read_text(encoding="utf-8"))
    analysis = next(node for node in ast.walk(spec) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Analysis")
    hidden_imports = next(keyword.value for keyword in analysis.keywords if keyword.arg == "hiddenimports")
    assert isinstance(hidden_imports, ast.Call)
    assert isinstance(hidden_imports.func, ast.Name)
    assert hidden_imports.func.id == "collect_submodules"
    assert ast.literal_eval(hidden_imports.args[0]) == "reportlab.graphics.barcode"
    resource_packages = {
        ast.literal_eval(node.args[0]) for node in ast.walk(spec)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "collect_data_files"
    }
    assert "reportlab" in resource_packages


def test_public_build_configuration_for_windows_and_linux():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = ast.parse((root / "GestorTracabilitat.spec").read_text(encoding="utf-8"))
    executable = next(node for node in ast.walk(spec) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EXE")
    name = next(keyword.value for keyword in executable.keywords if keyword.arg == "name")
    assert ast.literal_eval(name) == "erola producció"
    linux_script = (root / "build_linux.sh").read_bytes()
    assert linux_script.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in linux_script
    assert "dist/linux/erola producció" in linux_script.decode("utf-8")
    windows_script = (root / "build_exe.bat").read_text(encoding="utf-8")
    assert "--distpath dist\\windows" in windows_script


def test_application_icon_loads_independently_of_working_directory(request, monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    app, app_module, printer = request.getfixturevalue("application")
    assert app_module.APP_NAME == "erola producció"
    assert app.title().startswith("erola producció — ")
    assert app._application_icon.width() > 0
    assert app._application_icon.height() == app._application_icon.width()
    source = Path(app_module.__file__).resolve().parent.parent / "icon" / "erola-traçabilitat_ICON.png"
    with Image.open(source) as image:
        assert app._application_icon.width() == image.width
    assert app.tk.call("wm", "iconphoto", app._w, app._application_icon.name) == ""


def test_history_search_and_suggestions_respect_session_scope(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        first = create_session(conn)
        second = create_session(conn)
        for session_id, name, lot in ((first, "Marialluïsa", "LOT-1"), (second, "Farina", "LOT-2")):
            add_packaged_product(conn, name, lot, f"ENTRY-{session_id}", "Catalunya", "2027-12-01", lot, 2, 1, session_id, [], "001")
        assert [row["name"] for row in list_history_packaged_products(conn, session_id=first)] == ["Marialluïsa"]
        assert [row["session_id"] for row in list_history_packaged_products(conn)] == [second, first]
        assert list_history_packaged_products(conn, "Farina", first) == []
        assert list_history_packaged_products(conn, "Marialluisa")[0]["session_id"] == first
        assert list_history_packaged_products(conn, "ENTRY-2")[0]["session_id"] == second
        assert search_history_suggestions(conn, "Far", first) == []
        assert "Farina" in search_history_suggestions(conn, "Far")
        assert "Farina" in search_history_suggestions(conn, "Frnaa")
        assert "Farina" in search_history_suggestions(conn, "Fra")
        assert search_history_suggestions(conn, "Frnaa", first) == []
        assert search_history_suggestions(conn, "Farina")[0] == "Farina"
        assert search_history_suggestions(conn, "xyz-no-results") == []
        assert "Marialluïsa" in search_history_suggestions(conn, "Marialuisa", first)
        assert "Marialluïsa" in search_history_suggestions(conn, "Mria Llusa", first)
        assert len(search_history_suggestions(conn, "Frnaa", limit=1)) == 1
        assert "LOT-2" in search_history_suggestions(conn, "LOT", second)
        assert "2027-12-01" in search_history_suggestions(conn, "2027", first)
        assert len(search_history_suggestions(conn, "Catal")) == 1
        assert search_history_suggestions(conn, " ") == []
        assert len(search_history_suggestions(conn, "LOT", limit=1)) == 1
        add_packaged_product(conn, "Frnaa", "LOT-3", "ENTRY-3", "", "2027-12-01", "LOT-3", 1, 1, second, [], "002")
        suggestions = search_history_suggestions(conn, "Frnaa")
        assert suggestions[0] == "Frnaa"
        assert "Farina" in suggestions
        assert len(suggestions) == len(set(suggestions))
    finally:
        conn.close()


def test_text_inputs_highlight_on_focus_and_restore_background(application):
    import tkinter as tk
    from tkinter import ttk

    app, app_module, printer = application
    app.update()
    supplier = app.raw_supplier
    original = supplier.cget("background")
    supplier.focus_force()
    app.update()
    assert supplier.cget("background") == app._input_focus_color
    app.raw_name.focus_force()
    app.update()
    assert supplier.cget("background") == original
    dialog = tk.Toplevel(app)
    entry = ttk.Entry(dialog)
    entry.pack()
    text = tk.Text(dialog, height=2, background="#eeeeee")
    text.pack()
    readonly = ttk.Entry(dialog, state="readonly")
    readonly.pack()
    app.update()
    original_style = entry.cget("style")
    entry.focus_force()
    app.update()
    assert entry.cget("style") == "FocusedInput.TEntry"
    assert ttk.Style(app).lookup(entry.cget("style"), "fieldbackground") == app._input_focus_color
    text.focus_force()
    app.update()
    assert entry.cget("style") == original_style
    assert text.cget("background") == app._input_focus_color
    readonly.focus_force()
    app.update()
    assert text.cget("background") == "#eeeeee"
    assert readonly.cget("style") != "FocusedInput.TEntry"
    dialog.destroy()


@pytest.mark.parametrize("size", ["1000x650", "1200x760"])
def test_history_sessions_control_table_search_and_export(application, monkeypatch, tmp_path, size):
    import csv

    app, app_module, printer = application
    app.geometry(size)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    old_session = create_session(app.conn)
    for session_id, name in ((app.session_id, "Actual"), (old_session, "Anterior")):
        add_packaged_product(app.conn, name, "LOT-1", "ENTRY-1", "Catalunya", "2027-12-01", "LOT-1", 2, 1, session_id, [], "001")
    app.refresh_history()
    app.notebook.select(app.historial_tab)
    app.update()
    assert app.selected_session_id == app.session_id
    assert app.sessions_tree.selection() == (str(app.session_id),)
    assert app.session_export_button.cget("text") == "Exportar sessió actual"
    assert [app.history_tree.item(iid, "values")[0] for iid in app.history_tree.get_children()] == ["Actual"]
    app.sessions_tree.selection_set(str(old_session))
    app.update()
    assert app.selected_session_id == old_session
    assert app.session_export_button.cget("text") == "Exportar sessió seleccionada"
    assert [app.history_tree.item(iid, "values")[0] for iid in app.history_tree.get_children()] == ["Anterior"]
    app.search_var.insert(0, "Actual")
    app.refresh_history()
    assert not app.history_tree.get_children()
    assert app.sessions_tree.selection() == (str(old_session),)
    app.search_var.delete(0, "end")
    app.refresh_history()
    app.session_export_button.invoke()
    with (tmp_path / "output" / "sessions" / f"sessio_{old_session}.csv").open(encoding="utf-8-sig", newline="") as source:
        assert list(csv.DictReader(source))[0]["nom"] == "Anterior"
    app.sessions_tree.selection_set(str(app.session_id))
    app.update()
    assert app.session_export_button.cget("text") == "Exportar sessió actual"
    app.session_export_button.invoke()
    with (tmp_path / "output" / "sessions" / f"sessio_{app.session_id}.csv").open(encoding="utf-8-sig", newline="") as source:
        assert list(csv.DictReader(source))[0]["nom"] == "Actual"
    assert app.sessions_tree.winfo_rootx() + app.sessions_tree.winfo_width() < app.history_tree.winfo_rootx()
    assert app.session_export_button.winfo_rooty() >= app.sessions_tree.winfo_rooty() + app.sessions_tree.winfo_height()
    assert app.session_export_button.winfo_rootx() + app.session_export_button.winfo_width() <= app.history_tree.winfo_rootx()
    assert app.session_export_button.winfo_rooty() + app.session_export_button.winfo_height() <= app.winfo_rooty() + app.winfo_height()
    assert app.history_tree.winfo_rootx() + app.history_tree.winfo_width() <= app.winfo_rootx() + app.winfo_width()


@pytest.mark.parametrize("size", ["1000x650", "1200x760"])
def test_history_global_search_separators_clear_and_suggestions(application, size, monkeypatch, tmp_path):
    import csv
    from types import SimpleNamespace

    app, app_module, printer = application
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    app.geometry(size)
    other_session = create_session(app.conn)
    for session_id, name, lot in ((app.session_id, "Marialluïsa", "CURRENT-1"),
                                 (other_session, "Farina", "OTHER-1"), (other_session, "Oli", "OTHER-2")):
        add_packaged_product(app.conn, name, lot, "ENTRY-1", "Catalunya", "2027-12-01", lot, 2, 1, session_id, [], "001")
    app.notebook.select(app.historial_tab)
    app.refresh_history()
    app.update()
    assert not app.history_all_sessions_var.get()
    app.search_var.insert(0, "Frnaa")
    app._on_history_search_keyrelease(SimpleNamespace(keysym="r"))
    assert app.suggestions_anchor_entry is None
    app.history_search_button.invoke()
    assert not app.history_tree.get_children()
    app.history_all_sessions_check.invoke()
    assert app.history_all_sessions_var.get()
    assert app.suggestions_items == ["Farina"]
    app._on_suggestions_down_from(app.search_var)
    app._on_suggestions_accept(None)
    assert app.search_var.get() == "Farina"
    rows = [app.history_tree.item(iid) for iid in app.history_tree.get_children()]
    assert len(rows) == 2
    assert "session_separator" in rows[0]["tags"]
    assert rows[1]["values"][0] == "Farina"
    assert "search_match" in app.sessions_tree.item(str(other_session), "tags")
    assert "search_match" not in app.sessions_tree.item(str(app.session_id), "tags")
    assert app.sessions_tree.selection() == (str(app.session_id),)
    app.search_var.delete(0, "end")
    app.search_var.insert(0, "xyz-no-results")
    app.history_search_button.invoke()
    assert not app.history_tree.get_children()
    assert all("search_match" not in app.sessions_tree.item(iid, "tags") for iid in app.sessions_tree.get_children())
    app.history_clear_button.invoke()
    assert app.search_var.get() == ""
    assert app.suggestions_anchor_entry is None
    rows = [app.history_tree.item(iid) for iid in app.history_tree.get_children()]
    assert len(rows) == 5
    assert sum("session_separator" in row["tags"] for row in rows) == 2
    assert all("search_match" not in app.sessions_tree.item(iid, "tags") for iid in app.sessions_tree.get_children())
    assert {row["values"][0] for row in rows if "session_separator" not in row["tags"]} == {"Farina", "Oli", "Marialluïsa"}
    app.session_export_button.invoke()
    with (tmp_path / "output" / "sessions" / f"sessio_{app.session_id}.csv").open(encoding="utf-8-sig", newline="") as source:
        assert [row["nom"] for row in csv.DictReader(source)] == ["Marialluïsa"]
    app.search_var.insert(0, "Catalunya")
    app.history_search_button.invoke()
    assert all("search_match" in app.sessions_tree.item(iid, "tags") for iid in app.sessions_tree.get_children())
    app.history_all_sessions_check.invoke()
    assert all("search_match" not in app.sessions_tree.item(iid, "tags") for iid in app.sessions_tree.get_children())
    app.history_clear_button.invoke()
    rows = [app.history_tree.item(iid) for iid in app.history_tree.get_children()]
    assert len(rows) == 1 and rows[0]["values"][0] == "Marialluïsa"
    app.search_var.insert(0, "Marialuisa")
    app._on_history_search_keyrelease(SimpleNamespace(keysym="a"))
    assert "Marialluïsa" in app.suggestions_items
    app._clear_history_search()
    app.update()
    search = app.history_search_button
    checkbox = app.history_all_sessions_check
    clear = app.history_clear_button
    assert search.winfo_rootx() + search.winfo_width() <= checkbox.winfo_rootx()
    assert checkbox.winfo_rootx() + checkbox.winfo_width() <= clear.winfo_rootx()
    assert clear.winfo_rootx() + clear.winfo_width() <= app.winfo_rootx() + app.winfo_width()


def test_history_session_selection_resets_when_database_changes(application, tmp_path):
    app, app_module, printer = application
    other_session = create_session(app.conn)
    app.refresh_history()
    app.sessions_tree.selection_set(str(other_session))
    app.update()
    assert app.selected_session_id == other_session
    app._open_database(str(tmp_path / "new.db"))
    app.update()
    assert app.selected_session_id == app.session_id
    assert app.sessions_tree.selection() == (str(app.session_id),)
    assert app.sessions_tree.get_children() == (str(app.session_id),)
    assert app.session_export_button.cget("text") == "Exportar sessió actual"
    assert not app.history_tree.get_children()


def test_suggestions_ignore_accents_fragments_and_minor_typos(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        upsert_catalog_rows(conn, [
            {"sku": "001", "name": "Marialluïsa", "weight": 1},
            {"sku": "002", "name": "Farina", "weight": 1},
        ], source_file="test")
        for index in range(2):
            add_raw_material(conn, "Marialluïsa", "El Reloj", f"LOT-{index}", "Català", "2027-12-01", f"RAW-{index}")
        mixture = add_prepared_mixture(conn, "Barreja de Marialluïsa", "003", "MIX-LOT", "2027-12-01", [])
        for query in ("Marialluisa", "MARÍALLUÏSA", "rialluisa", "Marialuisa"):
            assert search_catalog_products(conn, query)[0]["name"] == "Marialluïsa"
            assert len(search_received_materials(conn, query)) == 2
            matches = search_packaging_ingredients(conn, query)
            assert {row["barcode"] for row in matches} == {"RAW-0", "RAW-1", mixture["barcode"]}
        assert search_distinct_raw_material_values(conn, "supplier", "Reloj") == ["El Reloj"]
        assert search_distinct_raw_material_values(conn, "supplier", "Relo") == ["El Reloj"]
        assert search_distinct_raw_material_values(conn, "provenance", "Catala") == ["Català"]
        assert search_catalog_products(conn, "001")[0]["sku"] == "001"
        assert search_catalog_products(conn, "004") == []
        assert search_catalog_products(conn, "xyzabc") == []
        assert search_catalog_products(conn, "   ") == []
        assert len(search_received_materials(conn, "Marialluisa", limit=1)) == 1
        with pytest.raises(ValueError):
            search_distinct_raw_material_values(conn, "name; DROP TABLE raw_materials", "a")
    finally:
        conn.close()


@pytest.mark.parametrize("field,handler,query,expected,tab", [
    ("raw_name", "_on_raw_name_keyrelease", "Marialluisa", "Marialluïsa", "recepcion_tab"),
    ("raw_supplier", "_on_raw_supplier_keyrelease", "Reloj", "El Reloj", "recepcion_tab"),
    ("raw_provenance", "_on_raw_provenance_keyrelease", "Catala", "Català", "recepcion_tab"),
    ("package_ingredient_entry", "_on_package_ingredient_keyrelease", "Marialuisa", "Marialluïsa", "envasat_tab"),
    ("package_final_product_entry", "_on_package_final_product_keyrelease", "Marialluisa", "Marialluïsa", "envasat_tab"),
    ("recipe_product_entry", "_on_recipe_product_keyrelease", "Marialluisa", "Marialluïsa", "recipes_tab"),
])
def test_suggestion_fields_show_accent_insensitive_and_fuzzy_matches(application, field, handler, query, expected, tab):
    from types import SimpleNamespace

    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Marialluïsa", "weight": 1}], source_file="test")
    add_raw_material(app.conn, "Marialluïsa", "El Reloj", "ENTRY-1", "Català", "2027-12-01", "RAW-1")
    app.notebook.select(getattr(app, tab))
    app.update()
    entry = getattr(app, field)
    entry.insert(0, query)
    getattr(app, handler)(SimpleNamespace(keysym="a"))
    assert app.suggestions_listbox is not None
    assert expected in app.suggestions_listbox.get(0)
    app._hide_suggestions()


@pytest.mark.parametrize("changed", [
    {"lot": ""}, {"barcode": ""}, {"expiry": "bad"}, {"created_at": "bad"},
    {"ingredients": []}, {"ingredients": [{"name": " ", "sku": ""}]},
    {"ingredients": [{"name": "Oli", "expiry": "bad"}]},
    {"ingredients": [{"name": "Oli", "quantity": "NaN"}]},
])
def test_prepared_mixture_rejects_invalid_edits(tmp_path, changed):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        ingredients = [{"name": "Oli", "expiry": "2027-12-01"}]
        mixture = add_prepared_mixture(conn, "Barreja", "001", "LOT-1", "2027-12-01", ingredients)
        values = {"lot": "LOT-2", "barcode": "NEW", "expiry": "2027-01-01", "created_at": "2026-10-14", "ingredients": ingredients}
        values.update(changed)
        with pytest.raises(ValueError):
            update_prepared_mixture(conn, mixture["id"], **values)
        assert dict(list_prepared_mixtures(conn)[0]) == dict(mixture)
    finally:
        conn.close()


def test_prepared_mixture_update_preserves_product_and_packaged_snapshot(application):
    app, app_module, printer = application
    ingredients = [{"name": "Farina", "sku": "1", "supplier_lot": "ENTRY-1", "supplier": "El Reloj",
                    "expiry": "2027-12-01", "provenance": "Catalunya", "barcode": "RAW-1"}]
    mixture = add_prepared_mixture(app.conn, "Barreja", "003", "LOT-1", "2027-12-01", ingredients)
    app._record_prepared_mixture_packaging(mixture, 1)
    packaged = dict(list_packaged_products(app.conn)[0])
    assert packaged["prepared_mixture_barcode"] == mixture["barcode"]
    assert app.package_history_tree.set(str(packaged["id"]), "Codi barreja") == mixture["barcode"]
    ingredients[0]["supplier"] = "Nou proveïdor"
    update_prepared_mixture(app.conn, mixture["id"], "LOT-EDITED", "01-11-2027", "MIX-EDITED", "14-10-2026", ingredients)
    updated = list_prepared_mixtures(app.conn)[0]
    assert updated["name"] == "Barreja" and updated["sku"] == "003"
    assert updated["lot"] == "LOT-EDITED" and updated["barcode"] == "MIX-EDITED"
    assert updated["created_at"] == "2026-10-14 00:00:00" and updated["expiry"] == "2027-11-01"
    assert json.loads(updated["ingredients_json"])[0]["supplier"] == "Nou proveïdor"
    app.populate_package_history()
    app.populate_prepared_mixture_history()
    assert app.package_history_tree.set(str(packaged["id"]), "Codi barreja") == "MIX-EDITED"
    assert app.prepared_mixture_tree.set(str(mixture["id"]), "Codi barreja") == "MIX-EDITED"
    after = dict(list_packaged_products(app.conn)[0])
    assert {**after, "prepared_mixture_barcode": packaged["prepared_mixture_barcode"]} == packaged
    add_raw_material(app.conn, "Oli", "Supplier", "ENTRY-2", "", "2027-12-01", "RAW-2")
    with pytest.raises(ValueError):
        update_prepared_mixture(app.conn, mixture["id"], "BAD", "2027-01-01", "RAW-2", "2026-10-15", ingredients)
    assert list_prepared_mixtures(app.conn)[0]["lot"] == "LOT-EDITED"


def test_packaged_history_leaves_preparation_code_blank_when_not_applicable(application):
    app, app_module, printer = application
    product_id = add_packaged_product(app.conn, "Farina", "LOT-1", "ENTRY-1", "Catalunya", "2027-12-01", "LOT-1", 2, 1, app.session_id, [], "001")
    app.populate_package_history()
    assert app.package_history_tree.set(str(product_id), "Codi barreja") == ""


def test_prepared_editor_saves_fields_and_ingredients_and_can_undo(application, monkeypatch):
    from types import SimpleNamespace

    app, app_module, printer = application
    ingredients = [{"name": "Marialluïsa", "sku": "001", "supplier": "El Reloj", "supplier_lot": "ENTRY-1",
                    "expiry": "2027-12-01", "provenance": "Catalunya", "barcode": "RAW-1", "created_at": "2026-10-15 09:00:00",
                    "ingredients": [{"name": "Oli", "supplier": "Supplier", "supplier_lot": "NESTED-1", "expiry": "2027-01-01"}]}]
    mixture = add_prepared_mixture(app.conn, "Barreja", "003", "LOT-1", "2027-12-01", ingredients)
    material = get_packaging_ingredient_by_barcode(app.conn, mixture["barcode"])
    app._add_package_ingredient(material)
    app.package_quantity_entry.insert(0, "4")
    app.populate_prepared_mixture_history()
    app.notebook.select(app.recipes_tab)
    app.update()
    bounds = app.prepared_mixture_tree.bbox(str(mixture["id"]), "Nom")
    app._on_prepared_mixture_double_click(SimpleNamespace(y=bounds[1] + bounds[3] // 2))
    dialog = app.grab_current()
    assert dialog.title() == "Modificar preparació"
    for key in ("name", "sku"):
        assert str(dialog.nametowidget(f"details.{key}").cget("state")) == "readonly"
    for key, value in (("lot", "LOT-EDITED"), ("barcode", "BARCODE-EDITED"), ("expiry", "01-11-2027"), ("created_at", "14-10-2026")):
        entry = dialog.nametowidget(f"details.{key}")
        entry.delete(0, "end")
        entry.insert(0, value)
    dialog.update()
    tree = dialog.nametowidget("ingredient_table.ingredients")
    bounds = tree.bbox(tree.get_children()[0], "supplier")
    for timestamp in (1000, 1100):
        tree.event_generate("<ButtonPress-1>", x=bounds[0] + bounds[2] // 2, y=bounds[1] + bounds[3] // 2, time=timestamp)
        tree.event_generate("<ButtonRelease-1>", x=bounds[0] + bounds[2] // 2, y=bounds[1] + bounds[3] // 2, time=timestamp + 40)
    editor = tree.nametowidget("cell_editor")
    editor.delete(0, "end")
    editor.insert(0, "Nou proveïdor")
    buttons = {button.cget("text"): button for button in dialog.nametowidget("actions").winfo_children()}
    buttons["Afegir ingredient"].invoke()
    assert len(tree.get_children()) == 2
    buttons["Eliminar ingredient"].invoke()
    assert len(tree.get_children()) == 1
    expiry = dialog.nametowidget("details.expiry")
    expiry.delete(0, "end")
    expiry.insert(0, "bad")
    buttons["Guardar"].invoke()
    assert dialog.winfo_exists()
    assert app_module.messagebox.showerror.called
    assert list_prepared_mixtures(app.conn)[0]["lot"] == "LOT-1"
    expiry.delete(0, "end")
    expiry.insert(0, "01-11-2027")
    buttons["Guardar"].invoke()
    updated = list_prepared_mixtures(app.conn)[0]
    assert updated["name"] == "Barreja" and updated["sku"] == "003"
    assert updated["lot"] == "LOT-EDITED" and updated["barcode"] == "BARCODE-EDITED"
    assert json.loads(updated["ingredients_json"])[0]["supplier"] == "Nou proveïdor"
    assert json.loads(updated["ingredients_json"])[0]["ingredients"] == ingredients[0]["ingredients"]
    assert app.package_reuse_lot == "LOT-EDITED"
    assert app.package_ingredients[0]["supplier"] == "Nou proveïdor"
    assert app.package_quantity_entry.get() == "4"
    app.undo_action()
    assert list_prepared_mixtures(app.conn)[0]["barcode"] == mixture["barcode"]
    app.redo_action()
    assert list_prepared_mixtures(app.conn)[0]["barcode"] == "BARCODE-EDITED"
    app._open_prepared_mixture_editor(mixture["id"])
    dialog = app.grab_current()
    dialog.nametowidget("details.barcode").insert(0, "cancelled")
    buttons = {button.cget("text"): button for button in dialog.nametowidget("actions").winfo_children()}
    buttons["Cancel·lar"].invoke()
    assert list_prepared_mixtures(app.conn)[0]["barcode"] == "BARCODE-EDITED"


def test_receipt_edit_preserves_identity(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        material_id = add_raw_material(conn, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01", "RAW-1")
        before = dict(conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
        update_raw_material(conn, material_id, "Oli", "New supplier", "LOT-2", "Nou origen", "2028-01-01")
        after = dict(conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
        assert after == {**before, "name": "Oli", "supplier": "New supplier", "supplier_lot": "LOT-2",
                         "provenance": "Nou origen", "expiry": "2028-01-01"}
    finally:
        conn.close()


def test_receipt_edit_updates_barcode_and_timestamp_without_duplicates(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        material_id = add_raw_material(conn, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01", "RAW-1")
        add_raw_material(conn, "Oli", "Supplier", "ORIGIN-2", "Origen", "2027-12-01", "RAW-2")
        update_raw_material(conn, material_id, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01",
                            barcode="RAW-EDITED", created_at="2026-10-14 13:45:12")
        stored = dict(conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
        assert stored["barcode"] == "RAW-EDITED"
        assert stored["created_at"] == "2026-10-14 13:45:12"
        for invalid_barcode in ("", "RAW-2"):
            with pytest.raises(ValueError):
                update_raw_material(conn, material_id, "Changed", "", "", "", "",
                                    barcode=invalid_barcode, created_at="2026-10-15 12:00:00")
            assert dict(conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == stored
    finally:
        conn.close()


def test_recipes_json_roundtrip_multiple_and_selection(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    other = initialize_db(str(tmp_path / "other.db"))
    try:
        first = upsert_recipe(conn, "001", "Barreja d'arròs", [{"sku": "002", "name": "Arròs", "quantity": 1.25}])
        upsert_recipe(conn, "", "Sense SKU", [{"sku": "", "name": "Oli", "quantity": None}])
        path = tmp_path / "recipes.json"
        assert export_recipes_json(conn, str(path)) == 2
        recipes = read_recipes_json(str(path))
        assert import_recipes(other, recipes) == 2
        assert export_recipes_json(other, str(tmp_path / "copy.json")) == 2
        assert read_recipes_json(str(tmp_path / "copy.json")) == recipes
        assert import_recipes(other, recipes) == 2
        assert len(list_recipes(other)) == 2
        assert export_recipes_json(conn, str(path), [first]) == 1
        assert read_recipes_json(str(path)) == [{
            "sku": "001", "name": "Barreja d'arròs",
            "ingredients": [{"sku": "002", "name": "Arròs", "quantity": 1.25}],
        }]
        recipes[0]["ingredients"][0]["quantity"] = 2.5
        import_recipes(other, recipes)
        stored = get_recipe_by_id(other, list_recipes(other)[0]["id"])
        assert stored["ingredients"][0]["quantity"] == 2.5
    finally:
        conn.close()
        other.close()


@pytest.mark.parametrize("payload", [
    {}, [], {"version": 2, "recipes": []}, [{"name": "Empty", "ingredients": []}],
    [{"name": "Bad", "sku": 123, "ingredients": [{"name": "Oli"}]}],
    [{"name": "Bad", "sku": 0, "ingredients": [{"name": "Oli"}]}],
    [{"name": "Bad", "ingredients": [{"sku": 0}]}],
    [{"name": "Bad", "ingredients": [{"name": "Oli", "quantity": 10 ** 400}]}],
    [{"name": "Bad", "ingredients": [{"name": "Oli", "quantity": float("nan")}]}],
    [{"name": "Bad", "ingredients": [{"name": "Oli", "quantity": True}]}],
    [{"name": "Bad", "ingredients": [{"quantity": 1}]}],
    [{"name": "Duplicate", "ingredients": [{"name": "Oli"}]}] * 2,
])
def test_recipes_reject_invalid_data(payload):
    with pytest.raises(ValueError):
        validate_recipes_data(payload)


def test_recipe_import_rolls_back_entire_batch(tmp_path):
    import sqlite3

    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        first = upsert_recipe(conn, "1", "Original", [{"name": "Oli", "quantity": 1}])
        conn.execute("CREATE TRIGGER reject_recipe BEFORE INSERT ON recipes WHEN NEW.name = 'Fail' "
                     "BEGIN SELECT RAISE(ABORT, 'failure'); END")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            import_recipes(conn, [
                {"sku": "1", "name": "Updated", "ingredients": [{"name": "Sal"}]},
                {"sku": "2", "name": "Fail", "ingredients": [{"name": "Sal"}]},
            ])
        data = get_recipe_by_id(conn, first)
        assert data["recipe"]["name"] == "Original"
        assert data["ingredients"][0]["name"] == "Oli"
        assert len(list_recipes(conn)) == 1
    finally:
        conn.close()


def test_mixture_barcodes_are_daily_short_and_unambiguous(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        prefix = date.today().strftime("%Y%m%d")
        add_raw_material(conn, "Producte", "Supplier", "ENTRY-1", "", "2027-12-01", f"{prefix}00101")
        first = add_prepared_mixture(conn, "Barreja", "001", "LOT-1", "2027-12-01", [])
        second = add_prepared_mixture(conn, "Barreja", "001", "LOT-2", "2027-12-01", [])
        assert first["barcode"] == f"{prefix}00102"
        assert second["barcode"] == f"{prefix}00103"
        assert next_inventory_barcode(conn, "001") == f"{prefix}00104"
        assert next_inventory_barcode(conn, "002") == f"{prefix}00201"
        assert next_inventory_barcode(conn, "001", date(2026, 1, 1)) == "2026010100101"
        assert get_packaging_ingredient_by_barcode(conn, first["barcode"])["mixture_id"] == first["id"]
        with pytest.raises(ValueError):
            update_raw_material(conn, 1, "Producte", "Supplier", "ENTRY-1", "", "2027-12-01", barcode=first["barcode"])
        add_raw_material(conn, "Producte", "Supplier", "ENTRY-99", "", "2027-12-01", f"{prefix}00199")
        with pytest.raises(ValueError, match="99"):
            add_prepared_mixture(conn, "Barreja", "001", "LOT-3", "2027-12-01", [])
        assert len(list_prepared_mixtures(conn)) == 2
    finally:
        conn.close()


def test_receipts_skip_existing_mixture_barcodes(application):
    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Barreja", "weight": 1}], source_file="test")
    mixture = add_prepared_mixture(app.conn, "Barreja", "001", "LOT-1", "2027-12-01", [])
    receipt = app._register_received_material("Barreja", "Supplier", "ENTRY-1", "", "2027-12-01")
    assert mixture["barcode"].endswith("00101")
    assert receipt["barcode"].endswith("00102")
    assert get_packaging_ingredient_by_barcode(app.conn, mixture["barcode"])["mixture_id"] == mixture["id"]
    assert get_packaging_ingredient_by_barcode(app.conn, receipt["barcode"])["id"] == receipt["id"]


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


def test_label_filenames_are_unique_and_safe(tmp_path, monkeypatch):
    from datetime import datetime
    from pathlib import Path

    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(label_print, "datetime", Mock(now=Mock(return_value=datetime(2026, 10, 15, 12, 30, 45, 123456))))
    paths = [label_print.create_label_image("Farina / arròs", "LOT:1", "", "2027-12-01", "RAW-1") for index in range(3)]
    assert len(set(paths)) == 3
    for path in paths:
        assert Path(path).name.startswith("Farina _ arròs_LOT_1_")
        assert "20261015_123045_123456" in Path(path).name
        assert Path(path).parent == tmp_path
        assert Path(path).is_file()
        with Image.open(path) as image:
            assert image.width == 900


@pytest.mark.parametrize("provenance", ["", "Catalunya"])
def test_receipt_and_packaged_labels_have_distinct_fields(tmp_path, monkeypatch, provenance):
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    captured = []
    original = label_print.ImageDraw.ImageDraw.text

    def capture(draw, position, text, *args, **kwargs):
        captured.append((text, kwargs["font"].size))
        bounds = draw.textbbox(position, text, font=kwargs["font"])
        assert 0 <= bounds[0] <= bounds[2] <= draw._image.width
        assert 0 <= bounds[1] <= bounds[3] <= draw._image.height
        return original(draw, position, text, *args, **kwargs)

    monkeypatch.setattr(label_print.ImageDraw.ImageDraw, "text", capture)
    name = "Arròs i farina de producció ecològica " * 4
    path = label_print.create_label_image(name, "ENTRY-1", provenance, "2027-12-01", "2026101500101", supplier="Supplier")
    text = [line for line, size in captured]
    assert "".join(line for line, size in captured if size == 46) == name
    assert "Proveïdor: Supplier" in text
    assert "Lot d'entrada: ENTRY-1" in text
    assert "Caducitat: 2027-12-01" in text
    assert "2026101500101" in text
    assert any(line.startswith("Origen:") for line in text) == bool(provenance)
    with Image.open(path) as image:
        assert image.crop((100, image.height - 55, 800, image.height - 20)).convert("L").getextrema()[0] < 100
    captured.clear()
    barcode_factory = Mock(wraps=label_print.barcode.get)
    monkeypatch.setattr(label_print.barcode, "get", barcode_factory)
    label_print.create_packaged_label_image("Barreja", "26101501", provenance, "2027-12-01")
    text = [line for line, size in captured]
    assert text[:3] == ["Barreja", "Lot: 26101501", "Consum preferent: 2027-12-01"]
    assert len(text) == 3 + bool(provenance)
    barcode_factory.assert_not_called()


def test_label_files_survive_missing_or_failing_printer(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    run = Mock(side_effect=OSError("Printer unavailable"))
    monkeypatch.setattr(label_print.subprocess, "run", run)
    paths = [label_print.create_packaged_label_image("Barreja", "LOT-1", "", "2027-12-01") for index in range(2)]
    result = label_print.print_label_file(paths[0], None)
    assert result["status"] == "skipped"
    assert paths[0] in result["message"]
    run.assert_not_called()
    result = label_print.print_label_file(paths[1], "Missing printer")
    assert result["status"] == "error"
    assert paths[1] in result["message"]
    assert all(Path(path).is_file() for path in paths)


def test_windows_print_escapes_apostrophes_and_checks_errors(tmp_path, monkeypatch):
    if label_print.os.name != "nt":
        pytest.skip("Impressió de Windows")
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    run = Mock()
    monkeypatch.setattr(label_print.subprocess, "run", run)
    path = label_print.create_packaged_label_image("Barreja d'arròs", "LOT-1", "", "2027-12-01")
    assert label_print.print_label_file(path, "Printer")["status"] == "printed"
    assert "d''arròs" in run.call_args.args[0][-1]
    assert run.call_args.kwargs["check"] is True


@pytest.mark.parametrize("count", [10, 60])
def test_mixture_label_contains_all_traceability_without_clipping(tmp_path, monkeypatch, count):
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    captured = {}
    pages = []
    original = label_print.SimpleDocTemplate.build

    def capture(document, story, **kwargs):
        captured["story"] = list(story)
        captured["width"] = document.width
        table = next(item for item in story if isinstance(item, label_print.Table) and item.repeatRows == 1)
        captured["cells"] = [[cell.getPlainText() for cell in row] for row in table._cellvalues]
        metadata = next(item for item in story if isinstance(item, label_print.Table) and item.repeatRows == 0)
        captured["metadata"] = [cell.getPlainText() for row in metadata._cellvalues for cell in row]
        footer = kwargs["onFirstPage"]

        def page(canvas, doc):
            pages.append(doc.page)
            assert canvas._pagesize == label_print.A4
            footer(canvas, doc)

        kwargs.update(onFirstPage=page, onLaterPages=page)
        return original(document, story, **kwargs)

    monkeypatch.setattr(label_print.SimpleDocTemplate, "build", capture)
    ingredients = [
        {"name": f"Ingredient {index}", "supplier_lot": f"ORIGIN-{index}", "supplier": "Supplier",
         "provenance": "Origen", "expiry": "2027-12-01", "barcode": f"RAW-{index}"}
        for index in range(count)
    ]
    ingredients[0]["ingredients"] = [{"name": "Arròs <especial> & oli", "supplier": "Nested supplier",
                                       "supplier_lot": "NESTED-1", "expiry": "2027-01-01", "provenance": "Catalunya"}]
    path = label_print.create_mixture_label_image("Barreja", "LOT-1", "123", "2027-12-01", "MIX-123", "2026-10-06", ingredients)
    table = next(item for item in captured["story"] if isinstance(item, label_print.Table) and item.repeatRows == 1)
    cells = captured["cells"]
    assert cells[0] == ["Ingredient", "Proveïdor", "Lot d'entrada", "Caducitat", "Origen"]
    assert cells[-1][1:] == ["Supplier", f"ORIGIN-{count - 1}", "2027-12-01", "Origen"]
    assert f"RAW-{count - 1}" in cells[-1][0]
    assert "Arròs <especial> & oli" in cells[2][0]
    assert cells[2][1:] == ["Nested supplier", "NESTED-1", "2027-01-01", "Catalunya"]
    assert captured["metadata"] == ["Lot de sortida: LOT-1", "Data de producció: 2026-10-06",
                                    "Consum preferent: 2027-12-01", "SKU: 123"]
    assert len(cells) == count + 2
    if count == 60:
        assert len(pages) > 1
    assert sum(table._colWidths) == pytest.approx(captured["width"])
    with open(path, "rb") as source:
        assert source.read(5) == b"%PDF-"


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


def test_receipt_reprint_and_editor(application, monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace

    app, app_module, printer = application
    material_id = add_raw_material(app.conn, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01", "RAW-1")
    app.refresh_materials()
    app.update()
    app.received_tree.xview_moveto(1)
    app.update()
    button = app._received_print_buttons[str(material_id)]
    assert button.winfo_ismapped()
    before = dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
    button.invoke()
    assert printer.call_count == 1
    assert Image.open(printer.call_args.args[0]).width > 0
    bounds = app.received_tree.bbox(str(material_id), "Nom")
    app._on_received_double_click(SimpleNamespace(y=bounds[1] + bounds[3] // 2))
    dialog = app.grab_current()
    assert isinstance(dialog, tk.Toplevel)
    name = dialog.nametowidget("name")
    name.delete(0, "end")
    name.insert(0, "Farina editada")
    buttons = {child.cget("text"): child for frame in dialog.winfo_children() if isinstance(frame, tk.Frame)
               for child in frame.winfo_children()}
    buttons["Imprimir"].invoke()
    assert printer.call_count == 2
    assert dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == before
    expiry = dialog.nametowidget("expiry")
    expiry.delete(0, "end")
    expiry.insert(0, "invalid")
    buttons["Guardar"].invoke()
    assert app_module.messagebox.showerror.called
    assert dialog.winfo_exists()
    expiry.delete(0, "end")
    expiry.insert(0, "01-02-28")
    barcode = dialog.nametowidget("barcode")
    barcode.delete(0, "end")
    buttons["Guardar"].invoke()
    assert dialog.winfo_exists()
    assert dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == before
    barcode.insert(0, "RAW-EDITED")
    received_at = dialog.nametowidget("created_at")
    received_at.delete(0, "end")
    received_at.insert(0, "invalid")
    buttons["Guardar"].invoke()
    assert dialog.winfo_exists()
    received_at.delete(0, "end")
    received_at.insert(0, "14-10-2026")
    label_factory = Mock(wraps=app_module.create_label_image)
    monkeypatch.setattr(app_module, "create_label_image", label_factory)
    buttons["Imprimir"].invoke()
    assert label_factory.call_args.args[4] == "RAW-EDITED"
    assert dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == before
    buttons["Guardar"].invoke()
    after = dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
    assert after == {**before, "name": "Farina editada", "expiry": "2028-02-01",
                     "barcode": "RAW-EDITED", "created_at": "2026-10-14 00:00:00"}
    assert get_packaging_ingredient_by_barcode(app.conn, "RAW-EDITED")["id"] == material_id
    app.undo_action()
    assert dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == before
    app.redo_action()
    app._open_received_editor(material_id)
    dialog = app.grab_current()
    dialog.nametowidget("name").insert(0, "cancelled")
    dialog.nametowidget("barcode").insert(0, "cancelled")
    dialog.nametowidget("created_at").insert(0, "cancelled")
    for frame in dialog.winfo_children():
        if isinstance(frame, tk.Frame):
            for child in frame.winfo_children():
                if child.cget("text") == "Cancel·lar":
                    child.invoke()
                    break
    assert dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone()) == after
    printer.side_effect = RuntimeError("Printer unavailable")
    app._received_print_buttons[str(material_id)].invoke()
    assert app_module.messagebox.showwarning.called
    assert app.conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0] == 1


def test_recipe_transfer_buttons_and_confirmation(application, monkeypatch, tmp_path):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    confirm = Mock(return_value=False)
    monkeypatch.setattr(app_module.messagebox, "askyesno", confirm)
    first = upsert_recipe(app.conn, "001", "Barreja 1", [{"name": "Oli", "quantity": 1}])
    second = upsert_recipe(app.conn, "002", "Barreja 2", [{"name": "Sal", "quantity": None}])
    upsert_recipe(app.conn, "003", "Barreja 3", [{"name": "Farina", "quantity": 3}])
    app.populate_recipes_list()
    app._populate_package_recipe_lookup()
    app.recipe_list_tree.selection_set(str(first), str(second))
    path = tmp_path / "selected.json"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", Mock(return_value=str(path)))
    app.recipe_export_button.invoke()
    recipes = read_recipes_json(str(path))
    assert len(recipes) == 2
    assert {row["sku"] for row in recipes} == {"001", "002"}
    assert printer.call_count == 0
    recipes[0]["ingredients"][0]["quantity"] = 5
    path.write_text(json.dumps(recipes), encoding="utf-8")
    new_path = tmp_path / "new.json"
    new_path.write_text(json.dumps([{"sku": "004", "name": "Nova", "ingredients": [{"name": "Oli"}]}]), encoding="utf-8")
    monkeypatch.setattr(app_module.filedialog, "askopenfilenames", Mock(return_value=(str(path), str(new_path))))
    app.recipe_import_button.invoke()
    confirm.assert_called_once()
    assert len(list_recipes(app.conn)) == 3
    assert get_recipe_by_id(app.conn, first)["ingredients"][0]["quantity"] == 1
    assert not app.history.can_undo
    confirm.return_value = True
    app._open_package_recipe_view(first)
    app.recipe_import_button.invoke()
    assert len(list_recipes(app.conn)) == 4
    assert get_recipe_by_id(app.conn, first)["ingredients"][0]["quantity"] == 5
    assert app.package_viewing_recipe_ingredients[0]["quantity"] == 5
    assert len(app.recipe_list_tree.get_children()) == 4
    assert len(app._package_recipe_lookup_rows) == 4
    app.undo_action()
    assert len(list_recipes(app.conn)) == 3
    assert get_recipe_by_id(app.conn, first)["ingredients"][0]["quantity"] == 1
    app.redo_action()
    assert len(list_recipes(app.conn)) == 4
    app.recipe_list_tree.selection_remove(*app.recipe_list_tree.selection())
    app.recipe_export_button.invoke()
    assert len(read_recipes_json(str(path))) == 4
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(app_module.filedialog, "askopenfilenames", Mock(return_value=(str(new_path), str(invalid))))
    confirm.reset_mock()
    app.recipe_import_button.invoke()
    assert app_module.messagebox.showerror.called
    confirm.assert_not_called()
    assert len(list_recipes(app.conn)) == 4
    monkeypatch.setattr(app_module.filedialog, "askopenfilenames", Mock(return_value=()))
    app.recipe_import_button.invoke()
    assert len(list_recipes(app.conn)) == 4


@pytest.mark.parametrize("status", ["error", "skipped"])
def test_receipt_print_reports_returned_failure(application, status):
    app, app_module, printer = application
    printer.return_value = {"status": status, "message": "Printer unavailable"}
    app._print_received_material({"name": "Farina", "supplier": "Supplier", "supplier_lot": "LOT-1",
                                  "provenance": "", "expiry": "2027-12-01", "barcode": "RAW-1"})
    assert app_module.messagebox.showwarning.call_args.args[1] == "Printer unavailable"


@pytest.mark.parametrize("size", ["1200x760", "1000x650"])
def test_receipt_and_recipe_controls_fit_window(application, size):
    app, app_module, printer = application
    material_id = add_raw_material(app.conn, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01", "RAW-1")
    app.geometry(size)
    app.refresh_materials()
    app.update()
    tree = app.received_tree
    tree.xview_moveto(1)
    app.update()
    button = app._received_print_buttons[str(material_id)]
    assert button.winfo_ismapped()
    assert button.winfo_width() == button.winfo_height()
    assert button.winfo_width() <= 24
    assert button.cget("text") == "\U0001f5a8"
    button.event_generate("<Enter>")
    assert app._received_print_tooltip is not None
    button.event_generate("<Leave>")
    assert app._received_print_tooltip is None
    assert button.winfo_x() + button.winfo_width() <= tree.winfo_width()
    app.notebook.select(app.recipes_tab)
    app.update()
    for control in (app.recipe_import_button, app.recipe_export_button):
        assert control.winfo_ismapped()
        assert control.winfo_rootx() + control.winfo_width() <= app.winfo_rootx() + app.winfo_width()
        assert control.winfo_rooty() + control.winfo_height() <= app.winfo_rooty() + app.winfo_height()
    assert app.recipe_import_button.winfo_rootx() + app.recipe_import_button.winfo_width() <= app.recipe_export_button.winfo_rootx()
    search = app.recipe_search_entry
    for control in (app.recipe_import_button, app.recipe_export_button):
        assert abs(control.winfo_rooty() - search.winfo_rooty()) <= 4
        assert search.winfo_rootx() + search.winfo_width() <= control.winfo_rootx()
    right = app.recipe_list_tree.master
    assert app.recipe_export_button.winfo_rootx() + app.recipe_export_button.winfo_width() == right.winfo_rootx() + right.winfo_width()


def prepare_form(app, quantity=0):
    for name, sku, expiry in (("Farina", "1", "2027-12-01"), ("Oli", "2", "2027-11-01")):
        app._add_package_ingredient({
            "name": name, "sku": sku, "supplier": "Supplier", "supplier_lot": f"ORIGIN-{sku}",
            "provenance": "Origen", "expiry": expiry, "barcode": f"RAW-{sku}",
        })
    app.package_final_product = {"name": "Barreja", "sku": "3"}
    if quantity:
        app.package_quantity_entry.insert(0, str(quantity))


@pytest.mark.parametrize("status", ["skipped", "error"])
def test_all_packaged_labels_are_saved_before_printer_failure(application, monkeypatch, status):
    from pathlib import Path

    app, app_module, printer = application
    printer.return_value = {"status": status, "message": "Printer unavailable"}
    app._add_package_ingredient({"name": "Farina", "sku": "001", "supplier": "Supplier", "supplier_lot": "ENTRY-1",
                                 "provenance": "Catalunya", "expiry": "2027-12-01", "barcode": "RAW-1"})
    app.package_quantity_entry.insert(0, "3")
    app._finalize_packaging()
    paths = list(Path(label_print.OUTPUT_DIR).glob("*.png"))
    assert len(paths) == 3
    assert len(set(paths)) == 3
    assert printer.call_count == (3 if status == "skipped" else 1)
    assert app.conn.execute("SELECT quantity FROM packaged_products").fetchone()[0] == 3


def test_new_receipt_reports_saved_file_without_printer(application, monkeypatch):
    from pathlib import Path
    from src.db import upsert_catalog_rows

    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Farina", "weight": 1}], source_file="test")
    printer.side_effect = lambda path, name: label_print.print_label_file(path, None)
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    app.raw_name.insert(0, "Farina")
    app.raw_supplier.insert(0, "Supplier")
    app.raw_supplier_lot.insert(0, "ENTRY-1")
    app.raw_expiry.insert(0, "01-12-2027")
    app.save_received_material()
    assert printer.call_count == 1
    assert Path(printer.call_args.args[0]).is_file()
    assert printer.call_args.args[0] in app_module.messagebox.showwarning.call_args.args[1]
    app_module.messagebox.showinfo.assert_not_called()
    assert app.conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0] == 1


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
    paths = [call.args[0] for call in printer.call_args_list]
    assert len(paths) == len(set(paths))
    assert sum(path.endswith(".pdf") for path in paths) == int(print_mixture)
    assert sum(path.endswith(".png") for path in paths) == quantity
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


def test_scanned_mixture_expands_ingredients_and_additions_make_new_preparation(application, monkeypatch):
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
    assert app.package_ingredients == json.loads(original["ingredients_json"])
    assert app.package_final_product == {"name": original["name"], "sku": original["sku"]}
    app._add_package_ingredient({"name": "Sal", "sku": "4", "supplier_lot": "SALT-1", "provenance": "", "expiry": "2027-10-01", "barcode": "RAW-4"})
    assert app.package_mixture_modified
    assert app.package_final_product == {"name": original["name"], "sku": original["sku"]}
    app._finalize_packaging()
    newest = list_prepared_mixtures(app.conn)[0]
    assert newest["lot"] != original["lot"]
    ingredients = json.loads(newest["ingredients_json"])
    assert ingredients[:2] == json.loads(original["ingredients_json"])
    assert ingredients[-1]["supplier_lot"] == "SALT-1"
    assert newest["barcode"] != original["barcode"]
    assert app_module.messagebox.askyesno.call_count == 2


@pytest.mark.parametrize("legacy", [False, True])
def test_scanned_mixture_packages_original_without_production_prompt(application, monkeypatch, legacy):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    original = list_prepared_mixtures(app.conn)[0]
    code = original["barcode"]
    if legacy:
        code = "MIX-legacy-test"
        app.conn.execute("UPDATE prepared_mixtures SET barcode = ? WHERE id = ?", (code, original["id"]))
        app.conn.commit()
    app_module.messagebox.askyesno.reset_mock()
    app.package_ingredient_entry.insert(0, code)
    app._on_package_ingredient_enter(None)
    assert app.package_ingredients == json.loads(original["ingredients_json"])
    assert app.ingredients_list.size() == 2
    assert app.package_final_product_entry.get() == original["name"]
    assert app.package_ingredient_entry.get() == ""
    assert not app.package_mixture_modified
    app.package_quantity_entry.insert(0, "2")
    app._finalize_packaging()
    app_module.messagebox.askyesno.assert_not_called()
    assert len(list_prepared_mixtures(app.conn)) == 1
    packed = app.conn.execute("SELECT * FROM packaged_products").fetchone()
    assert packed["prepared_mixture_id"] == original["id"]
    assert packed["packed_lot"] == original["lot"]
    assert json.loads(packed["ingredients_json"]) == json.loads(original["ingredients_json"])
    assert printer.call_count == 2


def test_removing_loaded_mixture_ingredient_prompts_even_with_one_left(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    original = list_prepared_mixtures(app.conn)[0]
    material, error = app._resolve_package_ingredient(original["barcode"])
    app._add_package_ingredient(material)
    app_module.messagebox.askyesno.reset_mock()
    app.ingredients_list.selection_set(0)
    app._on_ingredients_list_delete(None)
    assert app.package_mixture_modified
    assert len(app.package_ingredients) == 1
    app.undo_action()
    assert not app.package_mixture_modified
    assert app.package_prepared_mixture_id == original["id"]
    assert len(app.package_ingredients) == 2
    app.redo_action()
    assert app.package_mixture_modified
    assert app.package_prepared_mixture_id is None
    app_module.messagebox.askyesno.return_value = True
    app._finalize_packaging()
    app_module.messagebox.askyesno.assert_called_once()
    newest = list_prepared_mixtures(app.conn)[0]
    assert newest["id"] != original["id"]
    assert newest["lot"] != original["lot"]
    assert newest["barcode"] != original["barcode"]
    assert len(json.loads(newest["ingredients_json"])) == 1
    assert printer.call_count == 1
    assert printer.call_args.args[0].endswith(".pdf")


def test_mixture_barcode_limit_keeps_form_and_does_not_prompt(application, monkeypatch):
    app, app_module, printer = application
    prompt = Mock(return_value=True)
    monkeypatch.setattr(app_module.messagebox, "askyesno", prompt)
    prepare_form(app)
    code = f"{date.today().strftime('%Y%m%d')}399"
    add_raw_material(app.conn, "Barreja", "Supplier", "ENTRY-99", "", "2027-12-01", code)
    app._finalize_packaging()
    prompt.assert_not_called()
    assert app_module.messagebox.showerror.called
    assert not list_prepared_mixtures(app.conn)
    assert len(app.package_ingredients) == 2
    printer.assert_not_called()


def test_mixture_added_to_other_ingredients_keeps_nested_traceability(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=False))
    prepare_form(app)
    app._finalize_packaging()
    original = list_prepared_mixtures(app.conn)[0]
    app._add_package_ingredient({"name": "Sal", "sku": "4", "supplier_lot": "SALT-1", "provenance": "", "expiry": "2027-10-01", "barcode": "RAW-4"})
    material, error = app._resolve_package_ingredient(original["barcode"])
    app._add_package_ingredient(material)
    app.package_final_product = {"name": "Barreja nova", "sku": "5"}
    app._finalize_packaging()
    newest = list_prepared_mixtures(app.conn)[0]
    ingredients = json.loads(newest["ingredients_json"])
    assert ingredients[1]["barcode"] == original["barcode"]
    assert ingredients[1]["ingredients"] == json.loads(original["ingredients_json"])


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
