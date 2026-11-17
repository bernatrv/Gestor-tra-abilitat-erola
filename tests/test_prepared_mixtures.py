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
    search_prepared_mixture_suggestions,
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


def test_public_build_configuration_for_windows():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = ast.parse((root / "GestorTracabilitat.spec").read_text(encoding="utf-8"))
    executable = next(node for node in ast.walk(spec) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "EXE")
    name = next(keyword.value for keyword in executable.keywords if keyword.arg == "name")
    assert ast.literal_eval(name) == "erola producció"
    assert not (root / "build_linux.sh").exists()
    release_script = (root / "build_release.ps1").read_text(encoding="utf-8")
    assert "linux" not in release_script.casefold()
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


def test_production_search_includes_nested_ingredient_snapshots(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        first = create_session(conn)
        second = create_session(conn)
        ingredients = [{"name": "Base", "sku": "010", "ingredients": [
            {"name": "Marialluïsa", "sku": "001", "supplier_lot": "INGREDIENT-LOT", "expiry": "2027-12-01"},
        ]}]
        mixture = add_prepared_mixture(conn, "Infusió", "100", "PREP-1", "2027-12-01", ingredients)
        add_prepared_mixture(conn, "Altra", "101", "PREP-2", "2027-12-01", [{"name": "Farina"}])
        add_packaged_product(conn, "Infusió", "PACK-1", "", "", "2027-12-01", "PACK-1", 2, 0.25, first, ingredients, "100")
        add_packaged_product(conn, "Altra", "PACK-2", "", "", "2027-12-01", "PACK-2", 2, 0.25, second, [], "101")
        for term in ("marialluisa", "001", "INGREDIENT-LOT", "Base"):
            assert [row["id"] for row in list_prepared_mixtures(conn, term)] == [mixture["id"]]
            assert [row["name"] for row in list_history_packaged_products(conn, term)] == ["Infusió"]
            assert list_history_packaged_products(conn, term, session_id=second) == []
        assert "Marialluïsa" in search_prepared_mixture_suggestions(conn, "Mria Llusa")
        assert "Marialluïsa" in search_history_suggestions(conn, "Mria Llusa", first)
        assert search_history_suggestions(conn, "Mria Llusa", second) == []
        assert search_prepared_mixture_suggestions(conn, "Infusio")[0] == "Infusió"
        assert search_prepared_mixture_suggestions(conn, "inexistent") == []
        conn.execute("UPDATE prepared_mixtures SET ingredients_json = 'invalid' WHERE id = ?", (mixture["id"],))
        assert [row["id"] for row in list_prepared_mixtures(conn, "Infusio")] == [mixture["id"]]
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


def test_suggestion_popup_dismisses_outside_click_and_focus_loss(application, monkeypatch):
    from types import SimpleNamespace

    app, app_module, printer = application
    app.notebook.select(app.envasat_tab)
    app.update()
    anchor = app.package_final_product_entry
    app._show_suggestions(anchor, ["Arros"], ["Arros"], lambda item: None)
    app.update()
    popup = app.suggestions_window
    assert popup.state() == "normal"

    app._dismiss_suggestions_on_outside_click(SimpleNamespace(widget=app.suggestions_listbox))
    assert popup.state() == "normal"
    app._dismiss_suggestions_on_outside_click(SimpleNamespace(widget=anchor))
    assert popup.state() == "normal"
    app.package_quantity_entry.event_generate("<ButtonPress-1>")
    app.update()
    assert popup.state() == "withdrawn"
    assert app.suggestions_anchor_entry is None

    app._show_suggestions(anchor, ["Arros"], ["Arros"], lambda item: None)
    monkeypatch.setattr(app, "focus_get", Mock(return_value=None))
    anchor.event_generate("<FocusOut>")
    app.update()
    assert app.suggestions_window.state() == "withdrawn"
    assert app.suggestions_anchor_entry is None


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
    kind = {
        "package_ingredient_entry": "package_ingredient",
        "package_final_product_entry": "package_product",
    }.get(field)
    if kind:
        state = app._progressive_suggestion_states[kind]
        generation = state["generation"]
        job = app._progressive_suggestion_jobs.pop(kind)
        app.after_cancel(job)
        app._run_progressive_suggestion_batch(kind, generation)
    assert app.suggestions_listbox is not None
    assert expected in app.suggestions_listbox.get(0)
    app._hide_suggestions()


def test_packaging_exact_suggestions_are_immediate_and_fuzzy_work_is_deferred(application, monkeypatch):
    from difflib import SequenceMatcher
    from types import SimpleNamespace

    app, app_module, _ = application
    upsert_catalog_rows(
        app.conn,
        [{"sku": "001", "name": "Marialluïsa", "weight": 1}],
        source_file="test",
    )
    add_raw_material(app.conn, "Marialluïsa", "El Reloj", "ENTRY-1", "Català", "2027-12-01", "RAW-1")
    sequence_matcher = Mock(wraps=SequenceMatcher)
    monkeypatch.setattr(app_module, "SequenceMatcher", sequence_matcher)
    app.notebook.select(app.envasat_tab)
    app.update()

    entry = app.package_ingredient_entry
    entry.insert(0, "Marialluïsa")
    app._on_package_ingredient_keyrelease(SimpleNamespace(keysym="a"))
    assert app.suggestions_listbox.get(0).startswith("Marialluïsa")
    assert sequence_matcher.call_count == 0

    entry.delete(0, "end")
    entry.insert(0, "Marialuisa")
    app._on_package_ingredient_keyrelease(SimpleNamespace(keysym="a"))
    assert sequence_matcher.call_count == 0
    state = app._progressive_suggestion_states["package_ingredient"]
    generation = state["generation"]
    job = app._progressive_suggestion_jobs.pop("package_ingredient")
    app.after_cancel(job)
    app._run_progressive_suggestion_batch("package_ingredient", generation)
    assert sequence_matcher.call_count > 0
    assert "Marialluïsa" in app.suggestions_listbox.get(0)


def test_obsolete_packaging_fuzzy_search_is_cancelled(application, monkeypatch):
    from difflib import SequenceMatcher
    from types import SimpleNamespace

    app, app_module, _ = application
    add_raw_material(app.conn, "Marialluïsa", "El Reloj", "ENTRY-1", "Català", "2027-12-01", "RAW-1")
    sequence_matcher = Mock(wraps=SequenceMatcher)
    monkeypatch.setattr(app_module, "SequenceMatcher", sequence_matcher)
    app.notebook.select(app.envasat_tab)
    app.update()

    entry = app.package_ingredient_entry
    entry.insert(0, "Marialuisa")
    app._on_package_ingredient_keyrelease(SimpleNamespace(keysym="a"))
    old_state = app._progressive_suggestion_states["package_ingredient"]
    old_generation = old_state["generation"]
    old_job = app._progressive_suggestion_jobs.pop("package_ingredient")
    app.after_cancel(old_job)

    entry.delete(0, "end")
    entry.insert(0, "NoMatch")
    app._on_package_ingredient_keyrelease(SimpleNamespace(keysym="h"))
    new_state = app._progressive_suggestion_states["package_ingredient"]
    assert new_state["generation"] != old_generation
    app._run_progressive_suggestion_batch("package_ingredient", old_generation)
    assert app._progressive_suggestion_states["package_ingredient"] is new_state
    assert sequence_matcher.call_count == 0

    new_job = app._progressive_suggestion_jobs.pop("package_ingredient")
    app.after_cancel(new_job)
    app._run_progressive_suggestion_batch("package_ingredient", new_state["generation"])
    assert sequence_matcher.call_count > 0
    assert app.suggestions_listbox is None or app.suggestions_listbox.size() == 0


def test_packaging_approximate_suggestions_are_added_in_batches(application):
    from types import SimpleNamespace

    app, _, _ = application
    for index in range(30):
        add_raw_material(
            app.conn,
            f"Marialluïsa {index:02}",
            "El Reloj",
            f"ENTRY-{index}",
            "Català",
            "2027-12-01",
            f"RAW-{index}",
        )
    app.notebook.select(app.envasat_tab)
    app.update()
    app.package_ingredient_entry.insert(0, "Marialuisa")
    app._on_package_ingredient_keyrelease(SimpleNamespace(keysym="a"))
    state = app._progressive_suggestion_states["package_ingredient"]
    generation = state["generation"]
    job = app._progressive_suggestion_jobs.pop("package_ingredient")
    app.after_cancel(job)
    app._run_progressive_suggestion_batch("package_ingredient", generation)

    assert state["cursor"] == 24
    assert app.suggestions_listbox is not None
    assert app.suggestions_listbox.size() > 0
    assert "Marialluïsa" in app.suggestions_listbox.get(0)
    next_job = app._progressive_suggestion_jobs.pop("package_ingredient")
    app.after_cancel(next_job)
    app._run_progressive_suggestion_batch("package_ingredient", generation)
    assert state["cursor"] == 30
    assert app.suggestions_listbox.size() == 8


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
            assert image.size == (732, 354)


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
    assert "".join(line for line, size in captured)[:len(name)] == name
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
    label_print.create_packaged_label_image("Barreja", "26101501", provenance, "2027-12-01", weight=0.25)
    text = [line for line, size in captured]
    assert text[:3] == ["Barreja", "Lot: 26101501", "Consum preferent: 2027-12-01"]
    assert "Pes: 0.25 kg" in text
    assert len(text) == 4 + bool(provenance)
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


def test_windows_print_uses_selected_printer_without_shell(tmp_path, monkeypatch):
    if label_print.os.name != "nt":
        pytest.skip("Impressió de Windows")
    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    run = Mock()
    monkeypatch.setattr(label_print.subprocess, "run", run)
    backend = Mock()
    monkeypatch.setattr(label_print, "print_label_windows", backend)
    path = label_print.create_packaged_label_image("Barreja d'arròs", "LOT-1", "", "2027-12-01")
    assert label_print.print_label_file(path, "Printer")["status"] == "printed"
    assert backend.call_args.args[:2] == (path, "Printer")
    run.assert_not_called()
    backend.side_effect = OSError("Offline")
    assert label_print.print_label_file(path, "Printer")["status"] == "error"


@pytest.mark.parametrize("size", ["1000x550", "1200x760"])
def test_received_history_all_rows_search_colors_and_editor(application, monkeypatch, size):
    from datetime import timedelta
    from types import SimpleNamespace
    from src.db import list_received_history

    app, app_module, printer = application
    app.geometry(size)
    upsert_catalog_rows(app.conn, [{"name": "Arròs", "sku": "00123", "weight": 1}], "test.csv")
    expiries = [(date.today() - timedelta(days=1)).isoformat(), date.today().isoformat(), "2099-12-31"]
    for index in range(25):
        add_raw_material(app.conn, "Arròs", "Proveedor", f"ENTRY-{index:02}", "Catalunya", expiries[index % 3], f"BAR-{index:02}")
    app.refresh_materials()
    app.notebook.select(app.csv_tab)
    app.products_notebook.select(app.received_history_tab)
    app.focus_force()
    app.update()
    app._on_products_tab_changed()
    assert app.focus_get() is app.received_search_entry
    tree = app.received_history_tree
    assert len(tree.get_children()) == 25
    assert len(app.received_tree.get_children()) == 20
    for row in list_received_history(app.conn):
        expected = ("expired", "expiring", "")[int(row["barcode"][-2:]) % 3]
        assert tuple(tree.item(str(row["id"]), "tags") or ()) == ((expected,) if expected else ())
    for query in ("arros", "00123", "BAR-24", "ENTRY-24", date.today().isoformat(), date.today().strftime("%d/%m/%Y")):
        app.received_search_entry.delete(0, "end")
        app.received_search_entry.insert(0, query)
        app._on_received_search()
        assert tree.get_children(), query
        assert app.suggestions_items, query
    app.received_search_entry.delete(0, "end")
    app.received_search_entry.insert(0, "ENTRY-24")
    app._on_received_search()
    suggestion = app.suggestions_items[0]
    app.suggestions_apply(suggestion)
    assert len(tree.get_children()) == 1
    assert tree.set(tree.get_children()[0], "Codi de barres") == "BAR-24"
    app._clear_received_search()
    for column in tree["columns"]:
        app._sort_tree_by_column(tree, column)
        app._sort_tree_by_column(tree, column)
    app.update()
    iid = tree.get_children()[0]
    tree.see(iid)
    app.update()
    bounds = tree.bbox(iid, "Nom")
    editor = Mock()
    monkeypatch.setattr(app, "_open_received_editor", editor)
    app._on_received_history_double_click(SimpleNamespace(y=bounds[1] + bounds[3] // 2))
    editor.assert_called_once_with(int(iid))
    assert tree.winfo_rootx() + tree.winfo_width() <= app.winfo_rootx() + app.winfo_width()


def test_packaging_expiry_warnings_simple_and_nested(application):
    from datetime import timedelta

    app, app_module, printer = application
    expired = (date.today() - timedelta(days=1)).isoformat()
    for expiry in (expired, date.today().isoformat()):
        app._clear_package_form()
        app.package_ingredients = [{"name": "Arròs", "sku": "001", "supplier_lot": "ENTRY", "provenance": "", "expiry": expiry}]
        app.package_final_product = {"name": "Arròs", "sku": "001"}
        app.package_quantity_entry.insert(0, "1")
        app_module.messagebox.showwarning.reset_mock()
        app._finalize_packaging_impl()
        app_module.messagebox.showwarning.assert_called_once()
        assert app_module.messagebox.showwarning.call_args.args[0] == "Avís de caducitat"
        assert "ENTRY" in app_module.messagebox.showwarning.call_args.args[1]
    mixture = add_prepared_mixture(app.conn, "Barreja", "002", "MIX-1", "2099-12-31", [
        {"name": "Subbarreja", "expiry": "2099-12-31", "ingredients": [
            {"name": "Ingredient caducat", "supplier_lot": "NESTED", "expiry": expired},
        ]},
    ])
    app_module.messagebox.showwarning.reset_mock()
    app._record_prepared_mixture_packaging(mixture, 1)
    app_module.messagebox.showwarning.assert_called_once()
    assert "NESTED" in app_module.messagebox.showwarning.call_args.args[1]
    assert "CADUCAT" in app_module.messagebox.showwarning.call_args.args[1]
    assert len(list_packaged_products(app.conn)) == 3


def test_session_pdf_and_csv_include_input_lots_only_for_simple_products(application, monkeypatch, tmp_path):
    import pymupdf

    app, app_module, printer = application
    monkeypatch.chdir(tmp_path)
    for index in range(70):
        add_packaged_product(app.conn, f"Producte {index}", f"LOT-{index}", "ENTRY-SIMPLE", "Catalunya", "2099-12-31", f"LOT-{index}", 2, .25, app.session_id, [], "001")
    mixture = add_prepared_mixture(app.conn, "Barreja", "002", "MIX-1", "2099-12-31", [{"name": "Ingredient", "expiry": "2099-12-31"}])
    add_packaged_product(app.conn, "Barreja", "MIX-1", "HIDDEN-MIX-LOT", "", "2099-12-31", "MIX-1", 1, 1, app.session_id, [], "002", mixture["id"])
    app.search_var.insert(0, "No match")
    app._print_session()
    path, selected_printer = printer.call_args.args
    assert selected_printer is None
    with pymupdf.open(path) as document:
        assert len(document) > 1
        text = "".join(page.get_text() for page in document)
        assert "ENTRY-SIMPLE" in text
        assert "HIDDEN-MIX-LOT" not in text
        assert "Producte 69" in text
        assert document[0].rect.width > document[0].rect.height
    csv_path = tmp_path / "session.csv"
    export_session_csv(app.conn, app.session_id, str(csv_path))
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 71
    assert next(row for row in rows if row["nom"] == "Barreja")["lot_entrada"] == ""
    app.search_var.delete(0, "end")
    app.refresh_history()
    for iid in app.history_tree.get_children():
        values = app.history_tree.item(iid, "values")
        assert values[2] == ("" if values[0] == "Barreja" else "ENTRY-SIMPLE")


@pytest.mark.parametrize("status", ["cancelled", "opened"])
def test_a4_cancel_is_not_printer_failure(application, tmp_path, status):
    app, app_module, printer = application
    printer.return_value = {"status": status}
    assert app._print_saved_labels([str(tmp_path / "document.pdf")])["status"] == status
    app._show_label_saved_notice.assert_not_called()
    app_module.messagebox.showwarning.assert_not_called()


@pytest.mark.parametrize("size", ["760x500", "980x620"])
def test_template_manager_opens_with_actions_without_changing_settings(application, size):
    from src.label_templates import load_template_config

    app, app_module, printer = application
    dialog = app._show_template_manager()
    assert dialog is not None
    dialog.geometry(size)
    dialog.update()
    assert app.grab_current() is dialog
    assert dialog.title() == "Plantilles d'etiquetes"
    assert [dialog.notebook.tab(tab, "text") for tab in dialog.notebook.tabs()] == ["Plantilles", "Productes", "Accions"]
    assert dialog.actions_tree.get_children() == ("recepcio",)
    assert not load_template_config(app.conn)["enabled"]
    for tab in dialog.notebook.tabs():
        dialog.notebook.select(tab)
        dialog.update()
        for panel in (dialog, *dialog.winfo_children()):
            for widget in panel.winfo_children():
                if widget.winfo_ismapped():
                    assert widget.winfo_rootx() + widget.winfo_width() <= dialog.winfo_rootx() + dialog.winfo_width()
                    assert widget.winfo_rooty() + widget.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()
    dialog.destroy()
    app._print_received_material({"name": "Arros", "supplier_lot": "L-1", "provenance": "", "expiry": "2099-12-01", "barcode": "12345", "supplier": "Supplier"})
    printer.assert_not_called()
    assert "no té cap plantilla" in app_module.messagebox.showwarning.call_args.args[1]


@pytest.mark.parametrize("prepared", [False, True])
def test_packaging_uses_assigned_template_and_preserves_saved_job(application, monkeypatch, tmp_path, prepared):
    import zipfile
    from src import label_templates as templates
    from src.core import CSV_EXTRA_FIELDS

    app, app_module, printer = application
    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        objects = ['<objectStyle objectName="Producte"/>', '<objectStyle objectName="Lot"/>']
        objects.extend(f'<objectStyle objectName="{field}"/>' for field in CSV_EXTRA_FIELDS)
        archive.writestr("label.xml", f"<label>{''.join(objects)}</label>")
    identity = templates.import_label_template(app.conn, path)
    template_fields = {"Producte": "producte", "Lot": "lot"}
    template_fields.update({field: field for field in CSV_EXTRA_FIELDS})
    templates.configure_label_template(app.conn, identity, "Bossa", template_fields)
    templates.assign_product_template(app.conn, [{"sku": "001", "name": "Arros"}], identity)
    templates.set_templates_enabled(app.conn, True)
    metadata = {field: f"valor {index}" for index, field in enumerate(CSV_EXTRA_FIELDS)}
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Arros", "weight": 1, **metadata}], "test.csv")
    backend = Mock(return_value={"status": "printed"})
    monkeypatch.setattr(app_module, "print_template_job", backend)
    if prepared:
        mixture = add_prepared_mixture(app.conn, "Arros", "001", "MIX-TEMPLATE", "2099-12-31", [{"name": "Ingredient", "expiry": "2099-12-31"}])
        app._record_prepared_mixture_packaging(mixture, 3)
    else:
        app.package_ingredients = [{"name": "Arros", "sku": "001", "supplier_lot": "ENTRY-1", "provenance": "Catalunya", "expiry": "2099-12-31"}]
        app.package_final_product = {"name": "Arros", "sku": "001"}
        app.package_quantity_entry.insert(0, "3")
        app._finalize_packaging_impl()
    assert len(list_packaged_products(app.conn)) == 1
    backend.assert_called_once()
    printer.assert_not_called()
    job_path = backend.call_args.args[0]
    with open(job_path, encoding="utf-8") as source:
        job = json.load(source)
    assert job["copies"] == 3
    assert job["values"]["producte"] == "Arros"
    assert job["template"]["fields"] == template_fields
    assert {field: job["values"][field] for field in CSV_EXTRA_FIELDS} == metadata
    snapshot = job["template"]["content"]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Updated"/></label>')
    templates.import_label_template(app.conn, path, identity)
    with open(job_path, encoding="utf-8") as source:
        assert json.load(source)["template"]["content"] == snapshot
    backend.return_value = {"status": "error", "message": "b-PAC missing"}
    app._print_saved_labels([job_path])
    assert app_module.messagebox.showwarning.call_args.args[0] == "Plantilla no impresa"
    assert len(list_packaged_products(app.conn)) == 1


def test_template_manager_import_maps_fields_and_assigns_product(application, monkeypatch, tmp_path):
    import zipfile
    import src.template_manager as manager_module
    from src import label_templates as templates

    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"name": "Arros", "sku": "001", "weight": 1}], "test.csv")
    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/></label>')
    monkeypatch.setattr(manager_module.filedialog, "askopenfilenames", Mock(return_value=[str(path)]))
    dialog = app._show_template_manager()
    dialog._import()
    identity = dialog.templates_tree.selection()[0]
    dialog.object_name.set("Producte")
    dialog.field_name.set("Nom del producte")
    dialog._save_field()
    dialog.notebook.select(1)
    dialog.update()
    dialog.products_tree.selection_set(dialog.products_tree.get_children())
    dialog.product_template.set(next(label for label, value in dialog._template_choices.items() if value == identity))
    dialog._assign()
    dialog.enabled.set(True)
    dialog._set_enabled()
    template = templates.get_product_template(app.conn, "001", "Arros")
    assert template["fields"] == {"Producte": "producte"}
    dialog.destroy()
    app.undo_action()
    assert not templates.load_template_config(app.conn)["enabled"]


def test_template_manager_assigns_actions_and_supports_undo(application, receipt_template):
    from src import label_templates as templates

    app, app_module, printer = application
    identity, backend = receipt_template
    dialog = app._show_template_manager()
    dialog.notebook.select(2)
    dialog.actions_tree.selection_set("recepcio")
    dialog._select_action()
    assert dialog.action_template.get().startswith("Recepció")
    dialog.action_template.set("Cap plantilla")
    dialog._assign_action()
    assert "recepcio" not in templates.load_template_config(app.conn)["actions"]
    assert dialog.actions_tree.item("recepcio", "values")[1] == "Cap plantilla"
    dialog.destroy()
    app.undo_action()
    assert templates.load_template_config(app.conn)["actions"]["recepcio"] == identity
    app.redo_action()
    assert "recepcio" not in templates.load_template_config(app.conn)["actions"]


def test_template_manager_prints_a_test_label_from_packaging_history(application, monkeypatch, tmp_path):
    import zipfile
    import src.app as app_module
    from src import label_templates as templates

    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    add_packaged_product(
        app.conn, "Arros", "PACK-1", "ENTRY-1", "Catalunya", "2099-12-01",
        "PACK-1", 2, 1, app.session_id, [], "001",
    )
    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "label.xml",
            '<label><objectStyle objectName="Producte"/><objectStyle objectName="Lot"/><objectStyle objectName="Data"/></label>',
        )
    identity = templates.import_label_template(app.conn, path)
    templates.configure_label_template(
        app.conn, identity, "Bossa", {"Producte": "producte", "Lot": "lot", "Data": "data_envasat"},
    )
    backend = Mock(return_value={"status": "printed", "message": "OK"})
    monkeypatch.setattr(app_module, "print_template_job", backend)

    manager = app._show_template_manager()
    manager.templates_tree.selection_set(identity)
    manager._select_template()
    test_dialog = manager._test_template()
    test_dialog.print_button.invoke()

    assert backend.call_count == 1
    with open(backend.call_args.args[0], encoding="utf-8") as source:
        job = json.load(source)
    assert job["copies"] == 1
    assert job["values"]["producte"] == "Arros"
    assert job["values"]["lot"] == "PACK-1"
    assert job["values"]["data_envasat"] == list_packaged_products(app.conn)[0]["created_at"][:10]
    assert len(list_packaged_products(app.conn)) == 1
    test_dialog.close_button.invoke()
    manager.destroy()


def test_template_test_image_shows_values_and_uses_same_data_as_history_print(application, monkeypatch, tmp_path):
    import zipfile
    import src.template_manager as manager_module
    from src import label_templates as templates

    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Arros", "weight": 1}], "test.csv")
    add_packaged_product(
        app.conn, "Arros", "PACK-1", "ENTRY-1", "", "2099-12-01",
        "PACK-1", 2, 1, app.session_id, [], None,
    )
    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "label.xml",
            '<label><objectStyle objectName="Producte"/><objectStyle objectName="SKU"/>'
            '<objectStyle objectName="Origen"/><objectStyle objectName="Logo"/></label>',
        )
    identity = templates.import_label_template(app.conn, path)
    templates.configure_label_template(
        app.conn, identity, "Bossa", {"Producte": "producte", "SKU": "sku", "Origen": "origen"},
    )
    templates.assign_product_template(app.conn, [{"sku": "", "name": "Arros"}], identity)
    templates.set_templates_enabled(app.conn, True)
    backend = Mock(return_value={"status": "printed"})
    monkeypatch.setattr(app_module, "print_template_job", backend)
    export = Mock(return_value={"status": "saved", "message": "Desat"})
    monkeypatch.setattr(manager_module, "export_template_job_image", export)
    monkeypatch.setattr(manager_module.filedialog, "asksaveasfilename", Mock(return_value=str(tmp_path / "preview.png")))

    manager = app._show_template_manager()
    manager.templates_tree.selection_set(identity)
    manager._select_template()
    dialog = manager._test_template()
    displayed = {values[0]: values[1:] for values in (
        dialog.values_tree.item(row, "values") for row in dialog.values_tree.get_children()
    )}
    assert displayed["SKU"][1] == "(buit)"
    assert displayed["Origen"][1] == "(buit)"
    assert displayed["Producte"][1] == "Arros"
    assert displayed["Logo"][1] == "(fix a la plantilla)"

    dialog.image_button.invoke()
    assert export.call_count == 1
    with open(export.call_args.args[0], encoding="utf-8") as source:
        image_job = json.load(source)
    assert export.call_args.args[1] == str(tmp_path / "preview.png")
    assert backend.call_count == 0

    app._print_packaged_history_label(dict(list_packaged_products(app.conn)[0]))
    with open(backend.call_args.args[0], encoding="utf-8") as source:
        printed_job = json.load(source)
    assert image_job["values"] == printed_job["values"]
    assert image_job["values"]["sku"] == ""
    dialog.close_button.invoke()
    manager.destroy()


def test_print_packaged_label_without_registering_envasat(application):
    app, app_module, printer = application
    app.package_ingredients = [{
        "name": "Farina", "supplier_lot": "ENTRY-1", "provenance": "Catalunya", "expiry": "2099-12-01",
    }]
    app.package_final_product = {"name": "Farina envasada", "sku": None}
    app.package_quantity_entry.insert(0, "4")
    history_before = len(app.history.entries)

    app.package_print_button.invoke()

    assert app.conn.execute("SELECT COUNT(*) FROM packaged_products").fetchone()[0] == 0
    assert app.conn.execute("SELECT COUNT(*) FROM prepared_mixtures").fetchone()[0] == 0
    assert len(app.history.entries) == history_before
    assert printer.call_count == 1
    assert "sense registrar l'envasat" in app.package_status.get()


def test_packaged_history_has_per_row_print_button_on_left(application):
    from PIL import Image

    app, app_module, printer = application
    product_id = add_packaged_product(
        app.conn, "Farina", "PACK-1", "ENTRY-1", "Catalunya", "2099-12-01",
        "PACK-1", 2, 1, app.session_id, [], "001",
    )
    app.notebook.select(app.envasat_tab)
    app.populate_package_history()
    app.update()

    button = app._package_print_buttons[str(product_id)]
    bounds = app.package_history_tree.bbox(str(product_id), "Etiqueta")
    assert button.winfo_ismapped()
    assert bounds and button.winfo_x() == bounds[0] + (bounds[2] - button.winfo_width()) // 2
    button.invoke()

    assert printer.call_count == 1
    with Image.open(printer.call_args.args[0]) as image:
        assert json.loads(image.info["erola.label.v1"])["label_type"] == "packaged"
    assert len(list_packaged_products(app.conn)) == 1


@pytest.mark.parametrize("reprint", [False, True])
def test_receipts_use_action_template_with_all_receipt_values(application, receipt_template, reprint):
    from src import label_templates as templates
    from src.core import CSV_EXTRA_FIELDS

    app, app_module, printer = application
    identity, backend = receipt_template
    metadata = {field: f"valor {index}" for index, field in enumerate(CSV_EXTRA_FIELDS)}
    upsert_catalog_rows(
        app.conn, [{"sku": "001", "name": "Farina", "weight": 1, **metadata}], "test.csv"
    )
    template_fields = ("producte", "sku", "lot", "origen", "caducitat", "caducitat_dmy",
                       "codi_barres", "proveidor", "data_recepcio", *CSV_EXTRA_FIELDS)
    templates.configure_label_template(app.conn, identity, "Recepció", {
        field: field for field in template_fields
    })
    if reprint:
        material = app._register_received_material("Farina", "Supplier", "ENTRY-1", "Catalunya", "2027-12-01")
        update_raw_material(app.conn, material["id"], "Farina", "Supplier", "ENTRY-1", "Catalunya", "2027-12-01",
                            created_at="2026-10-14 09:00:00")
        material = dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material["id"],)).fetchone())
        app._print_received_material(material)
    else:
        app.raw_name.insert(0, "Farina")
        app.raw_supplier.insert(0, "Supplier")
        app.raw_supplier_lot.insert(0, "ENTRY-1")
        app.raw_provenance.insert(0, "Catalunya")
        app.raw_expiry.insert(0, "01-12-2027")
        app_module.messagebox.showinfo = Mock()
        app.save_received_material()
        material = dict(app.conn.execute("SELECT * FROM raw_materials").fetchone())
    backend.assert_called_once()
    printer.assert_not_called()
    with open(backend.call_args.args[0], encoding="utf-8") as source:
        job = json.load(source)
    assert job["copies"] == 1
    assert job["values"] == {
        "producte": "Farina", "sku": "001", "lot": "ENTRY-1", "origen": "Catalunya",
        "caducitat": "2027-12-01", "caducitat_dmy": "01/12/2027", "codi_barres": material["barcode"],
        "proveidor": "Supplier", "data_recepcio": material["created_at"][:10],
        **metadata,
    }
    assert job["template"]["name"] == "Recepció"
    assert not templates.load_template_config(app.conn)["enabled"]


@pytest.mark.parametrize("entry_point", ["save", "last_field", "advance"])
def test_receipt_saves_and_prints_without_confirmation(application, receipt_template, monkeypatch, entry_point):
    from types import SimpleNamespace

    app, app_module, printer = application
    _, backend = receipt_template
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock())
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Farina", "weight": 1}], "test.csv")
    app.raw_name.insert(0, "Farina")
    app.raw_supplier_lot.insert(0, "ENTRY-1")
    app.raw_expiry.insert(0, "01-12-2027")
    if entry_point == "save":
        app.save_received_material()
    elif entry_point == "last_field":
        assert app._handle_last_received_field(SimpleNamespace(widget=app.raw_expiry)) == "break"
    else:
        assert app._advance_received_field(SimpleNamespace(widget=app.raw_expiry)) == "break"
    assert app.conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0] == 1
    backend.assert_called_once()
    printer.assert_not_called()
    app_module.messagebox.askyesno.assert_not_called()
    app_module.messagebox.showinfo.assert_not_called()
    app_module.messagebox.showwarning.assert_not_called()
    app_module.messagebox.showerror.assert_not_called()
    assert app.raw_name.get() == ""


def test_missing_receipt_template_keeps_receipt_without_generic_printing(application, monkeypatch):
    app, app_module, printer = application
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock())
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Farina", "weight": 1}], "test.csv")
    app.raw_name.insert(0, "Farina")
    app.raw_supplier_lot.insert(0, "ENTRY-1")
    app.raw_expiry.insert(0, "01-12-2027")
    app._handle_last_received_field(None)
    assert app.conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0] == 1
    printer.assert_not_called()
    assert "no té cap plantilla" in app_module.messagebox.showwarning.call_args.args[1]
    app_module.messagebox.askyesno.assert_not_called()
    app_module.messagebox.showinfo.assert_not_called()


def test_receipt_template_with_packaging_only_field_reports_error(application, receipt_template):
    from src import label_templates as templates

    app, app_module, printer = application
    identity, backend = receipt_template
    templates.configure_label_template(app.conn, identity, "Incorrecta", {"Data": "data_envasat"})
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Farina", "weight": 1}], "test.csv")
    material = app._register_received_material("Farina", "Supplier", "ENTRY-1", "", "2027-12-01")
    result = app._print_received_material(material)
    assert result["status"] == "error"
    assert "Data d'envasat" in app_module.messagebox.showwarning.call_args.args[1]
    backend.assert_not_called()
    printer.assert_not_called()


def test_missing_template_does_not_print_generic_or_lose_packaging(application):
    from src import label_templates as templates

    app, app_module, printer = application
    templates.set_templates_enabled(app.conn, True)
    app.package_ingredients = [{"name": "Arros", "sku": "001", "supplier_lot": "ENTRY-1", "provenance": "", "expiry": "2099-12-31"}]
    app.package_final_product = {"name": "Arros", "sku": "001"}
    app.package_quantity_entry.insert(0, "2")
    app._finalize_packaging_impl()
    assert len(list_packaged_products(app.conn)) == 1
    printer.assert_not_called()
    assert "no té cap plantilla" in app_module.messagebox.showwarning.call_args.args[1]


def test_label_print_menu_persists_printer_and_dimensions(application, monkeypatch, tmp_path):
    from src import printing

    app, app_module, printer = application
    monkeypatch.setattr(printing, "SETTINGS_PATH", tmp_path / "printing.json")
    monkeypatch.setattr(app_module, "list_printer_choices", Mock(return_value=[
        ("Brother QL-600 (USB001)", "Brother QL-600 (Copiar 1)"),
        ("Microsoft Print to PDF (PORTPROMPT:)", "Microsoft Print to PDF"),
    ]))
    monkeypatch.setattr(app_module, "get_default_printer", Mock(return_value="Brother QL-600 (Copiar 1)"))
    preferences = Mock()
    monkeypatch.setattr(app_module, "printer_preferences", preferences)
    app._show_printer_settings()
    dialog = app.grab_current()
    combos = [child for frame in dialog.winfo_children() if isinstance(frame, app_module.ttk.LabelFrame)
              for child in frame.winfo_children() if isinstance(child, app_module.ttk.Combobox)]
    assert len(combos) == 2
    assert combos[1].get() == "Selecciona una impressora..."
    combos[0].set("Brother QL-600 (USB001)")
    combos[1].set("Microsoft Print to PDF (PORTPROMPT:)")
    preference_buttons = [child for frame in dialog.winfo_children() if isinstance(frame, app_module.ttk.LabelFrame)
                          for child in frame.winfo_children() if isinstance(child, app_module.ttk.Button)]
    preference_buttons[0].invoke()
    preference_buttons[1].invoke()
    assert preferences.call_args_list[0].args == ("Brother QL-600 (Copiar 1)", dialog.winfo_id())
    assert preferences.call_args_list[1].args == ("Microsoft Print to PDF", dialog.winfo_id())
    inputs = [child for frame in dialog.winfo_children() if isinstance(frame, app_module.ttk.LabelFrame)
              for child in frame.winfo_children() if isinstance(child, app_module.ttk.Spinbox)]
    for entry, value in zip(inputs, ("80", "50")):
        entry.delete(0, "end")
        entry.insert(0, value)
    buttons = next(child for child in dialog.winfo_children() if isinstance(child, app_module.tk.Frame))
    next(child for child in buttons.winfo_children() if child.cget("text") == "Desar").invoke()
    assert app.printer_name == "Brother QL-600 (Copiar 1)"
    assert app.document_printer == "Microsoft Print to PDF"
    assert printing.load_label_settings() == {
        "label_printer": "Brother QL-600 (Copiar 1)", "document_printer": "Microsoft Print to PDF",
        "label_roll_width_mm": 80, "label_roll_length_mm": 50,
    }
    printer.reset_mock()
    app._print_saved_labels([str(tmp_path / "sessio.pdf")])
    printer.assert_called_once_with(
        str(tmp_path / "sessio.pdf"), None, document_printer="Microsoft Print to PDF",
    )
    app._open_database(str(tmp_path / "other.db"))
    assert app.printer_name == "Brother QL-600 (Copiar 1)"
    assert app.document_printer == "Microsoft Print to PDF"


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
    monkeypatch.setattr(app, "_show_label_saved_notice", Mock())
    monkeypatch.setattr(app, "_choose_catalog_product", Mock(side_effect=lambda text, parent=None: {"name": text, "sku": None}))
    try:
        yield app, app_module, printer
    finally:
        app.conn.close()
        app.destroy()


@pytest.fixture
def receipt_template(application, tmp_path, monkeypatch):
    import zipfile
    from src import label_templates as templates

    app, app_module, printer = application
    path = tmp_path / "Recepcio.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/><objectStyle objectName="Codi"/></label>')
    identity = templates.import_label_template(app.conn, path)
    templates.configure_label_template(app.conn, identity, "Recepció", {"Producte": "producte", "Codi": "codi_barres"})
    templates.assign_action_template(app.conn, "recepcio", identity)
    backend = Mock(return_value={"status": "printed", "message": "OK"})
    monkeypatch.setattr(app_module, "print_template_job", backend)
    return identity, backend


@pytest.mark.parametrize("size", ["1000x550", "1000x650", "1200x760"])
@pytest.mark.parametrize("key", ["Down", "Tab"])
def test_package_recipe_keyboard_selection_and_full_height(application, size, key):
    app, app_module, printer = application
    first = upsert_recipe(app.conn, "001", "Barreja A", [{"name": "Farina", "sku": "010", "quantity": 2}])
    second = upsert_recipe(app.conn, "002", "Barreja B", [{"name": "Oli", "sku": "020", "quantity": 1}])
    app._populate_package_recipe_lookup()
    app.geometry(size)
    app.notebook.select(app.envasat_tab)
    app.update()
    entry = app.package_recipe_search_entry
    tree = app.package_recipe_lookup_tree
    entry.focus_force()
    app.update()
    entry.event_generate(f"<{key}>")
    entry.event_generate(f"<KeyRelease-{key}>")
    app.update()
    assert tree.selection() == (str(first),)
    assert app.focus_get() is tree
    assert app.package_viewing_recipe["id"] == first
    ingredients = app.package_recipe_ingredients_tree
    assert ingredients.item(ingredients.get_children()[0], "values")[1] == "Farina"
    tree.event_generate("<Down>")
    app.update()
    assert tree.selection() == (str(second),)
    assert app.package_viewing_recipe["id"] == second
    assert ingredients.item(ingredients.get_children()[0], "values")[1] == "Oli"
    app._refresh_package_recipe_matching()
    app.update()
    assert tree.selection() == (str(second),)
    lookup_title = next(child for child in entry.master.winfo_children()
                        if isinstance(child, app_module.tk.Label))
    ingredient_title = next(child for child in app.package_form_container.winfo_children()
                            if isinstance(child, app_module.tk.Label)
                            and child.cget("text") == "Ingredient (codi de barres / nom / SKU)")
    assert abs(lookup_title.winfo_rooty() - ingredient_title.winfo_rooty()) <= 2
    assert abs(entry.winfo_rooty() - app.package_ingredient_entry.winfo_rooty()) <= 2
    assert ingredients.winfo_rooty() == ingredients.master.winfo_rooty()
    assert ingredients.winfo_height() >= ingredients.master.winfo_height() - 25
    assert tree.winfo_rooty() + tree.winfo_height() >= tree.master.winfo_rooty() + tree.master.winfo_height() - 25
    assert not any(isinstance(child, app_module.tk.Label) for child in ingredients.master.winfo_children())
    app.package_recipe_close_button.invoke()
    app.update()
    assert not tree.selection()
    assert not ingredients.get_children()
    assert app.package_viewing_recipe is None


def test_requested_instruction_labels_are_removed(application):
    app, app_module, printer = application

    def labels(widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, app_module.tk.Label):
                result.append(child)
            result.extend(labels(child))
        return result

    reception_labels = labels(app.recepcion_tab)
    assert not any(label.cget("text").startswith("Format de caducitat:") for label in reception_labels)
    assert not any(str(label.cget("textvariable")) == str(app.raw_name_sku_var) for label in reception_labels)
    assert not app.package_status.get()
    assert not any(label.cget("text").startswith("Clica una cel·la i escriu") for label in labels(app.recipes_tab))
    app._refresh_package_final_product_state()
    assert not app.package_status.get()


@pytest.mark.parametrize("choice", ["suggestion", "create", "cancel"])
def test_unknown_product_dialog_updates_name_or_creates(application, monkeypatch, choice):
    import tkinter as tk
    from types import SimpleNamespace

    app, app_module, printer = application
    app_module.create_catalog_product(app.conn, "001", "Farina", "1", "Ingredients")
    monkeypatch.setattr(app, "_choose_catalog_product", app_module.TraceabilityApp._choose_catalog_product.__get__(app))
    app.raw_name.insert(0, "Frinaa")
    failures = []

    def interact():
        dialog = app.grab_current()
        try:
            assert isinstance(dialog, tk.Toplevel)
            children = dialog.winfo_children()
            tree = next(child for child in children if isinstance(child, app_module.ttk.Treeview))
            assert tree.item("0", "values")[0] == "Farina"
            assert tree.winfo_ismapped()
            frames = [child for child in children if isinstance(child, app_module.ttk.Frame)]
            buttons = {child.cget("text"): child for frame in frames for child in frame.winfo_children()
                       if isinstance(child, app_module.ttk.Button)}
            if choice == "suggestion":
                bounds = tree.bbox("0")
                for timestamp in (1000, 1150):
                    tree.event_generate("<ButtonPress-1>", x=bounds[0] + 30, y=bounds[1] + bounds[3] // 2, time=timestamp)
                    if tree.winfo_exists():
                        tree.event_generate("<ButtonRelease-1>", x=bounds[0] + 30, y=bounds[1] + bounds[3] // 2, time=timestamp + 30)
            elif choice == "create":
                panel = next(frame for frame in frames if "name" in frame.children)
                assert panel.children["name"].get() == "Frinaa"
                assert panel.children["sku"].get() == ""
                buttons["Crear producte"].invoke()
            else:
                buttons["Cancel·lar"].invoke()
        except BaseException as exc:
            failures.append(exc)
        finally:
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()

    app.after(80, interact)
    app._advance_received_field(SimpleNamespace(widget=app.raw_name))
    assert not failures, failures
    if choice == "suggestion":
        assert app.raw_name.get() == "Farina"
        assert app.raw_name_sku_var.get() == "SKU: 001"
    else:
        assert app.raw_name.get() == "Frinaa"
    row = app_module.get_catalog_row_by_name(app.conn, "Frinaa")
    if choice == "create":
        assert row is not None
        assert row["weight"] is None and row["sku"] == "" and row["category"] == ""
        app.undo_action()
        assert app_module.get_catalog_row_by_name(app.conn, "Frinaa") is None
    else:
        assert row is None


def test_reference_editor_merges_and_undoes_loaded_ingredients(application, monkeypatch):
    app, app_module, printer = application
    add_raw_material(app.conn, "Farina", "Supplier", "LOT-1", "España", "2027-12-01", "RAW-1")
    add_raw_material(app.conn, "Oli", "Supplier", "LOT-2", "Espanya", "2027-12-01", "RAW-2")
    app.package_ingredients = [{"name": "Farina", "provenance": "España", "supplier": "Supplier",
                                "supplier_lot": "LOT-1", "expiry": "2027-12-01", "barcode": "RAW-1",
                                "ingredients": [{"name": "Oli", "provenance": "España"}]}]
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock(return_value=True))
    app._show_reference_data()
    dialog = app.grab_current()
    notebook = next(child for child in dialog.winfo_children() if isinstance(child, app_module.ttk.Notebook))
    panel = notebook.nametowidget(notebook.tabs()[1])
    notebook.select(panel)
    app.update()
    tree = next(child for child in panel.winfo_children() if isinstance(child, app_module.ttk.Treeview))
    iid = next(iid for iid in tree.get_children() if tree.item(iid, "values")[0] == "España")
    tree.selection_set(iid)
    app.update()
    edit_row = next(child for child in panel.winfo_children() if isinstance(child, app_module.ttk.Frame))
    entry = edit_row.children["provenance"]
    assert entry.get() == "España"
    entry.delete(0, "end")
    entry.insert(0, "Espanya")
    next(child for child in edit_row.winfo_children() if isinstance(child, app_module.ttk.Button)).invoke()
    assert app_module.list_reference_values(app.conn, "provenance") == ["Espanya"]
    assert app.package_ingredients[0]["provenance"] == "Espanya"
    assert app.package_ingredients[0]["ingredients"][0]["provenance"] == "Espanya"
    assert len(tree.get_children()) == 1
    dialog.destroy()
    app.undo_action()
    assert app.package_ingredients[0]["provenance"] == "España"
    assert set(app_module.list_reference_values(app.conn, "provenance")) == {"España", "Espanya"}


def test_printer_alert_checkbox_silences_until_new_session(application, monkeypatch, tmp_path):
    app, app_module, printer = application
    real_notice = app_module.TraceabilityApp._show_label_saved_notice.__get__(app)
    monkeypatch.setattr(app, "_show_label_saved_notice", real_notice)
    printer.return_value = {"status": "error", "message": "No printer"}
    path = str(tmp_path / "label.png")
    app._print_saved_labels([path])
    dialog = app.grab_current()
    checkbox = next(child for child in dialog.winfo_children() if isinstance(child, app_module.ttk.Checkbutton))
    checkbox.invoke()
    next(child for child in dialog.winfo_children() if isinstance(child, app_module.ttk.Button)).invoke()
    app._print_saved_labels([path])
    assert printer.call_count == 2
    assert app.grab_current() is None
    app._open_database(str(tmp_path / "next.db"))
    assert not app.suppress_printer_alerts.get()
    app._print_saved_labels([path])
    assert app.grab_current() is not None
    app.grab_current().destroy()


def test_name_only_catalog_product_can_be_received(application):
    app, app_module, printer = application
    app_module.create_catalog_product(app.conn, "", "Nou producte")
    first = app._register_received_material("Nou producte", "", "LOT-1", "", "")
    second = app._register_received_material("Nou producte", "", "LOT-2", "", "")
    assert first["barcode"] != second["barcode"]
    assert first["name"] == "Nou producte"


@pytest.mark.parametrize("width", [1000, 1200])
def test_catalog_export_button_exports_all_products(application, monkeypatch, tmp_path, width):
    import csv
    app, app_module, printer = application
    app_module.create_catalog_product(app.conn, "001", "Farina", "1", "Ingredients")
    app_module.create_catalog_product(app.conn, "", "Nou")
    row = app_module.get_catalog_row_by_sku(app.conn, "001")
    app_module.update_catalog_field(app.conn, row["id"], "weight", 3)
    app.refresh_product_catalog_table()
    app.product_search_var.set("Farina")
    app.geometry(f"{width}x650")
    app.notebook.select(app.csv_tab)
    app.product_edit_summary_var.set("1 SKU, 4 Noms i 6 Pesos modificats")
    app.update()
    actions = app.catalog_export_button.master
    search_row = app.product_search_entry.master
    assert actions.master is search_row
    assert actions.grid_info()["row"] == 0
    assert actions.grid_info()["column"] == 6
    summary = next(child for child in search_row.winfo_children()
                   if isinstance(child, app_module.tk.Label)
                   and str(child.cget("textvariable")) == str(app.product_edit_summary_var))
    assert summary.winfo_rootx() + summary.winfo_width() <= actions.winfo_rootx()
    assert actions.winfo_rootx() + actions.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    assert all(child.winfo_ismapped() for child in actions.winfo_children())
    path = tmp_path / "export.csv"
    monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", Mock(return_value=str(path)))
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    app.catalog_export_button.invoke()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["Pes"] == "3.0"
    assert rows[1]["Nom"] == "Nou"


def test_numeric_enter_matches_return(application):
    import tkinter as tk
    from types import SimpleNamespace

    app, app_module, printer = application

    def press(widget):
        assert widget.bindtags()[0] == "NumericEnter"
        if app.tk.call("tk", "windowingsystem") == "win32":
            app._numeric_enter(SimpleNamespace(widget=widget, type=tk.EventType.KeyPress, state=0))
        else:
            widget.event_generate("<KP_Enter>")

    app.update()
    called = []
    app.raw_name.bind("<Return>", lambda event: called.append(event.keysym))
    app.raw_name.focus_force()
    app.update()
    press(app.raw_name)
    app.update()
    assert called == ["Return"]
    app.notebook.select(app.recipes_tab)
    app.update()
    tree = app.recipe_ingredients_tree
    tree.focus_force()
    app.update()
    row = tree.get_children()[0]
    app._set_recipe_ing_active_cell(row, "SKU")
    press(tree)
    app.update()
    assert app._recipe_ing_active_cell == (row, "Nom")


def test_receipt_reprint_and_editor(application, monkeypatch, receipt_template):
    import tkinter as tk
    from types import SimpleNamespace

    app, app_module, printer = application
    _, backend = receipt_template
    material_id = add_raw_material(app.conn, "Farina", "Supplier", "ORIGIN-1", "Origen", "2027-12-01", "RAW-1")
    app.refresh_materials()
    app.update()
    app.received_tree.xview_moveto(1)
    app.update()
    button = app._received_print_buttons[str(material_id)]
    assert button.winfo_ismapped()
    before = dict(app.conn.execute("SELECT * FROM raw_materials WHERE id = ?", (material_id,)).fetchone())
    button.invoke()
    assert backend.call_count == 1
    with open(backend.call_args.args[0], encoding="utf-8") as source:
        assert json.load(source)["values"]["codi_barres"] == "RAW-1"
    printer.assert_not_called()
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
    assert backend.call_count == 2
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
    label_factory = Mock(wraps=app_module.create_template_label_job)
    monkeypatch.setattr(app_module, "create_template_label_job", label_factory)
    buttons["Imprimir"].invoke()
    assert label_factory.call_args.args[1]["codi_barres"] == "RAW-EDITED"
    assert label_factory.call_args.args[1]["data_recepcio"] == "2026-10-14"
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
    backend.side_effect = RuntimeError("Printer unavailable")
    app._received_print_buttons[str(material_id)].invoke()
    assert app_module.messagebox.showwarning.call_args.args[0] == "Plantilla no impresa"
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
def test_receipt_print_reports_returned_failure(application, receipt_template, status):
    app, app_module, printer = application
    _, backend = receipt_template
    backend.return_value = {"status": status, "message": "Printer unavailable"}
    app._print_received_material({"name": "Farina", "supplier": "Supplier", "supplier_lot": "LOT-1",
                                  "provenance": "", "expiry": "2027-12-01", "barcode": "RAW-1", "created_at": "2026-10-14 09:00:00"})
    app_module.messagebox.showwarning.assert_called_once_with("Plantilla no impresa", "Printer unavailable", parent=app)
    printer.assert_not_called()


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


@pytest.mark.parametrize("size", ["1000x550", "1000x650", "1200x760"])
def test_history_heights_can_be_adjusted(application, size):
    app, app_module, printer = application
    app.geometry(size)
    for tab, panes, tree in (
        (app.envasat_tab, app.package_vertical_panes, app.package_history_tree),
        (app.recipes_tab, app.recipes_vertical_panes, app.prepared_mixture_tree),
    ):
        app.notebook.select(tab)
        app.update()
        assert app.winfo_height() == int(size.split("x")[1])
        assert tree.winfo_ismapped()
        assert tree.winfo_height() >= 50
        assert tree.winfo_rooty() + tree.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        initial_height = tree.winfo_height()
        sash_x, sash_y = panes.sash_coord(0)
        top, bottom = [app.nametowidget(name) for name in panes.panes()]
        shrink = top.winfo_height() - int(panes.panecget(top, "minsize"))
        grow = bottom.winfo_height() - int(panes.panecget(bottom, "minsize"))
        delta = -min(50, max(0, shrink)) if shrink > 0 else min(50, max(0, grow))
        panes.sash_place(0, sash_x, sash_y + delta)
        app.update()
        assert abs(tree.winfo_height() - (initial_height - delta)) <= 2
        panes.sash_place(0, sash_x, sash_y)
        app.update()
        assert abs(tree.winfo_height() - initial_height) <= 2


@pytest.mark.parametrize("size", ["1000x550", "1000x650", "1200x760"])
def test_packaging_ingredients_fill_space_above_quantity(application, size):
    app, app_module, printer = application
    app.geometry(size)
    app.notebook.select(app.envasat_tab)
    app.update()
    panel = app.package_form_container.master
    quantity_row = app.package_quantity_entry.master
    assert not any(
        isinstance(child, app_module.tk.Label)
        and child.cget("text").startswith("Intro afegeix l'ingredient")
        for child in app.package_form_container.winfo_children()
    )
    assert panel.winfo_rooty() + panel.winfo_height() - (quantity_row.winfo_rooty() + quantity_row.winfo_height()) <= 4
    panes = app.package_vertical_panes
    bottom = app.nametowidget(panes.panes()[1])
    available = bottom.winfo_height() - int(panes.panecget(bottom, "minsize"))
    delta = min(40, max(0, available))
    initial_height = app.ingredients_list.winfo_height()
    sash_x, sash_y = panes.sash_coord(0)
    panes.sash_place(0, sash_x, sash_y + delta)
    app.update()
    assert abs(app.ingredients_list.winfo_height() - initial_height - delta) <= 2
    assert panel.winfo_rooty() + panel.winfo_height() - (quantity_row.winfo_rooty() + quantity_row.winfo_height()) <= 4
    status = app.package_status.get()
    app.package_status.set("Producte final seleccionat: Farina.")
    app.update()
    assert app.package_status.get() != status
    assert abs(app.ingredients_list.winfo_height() - initial_height - delta) <= 2
    assert panel.winfo_rooty() + panel.winfo_height() - (quantity_row.winfo_rooty() + quantity_row.winfo_height()) <= 4


@pytest.mark.parametrize("size", ["1000x550", "1000x650", "1200x760"])
def test_packaging_quantity_label_entry_and_clear_button_share_row(application, size):
    app, _, _ = application
    app.geometry(size)
    app.notebook.select(app.envasat_tab)
    app.update()

    controls = (
        app.package_quantity_label,
        app.package_quantity_entry,
        app.package_clear_button,
    )
    assert len({control.master for control in controls}) == 1
    centers = {control.winfo_rooty() + control.winfo_height() / 2 for control in controls}
    assert max(centers) - min(centers) <= 2
    assert app.package_quantity_entry.winfo_rootx() > app.package_quantity_label.winfo_rootx()
    assert app.package_clear_button.winfo_rootx() > app.package_quantity_entry.winfo_rootx()


@pytest.mark.parametrize("size", ["1000x550", "1000x650", "1200x760"])
def test_packaging_minimum_height_keeps_three_ingredients_visible(application, size):
    app, app_module, printer = application
    app.geometry(size)
    app.notebook.select(app.envasat_tab)
    for index in range(6):
        app.ingredients_list.insert("end", f"Ingredient {index + 1}")
    app.update()
    panes = app.package_vertical_panes
    sash_x, sash_y = panes.sash_coord(0)
    panes.sash_place(0, sash_x, 0)
    app.update()
    ingredients = app.ingredients_list
    for index in range(3):
        bounds = ingredients.bbox(index)
        assert bounds is not None
        assert bounds[1] + bounds[3] <= ingredients.winfo_height() - int(ingredients.cget("borderwidth"))
    top = app.package_top_panes.master
    for entry in (app.package_final_product_entry, app.package_quantity_entry):
        assert entry.winfo_ismapped()
        assert entry.winfo_rooty() + entry.winfo_height() <= top.winfo_rooty() + top.winfo_height()
    assert app.package_history_tree.winfo_height() >= 50


@pytest.mark.parametrize("offset", [-6, 1, 7])
def test_pane_drag_defers_content_resize_until_release(application, offset):
    app, app_module, printer = application
    app.geometry("1200x760")
    for tab, panes in (
        (app.envasat_tab, app.package_vertical_panes),
        (app.envasat_tab, app.package_top_panes),
        (app.envasat_tab, app.package_recipe_panes),
        (app.recipes_tab, app.recipes_vertical_panes),
    ):
        app.notebook.select(tab)
        app.update()
        assert not panes.cget("opaqueresize")
        assert int(panes.cget("sashwidth")) == 2
        assert int(panes.cget("sashpad")) == 6
        assert int(panes.cget("borderwidth")) == 0
        assert str(panes.cget("sashrelief")) == "flat"
        assert panes.winfo_rgb(panes.cget("background")) != panes.winfo_rgb("#999999")
        line = panes._sash_lines[0]
        assert panes.winfo_rgb(line.cget("background")) == panes.winfo_rgb(panes.cget("background"))
        horizontal = str(panes.cget("orient")) == "horizontal"
        assert (line.winfo_width() if horizontal else line.winfo_height()) == 2
        assert int(panes.cget("proxyborderwidth")) == 0
        assert str(panes.cget("proxyrelief")) == "flat"
        assert panes.winfo_rgb(panes.cget("proxybackground")) == panes.winfo_rgb(panes.cget("background"))
        children = [app.nametowidget(name) for name in panes.panes()]
        before = [(child.winfo_width(), child.winfo_height()) for child in children]
        sash_x, sash_y = panes.sash_coord(0)
        start_x = sash_x + offset if horizontal else sash_x + 20
        start_y = sash_y + 20 if horizontal else sash_y + offset
        assert panes.identify(start_x, start_y) == (0, "sash")
        panes.event_generate("<Motion>", x=start_x, y=start_y)
        assert panes.cget("cursor") == ("sb_h_double_arrow" if horizontal else "sb_v_double_arrow")
        target_x = start_x - 20 if str(panes.cget("orient")) == "horizontal" else start_x
        target_y = start_y - 20 if str(panes.cget("orient")) == "vertical" else start_y
        receiver = line if offset == 1 else panes
        origin_x = receiver.winfo_rootx() - panes.winfo_rootx()
        origin_y = receiver.winfo_rooty() - panes.winfo_rooty()
        receiver.event_generate("<ButtonPress-1>", x=start_x - origin_x, y=start_y - origin_y)
        receiver.event_generate("<B1-Motion>", x=target_x - origin_x, y=target_y - origin_y)
        app.update()
        assert [(child.winfo_width(), child.winfo_height()) for child in children] == before
        receiver.event_generate("<ButtonRelease-1>", x=target_x - origin_x, y=target_y - origin_y)
        app.update()
        assert [(child.winfo_width(), child.winfo_height()) for child in children] != before
        sash_x, sash_y = panes.sash_coord(0)
        assert (line.winfo_x() if horizontal else line.winfo_y()) == (sash_x if horizontal else sash_y)


@pytest.mark.parametrize("prepared", [False, True])
def test_packaged_weight_reaches_labels_and_histories(application, monkeypatch, tmp_path, prepared):
    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Nom actual", "weight": 0.25}], source_file="test")
    ingredient = {"name": "Nom antic", "sku": "001", "supplier": "Supplier", "supplier_lot": "ENTRY-1",
                  "provenance": "Catalunya", "expiry": "2027-12-01", "barcode": "RAW-1"}
    factory = Mock(wraps=app_module.create_packaged_label_image)
    monkeypatch.setattr(app_module, "create_packaged_label_image", factory)
    if prepared:
        mixture = add_prepared_mixture(app.conn, "Nom antic", "001", "LOT-1", "2027-12-01", [ingredient])
        app._record_prepared_mixture_packaging(mixture, 2)
    else:
        app._add_package_ingredient(ingredient)
        app.package_quantity_entry.insert(0, "2")
        app._finalize_packaging()
    packaged = app.conn.execute("SELECT * FROM packaged_products").fetchone()
    assert packaged["weight"] == 0.25
    assert factory.call_count == 2
    assert all(call.kwargs["weight"] == 0.25 for call in factory.call_args_list)
    app.conn.execute("UPDATE product_catalog SET weight = 99")
    app.conn.commit()
    app.populate_package_history()
    app.refresh_history()
    assert app.package_history_tree.set(str(packaged["id"]), "Pes (kg)") == "0.25"
    assert app.history_tree.set(app.history_tree.get_children()[0], "Pes (kg)") == "0.25"
    target = tmp_path / "session.csv"
    export_session_csv(app.conn, app.session_id, str(target))
    with target.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["pes_kg"] == "0.25"


def test_saved_label_notice_has_clickable_directory_and_restores_editor(application, monkeypatch, tmp_path):
    import tkinter as tk

    app, app_module, printer = application
    material_id = add_raw_material(app.conn, "Farina", "Supplier", "LOT-1", "", "2027-12-01", "RAW-1")
    app._open_received_editor(material_id)
    editor = app.grab_current()
    assert editor.nametowidget("name").winfo_ismapped()
    opened = Mock()
    monkeypatch.setattr(app, "_open_label_directory", opened)
    app_module.TraceabilityApp._show_label_saved_notice(app, str(tmp_path / "etiqueta.png"), parent=editor)
    notice = app.grab_current()
    labels = [child for child in notice.winfo_children() if isinstance(child, tk.Label)]
    assert labels[0].cget("text") == "No s'ha trobat impressora, etiqueta guardada a"
    assert labels[1].cget("text") == str(tmp_path.resolve())
    labels[1].event_generate("<Button-1>")
    app.update()
    opened.assert_called_once_with(tmp_path.resolve(), parent=notice)
    button = next(child for child in notice.winfo_children() if isinstance(child, app_module.ttk.Button))
    button.invoke()
    assert app.grab_current() is editor
    editor.destroy()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_label_directory_opens_system_file_manager(application, monkeypatch, tmp_path, platform):
    app, app_module, printer = application
    startfile = Mock()
    popen = Mock()
    monkeypatch.setattr(app_module.os, "startfile", startfile, raising=False)
    monkeypatch.setattr(app_module.subprocess, "Popen", popen)
    monkeypatch.setattr(app_module.sys, "platform", platform)
    app._open_label_directory(tmp_path)
    if platform == "win32":
        startfile.assert_called_once_with(str(tmp_path))
        popen.assert_not_called()
    else:
        popen.assert_called_once_with(["xdg-open", str(tmp_path)])
        startfile.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_initial_database_name_and_legacy_preservation(tmp_path, monkeypatch, legacy):
    from pathlib import Path
    import tkinter as tk
    import src.app as app_module
    import src.db as db_module

    assert Path(db_module.DB_PATH).name == "Registre 1.db"
    new_path = tmp_path / "data" / "Registre 1.db"
    legacy_path = new_path.with_name("trazabilitat.db")
    if legacy:
        conn = initialize_db(str(legacy_path))
        add_raw_material(conn, "Dades existents", "Supplier", "LOT-1", "", "2027-12-01", "RAW-1")
        conn.close()
    monkeypatch.setattr(app_module, "DB_PATH", str(new_path))
    try:
        app = app_module.TraceabilityApp()
    except tk.TclError as exc:
        pytest.skip(f"Tkinter necessita una pantalla: {exc}")
    try:
        assert Path(app.db_path) == (legacy_path if legacy else new_path)
        assert app.conn.execute("SELECT COUNT(*) FROM raw_materials").fetchone()[0] == int(legacy)
        assert new_path.exists() == (not legacy)
    finally:
        app.conn.close()
        app.destroy()


@pytest.mark.parametrize("status", ["printed", "skipped", "error", "exception"])
def test_prepared_history_prints_production_sheet_without_changing_records(application, monkeypatch, status):
    app, app_module, printer = application
    mixture = add_prepared_mixture(app.conn, "Infusió", "100", "PREP-1", "2027-12-01", [
        {"name": "Marialluïsa", "sku": "001", "supplier_lot": "ENTRY-1", "expiry": "2027-12-01"},
    ])
    app.notebook.select(app.recipes_tab)
    app.populate_prepared_mixture_history()
    app.update()
    app.prepared_mixture_tree.xview_moveto(1)
    app.update()
    button = app._prepared_print_buttons[str(mixture["id"])]
    assert button.winfo_ismapped()
    assert button.winfo_width() == button.winfo_height()
    button.event_generate("<Enter>")
    assert app._prepared_print_tooltip is not None
    button.event_generate("<Leave>")
    assert app._prepared_print_tooltip is None
    factory = Mock(wraps=app_module.create_mixture_label_image)
    monkeypatch.setattr(app_module, "create_mixture_label_image", factory)
    if status == "exception":
        printer.side_effect = OSError("No printer")
    else:
        printer.return_value = {"status": status, "message": "Printer status"}
    before = app.conn.serialize()
    button.invoke()
    printer.assert_called_once()
    assert factory.call_args.args == (
        mixture["name"], mixture["lot"], mixture["sku"], mixture["expiry"], mixture["barcode"],
        mixture["created_at"], json.loads(mixture["ingredients_json"]),
    )
    with open(printer.call_args.args[0], "rb") as handle:
        assert handle.read(5) == b"%PDF-"
    assert app.conn.serialize() == before
    assert not app.history.can_undo
    app._show_label_saved_notice.assert_not_called()
    assert printer.call_args.args[1] is None
    if status == "printed":
        app_module.messagebox.showwarning.assert_not_called()
    else:
        assert app_module.messagebox.showwarning.call_args.args[0] == "Document no imprès"


@pytest.mark.parametrize("size", ["1000x550", "1200x760"])
def test_prepared_print_buttons_follow_scroll_sort_and_resize(application, size):
    app, app_module, printer = application
    for index in range(15):
        add_prepared_mixture(app.conn, f"Preparació {index:02}", "100", f"PREP-{index}", "2027-12-01", [{"name": "Farina"}])
    app.geometry(size)
    app.notebook.select(app.recipes_tab)
    app.populate_prepared_mixture_history()
    app.update()
    tree = app.prepared_mixture_tree
    tree.xview_moveto(1)
    tree.yview_moveto(1)
    app.update()
    first, last = tree.get_children()[0], tree.get_children()[-1]
    assert not app._prepared_print_buttons[first].winfo_ismapped()
    assert app._prepared_print_buttons[last].winfo_ismapped()
    app._sort_tree_by_column(tree, "Nom")
    tree.see(first)
    app.update()
    bounds = tree.bbox(first, "Full")
    button = app._prepared_print_buttons[first]
    assert button.winfo_ismapped()
    assert button.winfo_y() == bounds[1] + 1
    assert button.winfo_x() + button.winfo_width() <= tree.winfo_width()
    search = app.prepared_mixture_search_entry
    assert search.winfo_rootx() + search.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    assert app.prepared_mixture_quantity_entry.winfo_rooty() + app.prepared_mixture_quantity_entry.winfo_height() <= app.winfo_rooty() + app.winfo_height()


def test_prepared_history_search_suggests_nested_ingredients(application, tmp_path):
    from types import SimpleNamespace

    app, app_module, printer = application
    mixture = add_prepared_mixture(app.conn, "Infusió", "100", "PREP-1", "2027-12-01", [
        {"name": "Base", "ingredients": [{"name": "Marialluïsa", "supplier_lot": "ENTRY-1"}]},
    ])
    add_prepared_mixture(app.conn, "Pa", "101", "PREP-2", "2027-12-01", [{"name": "Farina"}])
    app.notebook.select(app.recipes_tab)
    app.populate_prepared_mixture_history()
    app.update()
    search = app.prepared_mixture_search_entry
    search.insert(0, "Mria Llusa")
    app._on_prepared_mixture_search_keyrelease(SimpleNamespace(keysym="a"))
    assert "Marialluïsa" in app.suggestions_items
    app._on_suggestions_down_from(search)
    app._on_suggestions_accept(None)
    assert search.get() == "Marialluïsa"
    assert app.prepared_mixture_tree.get_children() == (str(mixture["id"]),)
    assert set(app._prepared_print_buttons) == {str(mixture["id"])}
    assert app.suggestions_anchor_entry is None
    app._clear_prepared_mixture_search()
    assert len(app.prepared_mixture_tree.get_children()) == 2
    search.insert(0, "ENTRY-1")
    app._submit_prepared_mixture_search()
    assert app.prepared_mixture_tree.get_children() == (str(mixture["id"]),)
    search.delete(0, "end")
    search.insert(0, "inexistent")
    app._on_prepared_mixture_search_keyrelease()
    assert not app.prepared_mixture_tree.get_children()
    assert not app._prepared_print_buttons
    assert app.suggestions_anchor_entry is None
    app._open_database(str(tmp_path / "other.db"))
    assert search.get() == ""
    printer.assert_not_called()


def test_history_search_shows_packaged_products_by_nested_ingredient(application):
    app, app_module, printer = application
    other_session = create_session(app.conn)
    ingredients = [{"name": "Base", "ingredients": [{"name": "Marialluïsa", "supplier_lot": "ENTRY-1"}]}]
    for session_id, lot in ((app.session_id, "PACK-1"), (other_session, "PACK-2")):
        add_packaged_product(app.conn, "Infusió", lot, "", "", "2027-12-01", lot, 2, 0.25, session_id, ingredients, "100")
    app.notebook.select(app.historial_tab)
    app.search_var.insert(0, "Mria Llusa")
    app._on_history_search_keyrelease()
    assert "Marialluïsa" in app.suggestions_items
    app._on_suggestions_down_from(app.search_var)
    app._on_suggestions_accept(None)
    assert [app.history_tree.item(iid, "values")[0] for iid in app.history_tree.get_children()] == ["Infusió"]
    app.history_all_sessions_check.invoke()
    rows = [app.history_tree.item(iid) for iid in app.history_tree.get_children()]
    assert [row["values"][0] for row in rows if "session_separator" not in row["tags"]] == ["Infusió", "Infusió"]
    assert all("search_match" in app.sessions_tree.item(str(session_id), "tags") for session_id in (app.session_id, other_session))
    printer.assert_not_called()


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


def test_new_receipt_reports_saved_file_without_printer(application, receipt_template, monkeypatch):
    from pathlib import Path
    from src.db import upsert_catalog_rows

    app, app_module, printer = application
    upsert_catalog_rows(app.conn, [{"sku": "001", "name": "Farina", "weight": 1}], source_file="test")
    _, backend = receipt_template
    backend.side_effect = lambda path, name: {"status": "error", "message": f"Printer unavailable\nFeina conservada a: {path}"}
    monkeypatch.setattr(app_module.messagebox, "askyesno", Mock())
    monkeypatch.setattr(app_module.messagebox, "showinfo", Mock())
    app.raw_name.insert(0, "Farina")
    app.raw_supplier.insert(0, "Supplier")
    app.raw_supplier_lot.insert(0, "ENTRY-1")
    app.raw_expiry.insert(0, "01-12-2027")
    app._handle_last_received_field(None)
    backend.assert_called_once()
    printer.assert_not_called()
    assert Path(backend.call_args.args[0]).is_file()
    assert str(backend.call_args.args[0]) in app_module.messagebox.showwarning.call_args.args[1]
    app_module.messagebox.showinfo.assert_not_called()
    app_module.messagebox.askyesno.assert_not_called()
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
    app._show_label_saved_notice.assert_not_called()
    assert app_module.messagebox.showwarning.call_args.args[0] == "Document no imprès"
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
    app._show_label_saved_notice.assert_called_once_with(printer.call_args.args[0], parent=None)
    packed = app.conn.execute("SELECT * FROM packaged_products").fetchone()
    assert packed["quantity"] == 2
    assert packed["prepared_mixture_id"] == mixture["id"]
    assert len(list_prepared_mixtures(app.conn)) == 1

import csv

from src.db import export_session_csv, replace_catalog_rows, upsert_catalog_rows
from src.history import ActionHistory, Snapshot


def test_export_session_csv_includes_quantity_and_weight(tmp_path):
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        add_packaged_product(conn, "Te", "LOT-1", "ENTRADA-1", "Cat", "2027-01-01", "LOT-1", 7, 0.25, 5, [], "1")
        target = tmp_path / "out.csv"
        assert export_session_csv(conn, 5, str(target)) == 1
        with open(target, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        assert rows[0] == ["nom", "lot", "caducitat", "origen", "quantitat", "pes_kg", "lot_entrada"]
        assert rows[1] == ["Te", "LOT-1", "2027-01-01", "Cat", "7", "0.25", "ENTRADA-1"]
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
