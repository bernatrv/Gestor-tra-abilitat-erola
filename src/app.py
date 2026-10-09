from __future__ import annotations

import copy
import json
import os
import sqlite3
import tkinter as tk
import tkinter.font as tkfont
import unicodedata
import zlib
from contextlib import contextmanager
from datetime import date
from tkinter import filedialog, messagebox, ttk

from src.core import (
    compute_expiry_from_ingredients,
    generate_barcode_from_sku,
    generate_daily_sequential_lot,
    generate_packed_lot,
    ingredient_matches_recipe_ingredients,
    parse_csv_mapping,
    parse_date,
    rank_recipes_by_match,
)
from src.csv_import import read_csv_file
from src.history import DISCARDED, UNDONE, ActionHistory, Snapshot
from src.db import (
    DB_PATH,
    add_packaged_product,
    add_prepared_mixture,
    add_raw_material,
    count_barcodes_with_prefix,
    count_packed_lots_for_sku_on_date,
    export_session_csv,
    get_catalog_edit_counts,
    get_catalog_row_by_name,
    get_catalog_row_by_sku,
    get_catalog_sku_by_name,
    get_catalog_weight_by_name,
    get_material_by_barcode,
    get_packaging_ingredient_by_barcode,
    get_recipe_by_id,
    get_recipe_by_sku,
    get_setting,
    initialize_db,
    create_session,
    list_catalog_products,
    list_edited_catalog_products,
    list_packaged_products,
    list_prepared_mixtures,
    list_raw_materials,
    list_recipes,
    list_recipes_with_ingredients,
    revert_all_catalog_edits,
    revert_catalog_row,
    replace_catalog_rows,
    search_catalog_products,
    search_distinct_raw_material_values,
    search_packaged,
    search_packaging_ingredients,
    set_setting,
    update_catalog_field,
    upsert_recipe,
)
from src.label_print import create_label_image, create_mixture_label_image, print_label_file

_CATALOG_COLUMNS = ("SKU", "Nom", "Pes", "Categoria")
_CATALOG_EDITABLE_COLUMNS = ("SKU", "Nom", "Pes")
_CATALOG_FIELD_BY_COLUMN = {"SKU": "sku", "Nom": "name", "Pes": "weight"}


class TraceabilityApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Gestor de traçabilitat Erola")
        self.geometry("1200x760")
        self.minsize(1000, 650)
        self.printer_name = "Epson ColorWorks CW-C4000e"
        self.package_ingredients: list[dict] = []
        self.package_final_product: dict | None = None
        self.package_final_product_auto = True
        self.package_reuse_lot: str | None = None
        self.package_prepared_mixture_id: int | None = None
        self.suggestions_window: tk.Toplevel | None = None
        self.suggestions_listbox: tk.Listbox | None = None
        self.suggestions_items: list = []
        self.suggestions_apply = None
        self.suggestions_anchor_entry: tk.Entry | None = None
        self._package_history_rows: dict = {}
        self._prepared_mixture_rows: dict = {}
        self._package_final_product_catalog_row = None
        self.package_viewing_recipe = None
        self.package_viewing_recipe_ingredients: list = []
        self._package_recipe_lookup_rows: dict = {}
        self._product_search_matches: list[str] = []
        self._product_search_index = -1
        self.current_recipe_id: int | None = None
        self.current_recipe_sku = ""
        self.current_recipe_product_name = ""
        self._recipe_search_matches: list[str] = []
        self._recipe_search_index = -1
        self.conn: sqlite3.Connection | None = None
        self.db_path = DB_PATH
        self.history = ActionHistory()
        self._action_depth = 0
        self._db_snapshot_cache: tuple[int, bytes] | None = None
        self.history_window: tk.Toplevel | None = None
        self.actions_tree: ttk.Treeview | None = None
        self._install_entry_shortcuts()
        self.build_menu()
        self.build_ui()
        self._open_database(DB_PATH)

    def build_menu(self):
        menubar = tk.Menu(self)
        db_menu = tk.Menu(menubar, tearoff=0)
        db_menu.add_command(label="Nova base de dades...", command=self.new_database)
        db_menu.add_command(label="Obrir base de dades...", command=self.open_database)
        db_menu.add_separator()
        db_menu.add_command(label="Desar com...", command=self.save_database_as)
        menubar.add_cascade(label="Base de dades", menu=db_menu)
        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="Desfer", accelerator="Ctrl+Z", command=self.undo_action)
        edit_menu.add_command(label="Refer", accelerator="Ctrl+Y", command=self.redo_action)
        edit_menu.add_separator()
        edit_menu.add_command(label="Historial", command=self.show_history_window)
        menubar.add_cascade(label="Edita", menu=edit_menu)
        self.bind_all("<Control-z>", self._on_undo_shortcut)
        self.bind_all("<Control-y>", self._on_redo_shortcut)
        self.config(menu=menubar)

    def _open_database(self, path: str):
        new_conn = initialize_db(path)
        old_conn = self.conn
        self.conn = new_conn
        self.db_path = path
        self.session_id = create_session(self.conn)
        self.history.clear()
        self._db_snapshot_cache = None
        self._refresh_history_view()
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass
        self.title(f"Gestor de traçabilitat Erola — {os.path.basename(path)}")
        self.refresh_materials()
        self.refresh_history()
        self.populate_package_history()
        self.refresh_product_catalog_table()
        self.populate_recipes_list()
        self.populate_prepared_mixture_history()
        if hasattr(self, "package_recipe_lookup_tree"):
            self._close_package_recipe_view()

    def new_database(self):
        path = filedialog.asksaveasfilename(
            title="Crea una nova base de dades",
            defaultextension=".db",
            filetypes=[("Base de dades SQLite", "*.db"), ("Tots els fitxers", "*.*")],
        )
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
            self._open_database(path)
        except Exception as exc:
            messagebox.showerror("Error creant la base de dades", str(exc))
            return
        messagebox.showinfo("Base de dades creada", f"S'ha creat una nova base de dades a:\n{path}")

    def open_database(self):
        path = filedialog.askopenfilename(
            title="Obre una base de dades",
            filetypes=[("Base de dades SQLite", "*.db"), ("Tots els fitxers", "*.*")],
        )
        if not path:
            return
        try:
            self._open_database(path)
        except Exception as exc:
            messagebox.showerror(
                "Error obrint la base de dades",
                f"No s'ha pogut obrir el fitxer com a base de dades:\n{exc}",
            )
            return
        messagebox.showinfo("Base de dades oberta", f"S'ha obert la base de dades:\n{path}")

    def save_database_as(self):
        initial_dir = os.path.dirname(os.path.abspath(self.db_path)) or "."
        path = filedialog.asksaveasfilename(
            title="Desa la base de dades com",
            defaultextension=".db",
            initialfile=os.path.basename(self.db_path),
            initialdir=initial_dir,
            filetypes=[("Base de dades SQLite", "*.db"), ("Tots els fitxers", "*.*")],
        )
        if not path:
            return
        if os.path.abspath(path) == os.path.abspath(self.db_path):
            self.conn.commit()
            messagebox.showinfo(
                "Base de dades desada",
                "Ja estàs treballant sobre aquest fitxer; els canvis ja estan desats.",
            )
            return
        try:
            self.conn.commit()
            dest_conn = sqlite3.connect(path)
            try:
                self.conn.backup(dest_conn)
            finally:
                dest_conn.close()
            self._open_database(path)
        except Exception as exc:
            messagebox.showerror("Error desant la base de dades", str(exc))
            return
        messagebox.showinfo("Base de dades desada", f"S'ha desat una còpia de la base de dades a:\n{path}")

    def build_ui(self):
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook = notebook

        self.recepcion_tab = ttk.Frame(notebook)
        self.envasat_tab = ttk.Frame(notebook)
        self.csv_tab = ttk.Frame(notebook)
        self.recipes_tab = ttk.Frame(notebook)
        self.historial_tab = ttk.Frame(notebook)

        notebook.add(self.recepcion_tab, text="Recepció")
        notebook.add(self.envasat_tab, text="Envasat")
        notebook.add(self.csv_tab, text="Productes")
        notebook.add(self.recipes_tab, text="Receptes")
        notebook.add(self.historial_tab, text="Historial / exportació")

        self.build_recepcion_tab()
        self.build_envasat_tab()
        self.build_csv_tab()
        self.build_recipes_tab()
        self.build_historial_tab()
        self._bind_tab_shortcuts()

    def _bind_tab_shortcuts(self):
        """F1, F2, F3... canvien directament a la 1a, 2a, 3a... pestanya."""
        for index in range(len(self.notebook.tabs())):
            self.bind_all(f"<F{index + 1}>", lambda event, i=index: self._select_tab_by_index(i))

    def _select_tab_by_index(self, index):
        tabs = self.notebook.tabs()
        if 0 <= index < len(tabs):
            self.notebook.select(tabs[index])

    def _install_entry_shortcuts(self):
        """Dreceres d'edició de text habituals a Windows que Tk no porta per als quadres de text."""
        for widget_class in ("Entry", "TEntry"):
            self.bind_class(widget_class, "<Control-BackSpace>", self._entry_delete_word_back)
            self.bind_class(widget_class, "<Control-Delete>", self._entry_delete_word_forward)
            self.bind_class(widget_class, "<Control-a>", self._entry_select_all)
            self.bind_class(widget_class, "<Control-A>", self._entry_select_all)

    @staticmethod
    def _is_word_char(char: str) -> bool:
        return char.isalnum() or char == "_"

    @staticmethod
    def _entry_is_editable(entry) -> bool:
        return str(entry.cget("state")) == "normal"

    def _entry_delete_word_back(self, event):
        entry = event.widget
        if not self._entry_is_editable(entry):
            return "break"
        if entry.selection_present():
            entry.delete("sel.first", "sel.last")
            return "break"
        text, position = entry.get(), entry.index(tk.INSERT)
        start = position
        while start > 0 and text[start - 1].isspace():
            start -= 1
        if start > 0:
            word = self._is_word_char(text[start - 1])
            while start > 0 and not text[start - 1].isspace() and self._is_word_char(text[start - 1]) == word:
                start -= 1
        entry.delete(start, position)
        return "break"

    def _entry_delete_word_forward(self, event):
        entry = event.widget
        if not self._entry_is_editable(entry):
            return "break"
        if entry.selection_present():
            entry.delete("sel.first", "sel.last")
            return "break"
        text, position = entry.get(), entry.index(tk.INSERT)
        end = position
        if end < len(text) and not text[end].isspace():
            word = self._is_word_char(text[end])
            while end < len(text) and not text[end].isspace() and self._is_word_char(text[end]) == word:
                end += 1
        while end < len(text) and text[end].isspace():
            end += 1
        entry.delete(position, end)
        return "break"

    def _entry_select_all(self, event):
        entry = event.widget
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        return "break"

    def _on_tab_changed(self, event):
        selected = event.widget.select()
        if not selected:
            return
        tab_text = event.widget.tab(selected, "text")
        # Cada pestanya té el seu propi camp "principal": en canviar de pestanya (amb F1..FN o
        # clicant la pestanya) cal moure-hi el focus explícitament, altrament el teclat segueix
        # enviant tecles al quadre de la pestanya anterior (que ja no és visible).
        focus_widget = {
            "Recepció": getattr(self, "raw_name", None),
            "Envasat": getattr(self, "package_ingredient_entry", None),
            "Productes": getattr(self, "product_search_entry", None),
            "Receptes": getattr(self, "recipe_product_entry", None),
            "Historial / exportació": getattr(self, "search_var", None),
        }.get(tab_text)
        if focus_widget is not None:
            focus_widget.focus_set()

    def _attach_scrollbars(self, widget, horizontal: bool = False):
        """Afegeix barres de desplaçament que només es mostren quan el contingut no hi cap."""
        parent = widget.master
        info = widget.grid_info()
        row, column = int(info["row"]), int(info["column"])
        rowspan, columnspan = int(info.get("rowspan", 1)), int(info.get("columnspan", 1))

        def build(orient, command, **grid_options):
            bar = ttk.Scrollbar(parent, orient=orient, command=command)
            bar.grid(**grid_options)

            def on_scroll(first, last):
                bar.set(first, last)
                if float(first) <= 0.0 and float(last) >= 1.0:
                    bar.grid_remove()
                else:
                    bar.grid()

            return on_scroll

        widget.configure(
            yscrollcommand=build(
                "vertical", widget.yview, row=row, column=column + columnspan, rowspan=rowspan,
                sticky="ns", pady=info.get("pady", 0),
            )
        )
        if horizontal:
            widget.configure(
                xscrollcommand=build(
                    "horizontal", widget.xview, row=row + rowspan, column=column, columnspan=columnspan,
                    sticky="ew", padx=info.get("padx", 0),
                )
            )

    def _setup_sortable_tree(self, tree: ttk.Treeview, numeric_columns: tuple[str, ...] = ()):
        """Permet ordenar un Treeview clicant les capçaleres (ascendent/descendent alternant)."""
        tree._sort_state = {"column": None, "reverse": False}
        tree._base_headings = {column: tree.heading(column, "text") for column in tree["columns"]}
        tree._numeric_columns = set(numeric_columns)
        for column in tree["columns"]:
            tree.heading(column, command=lambda c=column, t=tree: self._sort_tree_by_column(t, c))

    @staticmethod
    def _text_sort_key(value) -> str:
        """Clau d'ordenació alfabètica sense majúscules ni accents (perquè 'Èxit' no vagi després de 'Z')."""
        decomposed = unicodedata.normalize("NFD", str(value or "").lower())
        return "".join(char for char in decomposed if not unicodedata.combining(char))

    def _reapply_tree_sort(self, tree: ttk.Treeview):
        """Torna a aplicar l'ordre triat per l'usuari (capçalera) després de repoblar el Treeview."""
        column = tree._sort_state["column"]
        if column is not None:
            self._sort_tree_by_column(tree, column, toggle=False)

    def _sort_tree_by_column(self, tree: ttk.Treeview, column: str, toggle: bool = True):
        state = tree._sort_state
        if not toggle:
            pass
        elif state["column"] == column:
            state["reverse"] = not state["reverse"]
        else:
            state["column"] = column
            state["reverse"] = False
        reverse = state["reverse"]
        is_numeric = column in tree._numeric_columns

        def sort_key(iid):
            value = tree.set(iid, column)
            if is_numeric:
                try:
                    return (0, float(str(value).replace(",", ".")))
                except (ValueError, TypeError):
                    return (1, 0.0)
            return (0, self._text_sort_key(value))

        for index, iid in enumerate(sorted(tree.get_children(""), key=sort_key, reverse=reverse)):
            tree.move(iid, "", index)
        for column_name, base_text in tree._base_headings.items():
            suffix = ""
            if column_name == column:
                suffix = " ▼" if reverse else " ▲"
            tree.heading(column_name, text=base_text + suffix)

    def build_recepcion_tab(self):
        frame = self.recepcion_tab

        row = 0
        tk.Label(frame, text="Nom", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", padx=(10, 4), pady=(12, 8))
        self.raw_name = tk.Entry(frame, width=18)
        self.raw_name.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=(12, 8))

        tk.Label(frame, text="Proveïdor", font=("Segoe UI", 9, "bold")).grid(row=row, column=2, sticky="w", padx=(0, 4), pady=(12, 8))
        self.raw_supplier = tk.Entry(frame, width=18)
        self.raw_supplier.grid(row=row, column=3, sticky="ew", padx=(0, 8), pady=(12, 8))

        tk.Label(frame, text="Lot", font=("Segoe UI", 9, "bold")).grid(row=row, column=4, sticky="w", padx=(0, 4), pady=(12, 8))
        self.raw_supplier_lot = tk.Entry(frame, width=18)
        self.raw_supplier_lot.grid(row=row, column=5, sticky="ew", padx=(0, 8), pady=(12, 8))

        tk.Label(frame, text="Origen", font=("Segoe UI", 9, "bold")).grid(row=row, column=6, sticky="w", padx=(0, 4), pady=(12, 8))
        self.raw_provenance = tk.Entry(frame, width=18)
        self.raw_provenance.grid(row=row, column=7, sticky="ew", padx=(0, 8), pady=(12, 8))

        tk.Label(frame, text="Caducitat", font=("Segoe UI", 9, "bold")).grid(row=row, column=8, sticky="w", padx=(0, 4), pady=(12, 8))
        self.raw_expiry = tk.Entry(frame, width=12)
        self.raw_expiry.grid(row=row, column=9, sticky="ew", padx=(0, 8), pady=(12, 8))

        tk.Button(frame, text="Guardar i etiquetar", command=self.save_received_material, width=22, bg="#2f7ed8", fg="white").grid(row=row, column=10, sticky="e", padx=(0, 10), pady=(12, 8))

        self.raw_name.bind("<Tab>", self._advance_received_field)
        self.raw_name.bind("<KeyRelease>", self._on_raw_name_keyrelease)
        self.raw_name.bind("<Down>", lambda event: self._on_suggestions_down_from(self.raw_name))
        self.raw_supplier.bind("<Tab>", self._advance_received_field)
        self.raw_supplier.bind("<KeyRelease>", self._on_raw_supplier_keyrelease)
        self.raw_supplier.bind("<Down>", lambda event: self._on_suggestions_down_from(self.raw_supplier))
        self.raw_supplier_lot.bind("<Tab>", self._advance_received_field)
        self.raw_provenance.bind("<Tab>", self._advance_received_field)
        self.raw_provenance.bind("<KeyRelease>", self._on_raw_provenance_keyrelease)
        self.raw_provenance.bind("<Down>", lambda event: self._on_suggestions_down_from(self.raw_provenance))
        self.raw_expiry.bind("<Tab>", self._handle_last_received_field)
        for received_entry in (self.raw_name, self.raw_supplier, self.raw_supplier_lot, self.raw_provenance, self.raw_expiry):
            received_entry.bind("<Shift-Tab>", self._retreat_received_field)
        self.raw_expiry.bind("<Return>", lambda event: self.save_received_material())

        for col in range(11):
            frame.grid_columnconfigure(col, weight=1 if col in (1, 3, 5, 7, 9) else 0)

        self.raw_name_sku_var = tk.StringVar(value="")
        tk.Label(
            frame,
            textvariable=self.raw_name_sku_var,
            font=("Segoe UI", 8),
            fg="#2f7ed8",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))

        tk.Label(
            frame,
            text="Format de caducitat: DD-MM-AAAA, DD-MM-AA, MM-AAAA o MM-AA (si no s'indica dia, es guarda com a dia 1). El codi de barres es genera automàticament (data + SKU + núm. d'entrada del dia).",
            font=("Segoe UI", 8),
            fg="#555555",
        ).grid(row=2, column=0, columnspan=11, sticky="w", padx=10, pady=(0, 4))

        tk.Label(frame, text="Últims productes rebuts", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", padx=10, pady=(10, 4), columnspan=11)
        self.received_tree = ttk.Treeview(
            frame, columns=("Nom", "Proveïdor", "SKU", "Lot", "Origen", "Caducitat", "Codi", "Data"), show="headings"
        )
        for text in ("Nom", "Proveïdor", "SKU", "Lot", "Origen", "Caducitat", "Codi", "Data"):
            self.received_tree.heading(text, text=text)
            self.received_tree.column(text, width=120)
        self.received_tree.grid(row=4, column=0, columnspan=11, padx=10, pady=(4, 8), sticky="nsew")
        self._attach_scrollbars(self.received_tree, horizontal=True)
        self._setup_sortable_tree(self.received_tree)
        frame.grid_rowconfigure(4, weight=1)

    def _advance_received_field(self, event):
        self._hide_suggestions()
        widgets = [
            self.raw_name,
            self.raw_supplier,
            self.raw_supplier_lot,
            self.raw_provenance,
            self.raw_expiry,
        ]
        current = event.widget
        index = widgets.index(current)
        if index < len(widgets) - 1:
            widgets[index + 1].focus_set()
        else:
            self._handle_last_received_field(event)
        return "break"

    def _retreat_received_field(self, event):
        self._hide_suggestions()
        widgets = [self.raw_name, self.raw_supplier, self.raw_supplier_lot, self.raw_provenance, self.raw_expiry]
        index = widgets.index(event.widget)
        if index > 0:
            widgets[index - 1].focus_set()
        return "break"

    def _handle_last_received_field(self, event):
        self._hide_suggestions()
        answer = messagebox.askyesno("Imprimir etiqueta", "Vols imprimir el codi de barres d'aquest producte?")
        self.save_received_material(should_print=answer)
        return "break"

    def _on_raw_name_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.raw_name.get().strip()
        self._update_name_sku_hint(text)
        if not text:
            self._hide_suggestions()
            return
        matches = search_catalog_products(self.conn, text)
        if not matches:
            self._hide_suggestions()
            return

        labels = [f"{row['name']}   [{row['sku']}]" if row["sku"] else row["name"] for row in matches]

        def apply_name_suggestion(chosen):
            self.raw_name.delete(0, tk.END)
            self.raw_name.insert(0, chosen["name"])
            self._update_name_sku_hint(chosen["name"])
            self.raw_supplier.focus_set()

        self._show_suggestions(self.raw_name, matches, labels, apply_name_suggestion)

    def _update_name_sku_hint(self, name: str):
        sku = get_catalog_sku_by_name(self.conn, name) if name else None
        self.raw_name_sku_var.set(f"SKU: {sku}" if sku else "")

    def _on_raw_supplier_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.raw_supplier.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_distinct_raw_material_values(self.conn, "supplier", text)
        if not matches:
            self._hide_suggestions()
            return

        def apply_supplier_suggestion(chosen):
            self.raw_supplier.delete(0, tk.END)
            self.raw_supplier.insert(0, chosen)
            self.raw_supplier_lot.focus_set()

        self._show_suggestions(self.raw_supplier, matches, matches, apply_supplier_suggestion)

    def _on_raw_provenance_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.raw_provenance.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_distinct_raw_material_values(self.conn, "provenance", text)
        if not matches:
            self._hide_suggestions()
            return

        def apply_provenance_suggestion(chosen):
            self.raw_provenance.delete(0, tk.END)
            self.raw_provenance.insert(0, chosen)
            self.raw_expiry.focus_set()

        self._show_suggestions(self.raw_provenance, matches, matches, apply_provenance_suggestion)

    def _show_suggestions(self, anchor_entry, items, labels, apply_callback):
        self.suggestions_items = items
        self.suggestions_apply = apply_callback
        self.suggestions_anchor_entry = anchor_entry
        visible_rows = min(len(items), 6)

        parent = anchor_entry.winfo_toplevel()
        if self.suggestions_window is not None and self.suggestions_window.master is not parent:
            self.suggestions_window.destroy()
            self.suggestions_window = None
            self.suggestions_listbox = None
        if self.suggestions_window is None:
            self.suggestions_window = tk.Toplevel(parent)
            self.suggestions_window.overrideredirect(True)
            self.suggestions_window.attributes("-topmost", True)
            self.suggestions_listbox = tk.Listbox(self.suggestions_window, activestyle="dotbox", exportselection=False)
            self.suggestions_listbox.pack(fill="both", expand=True)
            self.suggestions_listbox.bind("<Tab>", self._on_suggestions_accept)
            self.suggestions_listbox.bind("<Shift-Tab>", self._on_suggestions_back_to_entry)
            self.suggestions_listbox.bind("<Return>", self._on_suggestions_accept)
            self.suggestions_listbox.bind("<ButtonRelease-1>", self._on_suggestions_accept)
            self.suggestions_listbox.bind("<Escape>", lambda event: self._hide_suggestions())
        else:
            self.suggestions_listbox.delete(0, tk.END)

        self.suggestions_listbox.configure(height=visible_rows)
        for label in labels:
            self.suggestions_listbox.insert(tk.END, label)

        listbox_font = tkfont.Font(font=self.suggestions_listbox.cget("font"))
        max_label_width = max((listbox_font.measure(label) for label in labels), default=0)

        x = anchor_entry.winfo_rootx()
        y = anchor_entry.winfo_rooty() + anchor_entry.winfo_height()
        width = max(anchor_entry.winfo_width(), max_label_width + 30, 260)
        self.suggestions_window.geometry(f"{width}x{visible_rows * 20}+{x}+{y}")
        self.suggestions_window.deiconify()

    def _hide_suggestions(self):
        if self.suggestions_window is not None:
            self.suggestions_window.withdraw()
        self.suggestions_items = []
        self.suggestions_apply = None
        self.suggestions_anchor_entry = None

    def _on_suggestions_down_from(self, anchor_entry):
        if self.suggestions_window is None or self.suggestions_window.state() != "normal":
            return
        if self.suggestions_anchor_entry is not anchor_entry:
            return
        self.suggestions_listbox.focus_set()
        self.suggestions_listbox.selection_clear(0, tk.END)
        self.suggestions_listbox.selection_set(0)
        self.suggestions_listbox.activate(0)
        return "break"

    def _on_suggestions_back_to_entry(self, event):
        anchor = self.suggestions_anchor_entry
        self._hide_suggestions()
        if anchor is not None:
            anchor.focus_set()
        return "break"

    def _on_suggestions_accept(self, event):
        selection = self.suggestions_listbox.curselection()
        if selection and self.suggestions_apply is not None:
            chosen = self.suggestions_items[selection[0]]
            self.suggestions_apply(chosen)
        self._hide_suggestions()
        return "break"

    def build_envasat_tab(self):
        frame = self.envasat_tab
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        self.package_top_panes = tk.PanedWindow(frame, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.RAISED)
        self.package_top_panes.grid(row=0, column=0, sticky="nsew", padx=10, pady=12)
        form_panel = tk.Frame(self.package_top_panes)
        form_panel.grid_columnconfigure(0, weight=1)
        self.package_top_panes.add(form_panel, minsize=260, stretch="always")

        self.package_form_container = tk.Frame(form_panel)
        self.package_form_container.grid(row=0, column=0, sticky="new", padx=(0, 8))
        self._build_package_form(self.package_form_container)

        self.package_status = tk.StringVar(value="Escaneja un codi de barres, o escriu el nom o SKU de l'ingredient.")
        status_label = tk.Label(form_panel, textvariable=self.package_status, wraplength=320, justify="left")
        status_label.grid(
            row=1, column=0, sticky="ew", padx=(0, 8), pady=4
        )
        status_label.bind("<Configure>", lambda event: status_label.config(wraplength=max(1, event.width)))

        self.package_recipe_panel_container = tk.Frame(self.package_top_panes)
        self.package_top_panes.add(self.package_recipe_panel_container, minsize=420, stretch="always")
        self._build_package_recipe_panel(self.package_recipe_panel_container)

        history_frame = tk.Frame(frame)
        history_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 12))
        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(1, weight=1)
        tk.Label(history_frame, text="Historial d'envasats (doble clic per recarregar un producte)", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.package_history_tree = ttk.Treeview(
            history_frame,
            columns=("Nom", "SKU", "Lot origen", "Lot sortida", "Caducitat", "Procedència", "Quantitat", "Data"),
            show="headings",
            height=10,
        )
        for text in ("Nom", "SKU", "Lot origen", "Lot sortida", "Caducitat", "Procedència", "Quantitat", "Data"):
            self.package_history_tree.heading(text, text=text)
            self.package_history_tree.column(text, width=150)
        self.package_history_tree.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self._attach_scrollbars(self.package_history_tree, horizontal=True)
        self.package_history_tree.bind("<Double-1>", self._on_package_history_select)
        self._setup_sortable_tree(self.package_history_tree, numeric_columns=("Quantitat",))

    def _build_package_form(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        tk.Label(frame, text="Ingredient (codi de barres / nom / SKU)", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(4, 4)
        )
        self.package_ingredient_entry = tk.Entry(frame)
        self.package_ingredient_entry.grid(row=1, column=0, pady=(0, 4), sticky="ew")
        self.package_ingredient_entry.bind("<KeyRelease>", self._on_package_ingredient_keyrelease)
        self.package_ingredient_entry.bind("<Down>", lambda event: self._on_suggestions_down_from(self.package_ingredient_entry))
        self.package_ingredient_entry.bind("<Return>", self._on_package_ingredient_enter)
        self.package_ingredient_entry.bind("<Tab>", self._on_package_ingredient_tab)
        self.package_ingredient_entry.bind("<Shift-Tab>", lambda event: self._hide_suggestions())

        ingredient_help = tk.Label(
            frame,
            text="Intro afegeix l'ingredient i en permet un altre. Tab acaba i passa a Quantitat.",
            font=("Segoe UI", 8),
            fg="#555555",
            wraplength=320,
            justify="left",
        )
        ingredient_help.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ingredient_help.bind("<Configure>", lambda event: ingredient_help.config(wraplength=max(1, event.width)))

        tk.Label(frame, text="Ingredients afegits", font=("Segoe UI", 10, "bold")).grid(row=3, column=0, sticky="w", pady=(4, 4))
        self.ingredients_list = tk.Listbox(frame, height=8)
        self.ingredients_list.grid(row=4, column=0, pady=(0, 4), sticky="ew")
        self._attach_scrollbars(self.ingredients_list)
        self.ingredients_list.bind("<Delete>", self._on_ingredients_list_delete)

        self.package_final_product_label = tk.Label(
            frame, text="Producte final (nom / SKU / codi)", font=("Segoe UI", 10, "bold")
        )
        self.package_final_product_label.grid(row=5, column=0, sticky="w", pady=4)
        final_product_row = tk.Frame(frame)
        final_product_row.grid(row=6, column=0, pady=(0, 4), sticky="ew")
        final_product_row.grid_columnconfigure(0, weight=1)
        self.package_final_product_entry = tk.Entry(final_product_row)
        self.package_final_product_entry.grid(row=0, column=0, sticky="ew")
        self.package_final_product_entry.bind("<KeyRelease>", self._on_package_final_product_keyrelease)
        self.package_final_product_entry.bind("<Down>", lambda event: self._on_suggestions_down_from(self.package_final_product_entry))
        self.package_final_product_entry.bind("<Return>", self._on_package_final_product_enter)
        self.package_final_product_entry.bind("<Tab>", self._on_package_final_product_tab)
        self.package_final_product_entry.bind("<Shift-Tab>", self._on_package_back_to_ingredient)
        self.package_final_product_weight_var = tk.StringVar(value="")
        self.package_final_product_weight_label = tk.Label(
            final_product_row,
            textvariable=self.package_final_product_weight_var,
            font=("Segoe UI", 9),
            fg="#2f7ed8",
            width=10,
            anchor="e",
        )
        self.package_final_product_weight_label.grid(row=0, column=1, padx=(8, 0))
        self.package_final_product_weight_label.bind("<Double-1>", self._on_package_final_product_weight_double_click)

        tk.Label(frame, text="Quantitat", font=("Segoe UI", 10, "bold")).grid(row=7, column=0, sticky="w", pady=4)
        quantity_row = tk.Frame(frame)
        quantity_row.grid(row=8, column=0, pady=(0, 4), sticky="ew")
        quantity_row.grid_columnconfigure(1, weight=1)
        self.package_quantity_entry = tk.Entry(quantity_row, width=15)
        self.package_quantity_entry.grid(row=0, column=0, sticky="w")
        self.package_quantity_entry.bind("<Return>", self._finalize_packaging)
        self.package_quantity_entry.bind("<Shift-Tab>", self._on_package_back_to_final_product)
        tk.Button(
            quantity_row, text="✕", command=self._on_clear_package_form_clicked, width=3,
            fg="white", bg="#c0392b",
        ).grid(row=0, column=2, sticky="e")

    def _build_package_recipe_panel(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.package_recipe_panes = tk.PanedWindow(frame, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.RAISED)
        self.package_recipe_panes.grid(row=0, column=0, sticky="nsew")
        lookup_frame = tk.Frame(self.package_recipe_panes)
        ingredients_frame = tk.Frame(self.package_recipe_panes)
        for panel in (lookup_frame, ingredients_frame):
            panel.grid_columnconfigure(0, weight=1)
            panel.grid_rowconfigure(0, minsize=44)
            panel.grid_rowconfigure(1, minsize=26)
            panel.grid_rowconfigure(2, weight=1)
            self.package_recipe_panes.add(panel, minsize=200, stretch="always")

        tk.Label(lookup_frame, text="Cercador de receptes", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="sw", pady=(0, 4)
        )
        self.package_recipe_search_entry = tk.Entry(lookup_frame)
        self.package_recipe_search_entry.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        self.package_recipe_search_entry.bind("<KeyRelease>", self._on_package_recipe_search_changed)

        self.package_recipe_lookup_tree = ttk.Treeview(
            lookup_frame, columns=("Nom", "SKU", "Coincidència"), show="headings", height=8
        )
        for text, width, minwidth, stretch in (
            ("Nom", 120, 60, True), ("SKU", 55, 45, False), ("Coincidència", 90, 85, False),
        ):
            self.package_recipe_lookup_tree.heading(text, text=text)
            self.package_recipe_lookup_tree.column(text, width=width, minwidth=minwidth, stretch=stretch)
        self.package_recipe_lookup_tree.tag_configure("best_match", foreground="#1a7a1a", font=("Segoe UI", 9, "bold"))
        self.package_recipe_lookup_tree.grid(row=2, column=0, sticky="nsew")
        self._attach_scrollbars(self.package_recipe_lookup_tree, horizontal=True)
        self.package_recipe_lookup_tree.bind("<Double-1>", self._on_package_recipe_lookup_double_click)

        viewing_row = tk.Frame(ingredients_frame)
        viewing_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        viewing_row.grid_columnconfigure(0, weight=1)
        self.package_recipe_viewing_var = tk.StringVar(
            value="Fes doble clic en una recepta per veure'n els ingredients."
        )
        viewing_label = tk.Label(
            viewing_row, textvariable=self.package_recipe_viewing_var, font=("Segoe UI", 9, "bold"),
            wraplength=260, justify="left",
        )
        viewing_label.grid(row=0, column=0, sticky="ew")
        viewing_label.bind("<Configure>", lambda event: viewing_label.config(wraplength=max(1, event.width)))
        self.package_recipe_close_button = tk.Button(
            viewing_row, text="✕", width=3, command=self._close_package_recipe_view
        )
        self.package_recipe_close_button.grid(row=0, column=1, sticky="e")
        self.package_recipe_close_button.grid_remove()

        self.package_recipe_ingredients_tree = ttk.Treeview(
            ingredients_frame, columns=("Afegit", "Nom", "SKU", "Quantitat"), show="headings", height=8
        )
        for text, width in (("Afegit", 50), ("Nom", 150), ("SKU", 70), ("Quantitat", 70)):
            self.package_recipe_ingredients_tree.heading(text, text=text)
            self.package_recipe_ingredients_tree.column(text, width=width)
        self.package_recipe_ingredients_tree.tag_configure("matched", foreground="#1a7a1a", background="#e6f4e6")
        self.package_recipe_ingredients_tree.grid(row=2, column=0, sticky="nsew")
        self._attach_scrollbars(self.package_recipe_ingredients_tree, horizontal=True)

    def _on_package_recipe_search_changed(self, event=None):
        self._populate_package_recipe_lookup()

    def _populate_package_recipe_lookup(self):
        tree = self.package_recipe_lookup_tree
        for row in tree.get_children():
            tree.delete(row)
        self._package_recipe_lookup_rows = {}

        recipes = list_recipes_with_ingredients(self.conn)
        search_text = self.package_recipe_search_entry.get().strip().lower()
        if search_text:
            recipes = [
                recipe for recipe in recipes
                if search_text in (recipe["name"] or "").lower() or search_text in (recipe["sku"] or "").lower()
            ]
        recipes.sort(key=lambda recipe: (recipe["name"] or "").lower())

        if self.package_ingredients:
            ordered = rank_recipes_by_match(self.package_ingredients, recipes)
        else:
            ordered = [(None, recipe) for recipe in recipes]

        for score, recipe in ordered:
            iid = str(recipe["id"])
            percent_text = f"{round(score * 100)}%" if score else ""
            tree.insert("", "end", iid=iid, values=(recipe["name"], recipe["sku"] or "", percent_text))
            if score:
                tree.item(iid, tags=("best_match",))
            self._package_recipe_lookup_rows[iid] = recipe

    def _on_package_recipe_lookup_double_click(self, event):
        selection = self.package_recipe_lookup_tree.selection()
        if not selection:
            return
        recipe = self._package_recipe_lookup_rows.get(selection[0])
        if recipe is None:
            return
        self._open_package_recipe_view(recipe["id"])

    def _open_package_recipe_view(self, recipe_id):
        data = get_recipe_by_id(self.conn, recipe_id)
        if data is None:
            return
        self.package_viewing_recipe = data["recipe"]
        self.package_viewing_recipe_ingredients = data["ingredients"]
        self.package_recipe_viewing_var.set(f"Recepta: {data['recipe']['name']}")
        self.package_recipe_close_button.grid()
        self._refresh_package_recipe_ingredients_view()
        self._populate_package_recipe_lookup()

    def _close_package_recipe_view(self):
        self.package_viewing_recipe = None
        self.package_viewing_recipe_ingredients = []
        self.package_recipe_viewing_var.set("Fes doble clic en una recepta per veure'n els ingredients.")
        self.package_recipe_close_button.grid_remove()
        for row in self.package_recipe_ingredients_tree.get_children():
            self.package_recipe_ingredients_tree.delete(row)
        self._populate_package_recipe_lookup()

    def _refresh_package_recipe_ingredients_view(self):
        tree = self.package_recipe_ingredients_tree
        for row in tree.get_children():
            tree.delete(row)
        if self.package_viewing_recipe is None:
            return
        for item in self.package_viewing_recipe_ingredients:
            matched = ingredient_matches_recipe_ingredients(
                {"sku": item["sku"], "name": item["name"]}, self.package_ingredients
            )
            quantity = item["quantity"]
            quantity_text = f"{quantity:g}" if isinstance(quantity, (int, float)) else (quantity or "")
            tree.insert(
                "", "end", iid=str(item["id"]),
                values=("✓" if matched else "", item["name"], item["sku"] or "", quantity_text),
                tags=("matched",) if matched else (),
            )

    def _refresh_package_recipe_matching(self):
        """Actualitza el panell de receptes després d'afegir/treure un ingredient d'envasat."""
        self._refresh_package_recipe_ingredients_view()
        self._populate_package_recipe_lookup()

    def _on_package_ingredient_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.package_ingredient_entry.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_packaging_ingredients(self.conn, text)
        if not matches:
            self._hide_suggestions()
            return

        labels = []
        for row in matches:
            sku_part = row["sku"] if row["sku"] else "sense SKU"
            entry_date = (row["created_at"] or "").split(" ")[0]
            source = "Barreja preparada" if row["mixture_id"] else "Entrada"
            labels.append(
                f"{row['name']}  [{sku_part}]  {source}:{entry_date}  Lot:{row['supplier_lot']}  Caduca:{row['expiry']}"
            )

        def apply_ingredient_suggestion(chosen):
            self._add_package_ingredient(chosen)

        self._show_suggestions(self.package_ingredient_entry, matches, labels, apply_ingredient_suggestion)

    def _resolve_package_ingredient(self, text):
        """Resol un ingredient a partir d'un codi de barres/lot exacte o d'un nom/SKU sense ambigüitat."""
        material = get_packaging_ingredient_by_barcode(self.conn, text)
        if material is not None:
            return material, None
        matches = search_packaging_ingredients(self.conn, text)
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            return None, "Hi ha diversos lots que coincideixen amb aquest nom o SKU. Selecciona'n un de la llista."
        return None, "Aquest codi, nom o SKU no existeix al registre de productes rebuts."

    def _on_package_ingredient_enter(self, event):
        text = self.package_ingredient_entry.get().strip()
        if not text:
            return "break"
        material, error = self._resolve_package_ingredient(text)
        if material is None:
            if search_packaging_ingredients(self.conn, text):
                messagebox.showwarning("Ingredient no trobat", error)
            else:
                self._open_package_quick_receipt(text)
            return "break"
        self._add_package_ingredient(material)
        return "break"

    def _open_package_quick_receipt(self, text):
        self._hide_suggestions()
        dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.title("Ingredient no registrat · Recepció ràpida")
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        tk.Label(
            dialog, text="Aquest ingredient no existeix als productes rebuts.",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))
        fields = {}
        entries = {}
        for index, (key, title) in enumerate((
            ("name", "Nom"), ("supplier", "Proveïdor"), ("supplier_lot", "Lot"),
            ("provenance", "Origen (opcional)"), ("expiry", "Caducitat"),
        )):
            tk.Label(dialog, text=title, font=("Segoe UI", 9, "bold")).grid(
                row=index * 2 + 1, column=0, sticky="w", padx=16, pady=(4, 2)
            )
            fields[key] = tk.StringVar(value=text if key == "name" and not text.isdecimal() else "")
            entries[key] = tk.Entry(dialog, textvariable=fields[key], width=45)
            entries[key].grid(row=index * 2 + 2, column=0, sticky="ew", padx=16, pady=(0, 4))

        def close():
            current_position = (dialog.winfo_x(), dialog.winfo_y())
            if current_position != initial_position:
                saved_coordinates = (
                    position[0] + current_position[0] - initial_position[0],
                    position[1] + current_position[1] - initial_position[1],
                )
                set_setting(self.conn, "package_quick_receipt_position", json.dumps(saved_coordinates))
            self._hide_suggestions()
            if self.suggestions_window is not None and self.suggestions_window.master is dialog:
                self.suggestions_window.destroy()
                self.suggestions_window = None
                self.suggestions_listbox = None
            dialog.grab_release()
            dialog.destroy()
            self.package_ingredient_entry.focus_set()

        def add_ingredient(register):
            values = {key: variable.get().strip() for key, variable in fields.items()}
            if not values["name"]:
                entries["name"].focus_set()
                return
            if register and not all(values[key] for key in ("supplier", "supplier_lot", "expiry")):
                return
            try:
                if register:
                    material = self._register_received_material(**values)
                else:
                    expiry_date = parse_date(values["expiry"])
                    catalog_product = get_catalog_row_by_sku(self.conn, values["name"])
                    if catalog_product is None:
                        catalog_product = get_catalog_row_by_name(self.conn, values["name"])
                    material = {
                        "name": catalog_product["name"] if catalog_product else values["name"],
                        "supplier_lot": values["supplier_lot"],
                        "supplier": values["supplier"],
                        "provenance": values["provenance"],
                        "expiry": expiry_date.isoformat() if expiry_date else "",
                        "barcode": "", "sku": catalog_product["sku"] if catalog_product else None,
                    }
            except ValueError as exc:
                messagebox.showerror("Recepció no guardada" if register else "Dades invàlides", str(exc), parent=dialog)
                return
            if register:
                self.refresh_materials()
            close()
            self._add_package_ingredient(material)

        buttons = tk.Frame(dialog)
        buttons.grid(row=11, column=0, sticky="ew", padx=16, pady=(12, 16))
        save_button = tk.Button(buttons, text="Guardar recepció i afegir", command=lambda: add_ingredient(True))
        save_button.pack(side=tk.LEFT)
        unregistered_button = tk.Button(buttons, text="Afegir sense registre", command=lambda: add_ingredient(False))
        unregistered_button.pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(buttons, text="Cancel·lar", command=close).pack(side=tk.RIGHT, padx=(8, 0))

        def update_buttons(*args):
            complete = all(fields[key].get().strip() for key in ("name", "supplier", "supplier_lot", "expiry"))
            save_button.config(state=tk.NORMAL if complete else tk.DISABLED)
            unregistered_button.config(state=tk.NORMAL if fields["name"].get().strip() else tk.DISABLED)

        def advance(key):
            self._hide_suggestions()
            field_order = list(entries)
            index = field_order.index(key)
            if index + 1 < len(field_order):
                entries[field_order[index + 1]].focus_set()
            else:
                (save_button if save_button.cget("state") == tk.NORMAL else unregistered_button).focus_set()
            return "break"

        def retreat(key):
            self._hide_suggestions()
            field_order = list(entries)
            index = field_order.index(key)
            if index > 0:
                entries[field_order[index - 1]].focus_set()
            return "break"

        def apply_suggestion(chosen, key):
            fields[key].set(chosen["name"] if key == "name" else chosen)
            advance(key)

        def suggest(event, key):
            if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
                return
            term = fields[key].get().strip()
            if not term:
                self._hide_suggestions()
                return
            if key == "name":
                matches = search_catalog_products(self.conn, term)
                labels = [f"{row['name']}   [{row['sku'] or 'sense SKU'}]" for row in matches]
            else:
                matches = search_distinct_raw_material_values(self.conn, key, term)
                labels = matches
            if not matches:
                self._hide_suggestions()
                return
            self._show_suggestions(
                entries[key], matches, labels,
                lambda chosen: apply_suggestion(chosen, key),
            )

        for key, entry in entries.items():
            entry.bind("<Return>", lambda event, field=key: advance(field))
            entry.bind("<Tab>", lambda event, field=key: advance(field))
            entry.bind("<Shift-Tab>", lambda event, field=key: retreat(field))
        for key in ("name", "supplier", "provenance"):
            entries[key].bind("<KeyRelease>", lambda event, field=key: suggest(event, field))
            entries[key].bind("<Down>", lambda event, field=key: self._on_suggestions_down_from(entries[field]))
        for variable in fields.values():
            variable.trace_add("write", update_buttons)
        entries["expiry"].bind("<Return>", lambda event: add_ingredient(True))
        dialog.bind("<Escape>", lambda event: close())
        dialog.protocol("WM_DELETE_WINDOW", close)
        update_buttons()
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        position = None
        saved_position = get_setting(self.conn, "package_quick_receipt_position")
        if saved_position:
            try:
                candidate = json.loads(saved_position)
                if isinstance(candidate, list) and len(candidate) == 2 and all(isinstance(value, int) for value in candidate):
                    position = candidate
            except (TypeError, ValueError):
                pass
        if position is None:
            position = (
                max(0, (dialog.winfo_screenwidth() - width) // 2),
                max(0, (dialog.winfo_screenheight() - height) // 2),
            )
        dialog.geometry(f"{width}x{height}{position[0]:+d}{position[1]:+d}")
        dialog.deiconify()
        dialog.update_idletasks()
        initial_position = (dialog.winfo_x(), dialog.winfo_y())
        entries["supplier" if fields["name"].get() else "name"].focus_set()

    def _add_package_ingredient(self, material):
        with self._action(f"Envasat: afegir ingredient {material['name']}"):
            self._add_package_ingredient_impl(material)

    def _add_package_ingredient_impl(self, material):
        self.package_reuse_lot = None
        self.package_prepared_mixture_id = None
        material = dict(material)
        try:
            sku = material["sku"]
        except (IndexError, KeyError):
            sku = None
        if not sku:
            sku = get_catalog_sku_by_name(self.conn, material["name"])

        ingredient = {
            "name": material["name"],
            "supplier_lot": material["supplier_lot"],
            "provenance": material["provenance"],
            "expiry": material["expiry"],
            "barcode": material["barcode"],
            "sku": sku or None,
            "supplier": material.get("supplier") or "",
            "created_at": material.get("created_at") or "",
        }
        if material.get("mixture_id"):
            ingredient["mixture_id"] = material["mixture_id"]
            ingredient["ingredients"] = json.loads(material["ingredients_json"])
        self.package_ingredients.append(ingredient)
        if len(self.package_ingredients) == 1 and ingredient.get("mixture_id"):
            self.package_reuse_lot = ingredient["supplier_lot"]
            self.package_prepared_mixture_id = ingredient["mixture_id"]
        sku_part = ingredient["sku"] if ingredient["sku"] else "sense SKU"
        self.ingredients_list.insert(
            tk.END,
            f"{ingredient['name']}  [{sku_part}]  Lot:{ingredient['supplier_lot']}  Caduca:{ingredient['expiry']}",
        )
        self._hide_suggestions()
        self.package_ingredient_entry.delete(0, tk.END)
        self._refresh_package_final_product_state()
        self._refresh_package_recipe_matching()
        self.package_ingredient_entry.focus_set()

    def _on_package_back_to_ingredient(self, event):
        self._hide_suggestions()
        self.package_ingredient_entry.focus_set()
        return "break"

    def _on_package_back_to_final_product(self, event):
        self.package_final_product_entry.focus_set()
        return "break"

    def _on_package_ingredient_tab(self, event):
        self._hide_suggestions()
        if not self.package_ingredients:
            messagebox.showwarning("Sense ingredients", "Cal identificar almenys un ingredient abans de continuar.")
            return "break"
        if self.package_final_product is None:
            self.package_final_product_entry.focus_set()
        else:
            self.package_quantity_entry.focus_set()
        return "break"

    def _refresh_package_final_product_state(self):
        if not self.package_ingredients:
            if self.package_final_product_auto:
                self._apply_final_product_suggestion(None)
            self.package_status.set("Escaneja un codi de barres, o escriu el nom o SKU de l'ingredient.")
            return

        if len(self.package_ingredients) == 1:
            first = self.package_ingredients[0]
            if self.package_final_product_auto:
                self._apply_final_product_suggestion({"name": first["name"], "sku": first["sku"]})
            self.package_status.set(
                f"Ingredient afegit: {first['name']}. Prem Intro per afegir-ne un altre o Tab per anar a Quantitat "
                "(el producte final serà el mateix ingredient, o escriu-ne un altre manualment)."
            )
            return

        skus = {ing["sku"] for ing in self.package_ingredients}
        if len(skus) == 1 and None not in skus:
            unique_sku = next(iter(skus))
            first = self.package_ingredients[0]
            if self.package_final_product_auto:
                self._apply_final_product_suggestion({"name": first["name"], "sku": unique_sku})
            self.package_status.set(
                f"{len(self.package_ingredients)} ingredients del mateix SKU afegits. Producte final (automàtic): "
                f"{first['name']}. Prem Intro per afegir-ne un altre o Tab per anar a Quantitat."
            )
            return

        ranked = rank_recipes_by_match(self.package_ingredients, list_recipes_with_ingredients(self.conn))
        match_score, match = ranked[0] if ranked else (0.0, None)
        if match is not None and match_score > 0:
            if self.package_final_product_auto:
                self._apply_final_product_suggestion({"name": match["name"], "sku": match["sku"]})
            percent = round(match_score * 100)
            self.package_status.set(
                f"{len(self.package_ingredients)} ingredients afegits. Coincidència aproximada ({percent}%) amb la "
                f"recepta '{match['name']}'. Comprova el producte final o escriu-ne un altre manualment."
            )
        else:
            if self.package_final_product_auto:
                self._apply_final_product_suggestion(None)
            self.package_status.set(
                f"{len(self.package_ingredients)} ingredients afegits amb SKU diferent. No s'ha trobat cap recepta "
                "coincident; indica el producte final (nom, SKU o codi de barres)."
            )

    def _on_package_final_product_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.package_final_product_entry.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_catalog_products(self.conn, text)
        if not matches:
            self._hide_suggestions()
            return

        labels = [f"{row['name']}   [{row['sku']}]" if row["sku"] else row["name"] for row in matches]

        def apply_final_product_suggestion(chosen):
            self._set_package_final_product({"name": chosen["name"], "sku": chosen["sku"]})

        self._show_suggestions(self.package_final_product_entry, matches, labels, apply_final_product_suggestion)

    def _resolve_package_final_product(self, text):
        material = get_material_by_barcode(self.conn, text)
        if material is not None:
            return {"name": material["name"], "sku": get_catalog_sku_by_name(self.conn, material["name"])}, None
        matches = search_catalog_products(self.conn, text)
        if len(matches) == 1:
            row = matches[0]
            return {"name": row["name"], "sku": row["sku"]}, None
        if len(matches) > 1:
            return None, "Hi ha diversos productes que coincideixen. Selecciona'n un de la llista."
        # Es permet indicar manualment un producte final que no existeix al catàleg importat.
        return {"name": text, "sku": None}, None

    def _apply_final_product_suggestion(self, product):
        """Omple el producte final amb un suggeriment automàtic (ingredient únic o recepta coincident).

        A diferència de `_set_package_final_product`, no roba el focus ni mostra cap missatge d'estat
        propi, perquè el crida `_refresh_package_final_product_state` mentre s'estan afegint ingredients.
        """
        self.package_final_product = product
        self.package_final_product_entry.delete(0, tk.END)
        if product is not None:
            self.package_final_product_entry.insert(0, product["name"])
        self.package_final_product_auto = True
        self._refresh_package_final_product_weight()

    def _set_package_final_product(self, product):
        with self._action(f"Envasat: producte final {product['name']}"):
            self._set_package_final_product_impl(product)

    def _set_package_final_product_impl(self, product):
        self.package_final_product = product
        self.package_final_product_entry.delete(0, tk.END)
        self.package_final_product_entry.insert(0, product["name"])
        self.package_final_product_auto = False
        self._hide_suggestions()
        self._refresh_package_final_product_weight()
        self.package_status.set(f"Producte final seleccionat: {product['name']} (SKU {product['sku'] or '—'}).")
        self.package_quantity_entry.focus_set()

    def _refresh_package_final_product_weight(self):
        sku = (self.package_final_product or {}).get("sku")
        row = get_catalog_row_by_sku(self.conn, sku) if sku else None
        self._package_final_product_catalog_row = row
        if row is None or row["weight"] is None:
            self.package_final_product_weight_var.set("")
        else:
            self.package_final_product_weight_var.set(f"{row['weight']:g} kg")

    def _on_package_final_product_weight_double_click(self, event):
        row = self._package_final_product_catalog_row
        if row is None:
            messagebox.showinfo(
                "Pes no disponible",
                "Aquest producte no té SKU al catàleg importat; no es pot editar el pes.",
            )
            return
        current_value = f"{row['weight']:g}" if row["weight"] is not None else ""
        editor = tk.Entry(self.package_final_product_weight_label.master)
        editor.insert(0, current_value)
        editor.select_range(0, tk.END)
        x = self.package_final_product_weight_label.winfo_x()
        y = self.package_final_product_weight_label.winfo_y()
        width = max(self.package_final_product_weight_label.winfo_width(), 80)
        height = self.package_final_product_weight_label.winfo_height()
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        def commit(event=None):
            new_value = editor.get().strip().replace(",", ".")
            editor.destroy()
            try:
                value = float(new_value) if new_value else 0.0
            except ValueError:
                messagebox.showerror("Pes invàlid", f"'{new_value}' no és un número vàlid.")
                return
            with self._action(f"Productes: editar pes de '{(self.package_final_product or {}).get('name', '')}'"):
                update_catalog_field(self.conn, row["id"], "weight", value)
            self._refresh_package_final_product_weight()
            if hasattr(self, "product_tree"):
                self.refresh_product_catalog_table()

        def cancel(event=None):
            editor.destroy()

        editor.bind("<Return>", commit)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", cancel)

    def _on_package_final_product_enter(self, event):
        text = self.package_final_product_entry.get().strip()
        if not text:
            messagebox.showwarning("Producte faltant", "Indica el producte final (nom, SKU o codi de barres).")
            return "break"
        product, error = self._resolve_package_final_product(text)
        if product is None:
            messagebox.showwarning("Producte no trobat", error)
            return "break"
        self._set_package_final_product(product)
        return "break"

    def _on_package_final_product_tab(self, event):
        self._hide_suggestions()
        text = self.package_final_product_entry.get().strip()
        if not text:
            messagebox.showwarning("Producte faltant", "Indica el producte final (nom, SKU o codi de barres) abans de continuar.")
            return "break"
        if self.package_final_product is not None and self.package_final_product["name"] == text:
            self.package_quantity_entry.focus_set()
            return "break"
        product, error = self._resolve_package_final_product(text)
        if product is None:
            messagebox.showwarning("Producte no trobat", error)
            return "break"
        self._set_package_final_product(product)
        return "break"

    def _finalize_packaging(self, event=None):
        product_name = (self.package_final_product or {}).get("name", "")
        quantity_text = self.package_quantity_entry.get().strip() or "0"
        with self._action(f"Envasat: {product_name} (quantitat {quantity_text})"):
            return self._finalize_packaging_impl(event)

    def _finalize_packaging_impl(self, event=None):
        if not self.package_ingredients:
            messagebox.showwarning("Sense ingredients", "Afegeix almenys un ingredient abans de continuar.")
            self.package_ingredient_entry.focus_set()
            return "break"

        if self.package_final_product is None:
            messagebox.showwarning(
                "Producte final no definit",
                "Indica el producte final (nom, SKU o codi de barres) abans de continuar.",
            )
            self.package_final_product_entry.focus_set()
            return "break"

        quantity_text = self.package_quantity_entry.get().strip()
        if not quantity_text:
            quantity = 0
        else:
            try:
                quantity = int(quantity_text)
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror(
                    "Quantitat invàlida",
                    f"La quantitat '{quantity_text}' ha de ser un número enter positiu (deixa-la buida per registrar-la com a 0).",
                )
                return "break"

        try:
            expiry_date = compute_expiry_from_ingredients(self.package_ingredients)
        except ValueError as exc:
            messagebox.showerror("Caducitat invàlida", str(exc))
            return "break"

        product_name = self.package_final_product["name"]
        sku_value = self.package_final_product.get("sku") or ""

        if len(self.package_ingredients) == 1:
            single = self.package_ingredients[0]
            supplier_lot = single["supplier_lot"] or ""
            provenance = single["provenance"] or ""
        else:
            supplier_lot = ""
            provenance = ""

        weight = get_catalog_weight_by_name(self.conn, product_name) or 0.0

        if self.package_reuse_lot:
            packed_lot = self.package_reuse_lot
        else:
            today_str = date.today().isoformat()
            sequence = count_packed_lots_for_sku_on_date(self.conn, sku_value, today_str) + 1
            if sequence > 99:
                messagebox.showerror(
                    "Límit assolit",
                    "Ja s'han generat 99 lots de sortida avui per aquest SKU. No se'n pot crear un de nou.",
                )
                return "break"
            packed_lot = generate_daily_sequential_lot(sequence)
        barcode = packed_lot

        if len(self.package_ingredients) > 1 and not self.package_reuse_lot:
            mixture = add_prepared_mixture(
                self.conn, product_name, sku_value, packed_lot, str(expiry_date), self.package_ingredients,
            )
            self.populate_prepared_mixture_history()
            if messagebox.askyesno("Etiqueta de barreja", "Vols imprimir l'etiqueta de traçabilitat de la barreja sense envasar?"):
                try:
                    label_path = create_mixture_label_image(
                        mixture["name"], mixture["lot"], mixture["sku"], mixture["expiry"],
                        mixture["barcode"], mixture["created_at"], json.loads(mixture["ingredients_json"]),
                    )
                    result = print_label_file(label_path, self.printer_name)
                    if result["status"] == "error":
                        raise RuntimeError(result["message"])
                except Exception as exc:
                    messagebox.showwarning("Barreja guardada", f"La barreja s'ha guardat, però no s'ha pogut imprimir l'etiqueta:\n{exc}")
            if quantity:
                self._record_prepared_mixture_packaging(mixture, quantity)
            self._clear_package_form()
            self.package_status.set(f"Barreja preparada: {product_name} | Lot {packed_lot} | Envasats: {quantity}")
            return "break"

        add_packaged_product(
            self.conn,
            product_name,
            packed_lot,
            supplier_lot,
            provenance,
            str(expiry_date),
            barcode,
            quantity,
            weight,
            self.session_id,
            self.package_ingredients,
            sku_value,
            self.package_prepared_mixture_id,
        )

        self.populate_package_history()
        self.refresh_history()
        try:
            label_path = create_label_image(
                product_name, packed_lot, provenance, str(expiry_date), barcode,
                weight=f"{weight} kg" if weight else None,
            )
            for label_index in range(quantity or 1):
                result = print_label_file(label_path, self.printer_name)
                if result["status"] == "error":
                    raise RuntimeError(result["message"])
            status_extra = result["message"]
        except Exception as exc:
            status_extra = f"etiqueta no impresa: {exc}"

        self._clear_package_form()
        self.package_status.set(f"Envasat creat: {product_name} | Lot {packed_lot} | Quantitat {quantity} | {status_extra}")
        return "break"

    def _record_prepared_mixture_packaging(self, mixture, quantity):
        weight = get_catalog_weight_by_name(self.conn, mixture["name"]) or 0.0
        with self._action(f"Envasar barreja: {mixture['name']} (quantitat {quantity})"):
            add_packaged_product(
                self.conn, mixture["name"], mixture["lot"], "", "", mixture["expiry"], mixture["lot"],
                quantity, weight, self.session_id, json.loads(mixture["ingredients_json"]), mixture["sku"], mixture["id"],
            )
        self.populate_package_history()
        self.refresh_history()
        try:
            label_path = create_label_image(
                mixture["name"], mixture["lot"], "", mixture["expiry"], mixture["lot"],
                weight=f"{weight} kg" if weight else None,
            )
            for label_index in range(quantity):
                result = print_label_file(label_path, self.printer_name)
                if result["status"] == "error":
                    raise RuntimeError(result["message"])
        except Exception as exc:
            messagebox.showwarning(
                "Envasat registrat", f"L'envasat de {quantity} unitats s'ha guardat, però no s'han pogut imprimir totes les etiquetes:\n{exc}",
            )

    def _clear_package_form(self):
        self.package_ingredients = []
        self.package_final_product = None
        self.package_final_product_auto = True
        self.package_reuse_lot = None
        self.package_prepared_mixture_id = None
        self.ingredients_list.delete(0, tk.END)
        self.package_ingredient_entry.delete(0, tk.END)
        self.package_final_product_entry.delete(0, tk.END)
        self.package_quantity_entry.delete(0, tk.END)
        self._hide_suggestions()
        self._refresh_package_final_product_state()
        self._refresh_package_recipe_matching()
        self.package_ingredient_entry.focus_set()

    def _on_clear_package_form_clicked(self):
        has_content = bool(
            self.package_ingredients
            or self.package_ingredient_entry.get().strip()
            or self.package_final_product_entry.get().strip()
            or self.package_quantity_entry.get().strip()
        )
        if has_content and not messagebox.askyesno(
            "Esborrar formulari", "Segur que vols esborrar tots els ingredients i dades introduïdes?"
        ):
            return
        with self._action("Envasat: esborrar formulari"):
            self._clear_package_form()

    def _on_ingredients_list_delete(self, event):
        selection = self.ingredients_list.curselection()
        if not selection:
            return "break"
        index = selection[0]
        with self._action(f"Envasat: treure ingredient {self.package_ingredients[index]['name']}"):
            self._remove_package_ingredient(index)
        return "break"

    def _remove_package_ingredient(self, index):
        del self.package_ingredients[index]
        self.ingredients_list.delete(index)
        self.package_reuse_lot = None
        self.package_prepared_mixture_id = None
        if len(self.package_ingredients) == 1 and self.package_ingredients[0].get("mixture_id"):
            self.package_reuse_lot = self.package_ingredients[0]["supplier_lot"]
            self.package_prepared_mixture_id = self.package_ingredients[0]["mixture_id"]
        self._refresh_package_final_product_state()
        self._refresh_package_recipe_matching()

    def _on_package_history_select(self, event):
        selection = self.package_history_tree.selection()
        if not selection:
            return
        row = self._package_history_rows.get(selection[0])
        if row is None:
            return
        self._load_packaged_product_into_form(row)

    def _load_packaged_product_into_form(self, row):
        with self._action(f"Envasat: recarregar {row['name']} de l'historial"):
            self._load_packaged_product_into_form_impl(row)

    def _load_packaged_product_into_form_impl(self, row):
        try:
            ingredients = json.loads(row["ingredients_json"] or "[]")
        except (ValueError, TypeError):
            ingredients = []

        self.package_ingredients = []
        self.ingredients_list.delete(0, tk.END)
        for item in ingredients:
            ingredient = dict(item)
            ingredient.update({
                "name": item.get("name", ""),
                "supplier_lot": item.get("supplier_lot", ""),
                "provenance": item.get("provenance", ""),
                "expiry": item.get("expiry", ""),
                "barcode": item.get("barcode", ""),
                "sku": item.get("sku") or None,
            })
            self.package_ingredients.append(ingredient)
            sku_part = ingredient["sku"] if ingredient["sku"] else "sense SKU"
            self.ingredients_list.insert(
                tk.END,
                f"{ingredient['name']}  [{sku_part}]  Lot:{ingredient['supplier_lot']}  Caduca:{ingredient['expiry']}",
            )

        sku = row["sku"] or get_catalog_sku_by_name(self.conn, row["name"])
        self.package_final_product = {"name": row["name"], "sku": sku}
        self.package_final_product_auto = False
        self.package_reuse_lot = row["packed_lot"]
        self.package_prepared_mixture_id = row["prepared_mixture_id"]

        self.package_final_product_entry.delete(0, tk.END)
        self.package_final_product_entry.insert(0, row["name"])
        self._refresh_package_final_product_weight()

        self.package_quantity_entry.delete(0, tk.END)
        self._hide_suggestions()
        self._refresh_package_recipe_matching()
        self.package_status.set(
            f"Producte carregat de l'historial: {row['name']} | Lot {row['packed_lot']}. "
            "Introdueix la quantitat i prem Intro per registrar-ne més."
        )
        self.package_quantity_entry.focus_set()

    def populate_package_history(self):
        products = list_packaged_products(self.conn)
        for row in self.package_history_tree.get_children():
            self.package_history_tree.delete(row)
        self._package_history_rows = {}
        for row in products[:30]:
            sku = row["sku"] or get_catalog_sku_by_name(self.conn, row["name"]) or ""
            iid = str(row["id"])
            self._package_history_rows[iid] = row
            self.package_history_tree.insert(
                "", "end", iid=iid,
                values=(row["name"], sku, row["supplier_lot"], row["packed_lot"], row["expiry"], row["provenance"], row["quantity"], row["created_at"]),
            )

    def build_csv_tab(self):
        frame = self.csv_tab
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        top_bar = tk.Frame(frame)
        top_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 6))
        tk.Button(top_bar, text="Carregar CSV", command=self._on_load_csv_clicked, width=16).pack(side="left")
        self.csv_filename_var = tk.StringVar(value="")
        tk.Label(top_bar, textvariable=self.csv_filename_var, font=("Segoe UI", 9, "italic"), fg="#2f7ed8").pack(
            side="left", padx=(10, 0)
        )

        table_frame = tk.Frame(frame)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=10)
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        self.product_tree = ttk.Treeview(table_frame, columns=_CATALOG_COLUMNS, show="headings")
        for text, width in (("SKU", 150), ("Nom", 380), ("Pes", 90), ("Categoria", 150)):
            self.product_tree.heading(text, text=text)
            self.product_tree.column(text, width=width)
        self.product_tree.grid(row=0, column=0, sticky="nsew")
        product_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.product_tree.yview)
        self.product_tree.configure(yscrollcommand=product_scroll.set)
        product_scroll.grid(row=0, column=1, sticky="ns")
        self.product_tree.bind("<Double-1>", self._on_product_tree_double_click)
        self._setup_sortable_tree(self.product_tree, numeric_columns=("Pes",))

        search_row = tk.Frame(frame)
        search_row.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 12))
        search_row.grid_columnconfigure(5, weight=1)

        tk.Label(search_row, text="Cerca", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.product_search_var = tk.StringVar(value="")
        self.product_search_entry = tk.Entry(search_row, textvariable=self.product_search_var, width=30)
        self.product_search_entry.grid(row=0, column=1, padx=(6, 4), sticky="w")
        self.product_search_var.trace_add("write", self._on_product_search_changed)
        self.product_search_entry.bind("<Up>", self._product_search_prev)
        self.product_search_entry.bind("<Down>", self._product_search_next)

        tk.Button(search_row, text="▲", command=self._product_search_prev, width=3).grid(row=0, column=2, padx=2)
        tk.Button(search_row, text="▼", command=self._product_search_next, width=3).grid(row=0, column=3, padx=2)
        tk.Button(search_row, text="✕", command=self._clear_product_search, width=3).grid(row=0, column=4, padx=(2, 10))

        self.product_edit_summary_var = tk.StringVar(value="Cap modificació")
        tk.Label(
            search_row, textvariable=self.product_edit_summary_var, font=("Segoe UI", 9), fg="#555555"
        ).grid(row=0, column=5, sticky="e")
        tk.Button(
            search_row, text="🔎", command=self._show_edited_products_window, width=3
        ).grid(row=0, column=6, padx=(6, 0), sticky="e")

    def _on_load_csv_clicked(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("Tots", "*.*")])
        if not file_path:
            return
        try:
            headers, rows = read_csv_file(file_path)
        except Exception as exc:
            messagebox.showerror("Error llegint el CSV", str(exc))
            return
        if not headers or not rows:
            messagebox.showwarning("CSV buit", "El fitxer seleccionat no té dades.")
            return
        self._open_csv_mapping_dialog(file_path, headers, rows)

    def _open_csv_mapping_dialog(self, file_path, headers, rows):
        dialog = tk.Toplevel(self)
        dialog.title("Importar CSV de productes")
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)

        def guess(keywords):
            for header in headers:
                low = header.lower()
                if any(keyword in low for keyword in keywords):
                    return header
            return headers[0]

        sku_var = tk.StringVar(value=guess(("sku", "codi", "referencia", "ref")))
        name_var = tk.StringVar(value=guess(("nom", "name", "descripcio", "producte")))
        weight_var = tk.StringVar(value=guess(("pes", "weight", "kg")))
        category_var = tk.StringVar(value=guess(("categoria", "category", "familia", "grup")))

        tk.Label(dialog, text="Columna SKU", font=("Segoe UI", 9, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(12, 4)
        )
        sku_combo = ttk.Combobox(dialog, state="readonly", values=headers, textvariable=sku_var)
        sku_combo.grid(row=0, column=1, sticky="ew", padx=10, pady=(12, 4))

        tk.Label(dialog, text="Columna NOM", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", padx=10, pady=4)
        name_combo = ttk.Combobox(dialog, state="readonly", values=headers, textvariable=name_var)
        name_combo.grid(row=1, column=1, sticky="ew", padx=10, pady=4)

        tk.Label(dialog, text="Columna PÈS", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w", padx=10, pady=4)
        weight_combo = ttk.Combobox(dialog, state="readonly", values=headers, textvariable=weight_var)
        weight_combo.grid(row=2, column=1, sticky="ew", padx=10, pady=4)

        tk.Label(dialog, text="Columna CATEGORIA", font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", padx=10, pady=4)
        category_combo = ttk.Combobox(dialog, state="readonly", values=headers, textvariable=category_var)
        category_combo.grid(row=3, column=1, sticky="ew", padx=10, pady=4)

        tk.Label(dialog, text="Vista prèvia (fila 2 del CSV)", font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(14, 4)
        )
        preview_var = tk.StringVar()
        tk.Label(dialog, textvariable=preview_var, justify="left", wraplength=420).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 10)
        )

        first_row = rows[0]

        def update_preview(*_args):
            sku = first_row.get(sku_var.get(), "")
            name = first_row.get(name_var.get(), "")
            weight = first_row.get(weight_var.get(), "")
            category = first_row.get(category_var.get(), "")
            preview_var.set(f"SKU: {sku}\nNom: {name}\nPès: {weight}\nCategoria: {category}")

        sku_combo.bind("<<ComboboxSelected>>", update_preview)
        name_combo.bind("<<ComboboxSelected>>", update_preview)
        weight_combo.bind("<<ComboboxSelected>>", update_preview)
        category_combo.bind("<<ComboboxSelected>>", update_preview)
        update_preview()

        button_row = tk.Frame(dialog)
        button_row.grid(row=6, column=0, columnspan=2, pady=16)

        def accept():
            sku_field, name_field, weight_field = sku_var.get(), name_var.get(), weight_var.get()
            category_field = category_var.get()
            if not sku_field or not name_field or not weight_field or not category_field:
                messagebox.showwarning(
                    "Mapejat incomplet", "Selecciona les quatre columnes abans d'acceptar.", parent=dialog
                )
                return
            if len({sku_field, name_field, weight_field, category_field}) < 4:
                messagebox.showwarning(
                    "Columnes repetides", "Les quatre columnes han de ser diferents.", parent=dialog
                )
                return
            normalized = parse_csv_mapping(rows, sku_field, name_field, weight_field, category_field)
            if not normalized:
                messagebox.showwarning(
                    "CSV sense dades",
                    "No s'ha pogut extreure cap producte amb el mapejat seleccionat.",
                    parent=dialog,
                )
                return
            with self._action(f"Importar CSV: {os.path.basename(file_path)}"):
                count = replace_catalog_rows(self.conn, normalized, file_path)
                set_setting(self.conn, "csv_source_file", file_path)
            self.refresh_product_catalog_table()
            dialog.destroy()
            messagebox.showinfo("CSV importat", f"S'han importat {count} productes.")

        tk.Button(button_row, text="Acceptar", command=accept, width=14, bg="#2f7ed8", fg="white").pack(
            side="left", padx=6
        )
        tk.Button(button_row, text="Cancel·lar", command=dialog.destroy, width=14).pack(side="left", padx=6)

        sku_combo.focus_set()

    def refresh_product_catalog_table(self):
        for item in self.product_tree.get_children():
            self.product_tree.delete(item)
        for row in list_catalog_products(self.conn):
            weight_display = f"{row['weight']:g}" if row["weight"] is not None else ""
            self.product_tree.insert(
                "", "end", iid=str(row["id"]), values=(row["sku"], row["name"], weight_display, row["category"] or "")
            )
        csv_source = get_setting(self.conn, "csv_source_file", "")
        self.csv_filename_var.set(os.path.basename(csv_source) if csv_source else "")
        self._refresh_product_edit_summary()
        self._on_product_search_changed()

    def _on_product_tree_double_click(self, event):
        tree = self.product_tree
        if tree.identify("region", event.x, event.y) != "cell":
            return
        row_iid = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if not row_iid or not column_id:
            return
        try:
            column_name = _CATALOG_COLUMNS[int(column_id.replace("#", "")) - 1]
        except (IndexError, ValueError):
            return
        field = _CATALOG_FIELD_BY_COLUMN.get(column_name)
        if field is None:
            return
        bbox = tree.bbox(row_iid, column_id)
        if not bbox:
            return
        x, y, width, height = bbox
        current_value = tree.set(row_iid, column_name)

        editor = tk.Entry(tree)
        editor.insert(0, current_value)
        editor.select_range(0, tk.END)
        editor.icursor(tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()

        def commit(event=None):
            new_value = editor.get().strip()
            editor.destroy()
            self._apply_catalog_edit(row_iid, column_name, field, current_value, new_value)

        def cancel(event=None):
            editor.destroy()

        editor.bind("<Return>", commit)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", cancel)

    def _apply_catalog_edit(self, row_iid, column_name, field, old_display_value, new_value):
        if field == "weight":
            normalized = new_value.replace(",", ".")
            try:
                value = float(normalized) if normalized else 0.0
            except ValueError:
                messagebox.showerror("Pes invàlid", f"'{new_value}' no és un número vàlid.")
                return
            display_value = f"{value:g}"
            store_value = value
        else:
            store_value = new_value
            display_value = new_value
        if display_value == old_display_value:
            return
        sku_text = self.product_tree.set(row_iid, "SKU")
        name_text = self.product_tree.set(row_iid, "Nom")
        with self._action(f"Productes: editar {column_name} de '{name_text or sku_text}'"):
            update_catalog_field(self.conn, int(row_iid), field, store_value)
        self.product_tree.set(row_iid, column_name, display_value)
        self._refresh_product_edit_summary()

    def _refresh_product_edit_summary(self):
        counts = get_catalog_edit_counts(self.conn)
        parts = []
        if counts["sku"]:
            parts.append(f"{counts['sku']} SKU")
        if counts["name"]:
            parts.append(f"{counts['name']} {'Nom' if counts['name'] == 1 else 'Noms'}")
        if counts["weight"]:
            parts.append(f"{counts['weight']} {'Pes' if counts['weight'] == 1 else 'Pesos'}")
        if not parts:
            self.product_edit_summary_var.set("Cap modificació")
        elif len(parts) == 1:
            self.product_edit_summary_var.set(f"{parts[0]} modificats")
        else:
            self.product_edit_summary_var.set(f"{', '.join(parts[:-1])} i {parts[-1]} modificats")

    def _show_edited_products_window(self):
        window = tk.Toplevel(self)
        window.title("Productes amb dades modificades")
        window.geometry("680x450")

        top_bar = tk.Frame(window)
        top_bar.pack(fill="x", padx=8, pady=(8, 0))
        revert_all_button = tk.Button(
            top_bar, text="Revertir totes les modificacions", fg="white", bg="#c0392b",
        )
        revert_all_button.pack(side="right")

        container = tk.Frame(window)
        container.pack(fill="both", expand=True, padx=8, pady=8)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def populate():
            for child in scroll_frame.winfo_children():
                child.destroy()

            rows = list_edited_catalog_products(self.conn)

            normal_font = tkfont.Font(font=("Segoe UI", 9))
            underline_font = tkfont.Font(font=("Segoe UI", 9))
            underline_font.configure(underline=True)

            for col, text in enumerate(_CATALOG_EDITABLE_COLUMNS):
                tk.Label(scroll_frame, text=text, font=("Segoe UI", 9, "bold")).grid(
                    row=0, column=col, sticky="w", padx=8, pady=4
                )

            if not rows:
                tk.Label(scroll_frame, text="No hi ha cap producte amb dades modificades.").grid(
                    row=1, column=0, columnspan=4, padx=8, pady=8, sticky="w"
                )

            for i, row in enumerate(rows, start=1):
                weight_display = f"{row['weight']:g}" if row["weight"] is not None else ""
                tk.Label(
                    scroll_frame, text=row["sku"], font=underline_font if row["edited_sku"] else normal_font
                ).grid(row=i, column=0, sticky="w", padx=8, pady=2)
                tk.Label(
                    scroll_frame, text=row["name"], font=underline_font if row["edited_name"] else normal_font
                ).grid(row=i, column=1, sticky="w", padx=8, pady=2)
                tk.Label(
                    scroll_frame, text=weight_display, font=underline_font if row["edited_weight"] else normal_font
                ).grid(row=i, column=2, sticky="w", padx=8, pady=2)
                tk.Button(
                    scroll_frame, text="Revertir", width=10,
                    command=lambda row_id=row["id"]: revert_row(row_id),
                ).grid(row=i, column=3, sticky="e", padx=8, pady=2)

        def revert_row(row_id):
            product = self.conn.execute("SELECT name FROM product_catalog WHERE id = ?", (row_id,)).fetchone()
            with self._action(f"Productes: revertir modificacions de '{product['name'] if product else row_id}'"):
                revert_catalog_row(self.conn, row_id)
            self.refresh_product_catalog_table()
            populate()

        populate()

        def revert_all():
            if not messagebox.askyesno(
                "Revertir totes les modificacions",
                "Segur que vols desfer TOTES les modificacions manuals (SKU, Nom i Pes) del catàleg de productes?",
                parent=window,
            ):
                return
            with self._action("Productes: revertir totes les modificacions"):
                revert_all_catalog_edits(self.conn)
            self.refresh_product_catalog_table()
            populate()

        revert_all_button.configure(command=revert_all)

    def _on_product_search_changed(self, *_args):
        term = self.product_search_var.get().strip().lower()
        self._product_search_matches = []
        self._product_search_index = -1
        if not term:
            self.product_tree.selection_remove(*self.product_tree.selection())
            return
        for iid in self.product_tree.get_children():
            values = [str(value).lower() for value in self.product_tree.item(iid, "values")]
            if any(term in value for value in values):
                self._product_search_matches.append(iid)
        if self._product_search_matches:
            self._product_search_index = 0
            self._select_product_search_match()
        else:
            self.product_tree.selection_remove(*self.product_tree.selection())

    def _select_product_search_match(self):
        if not self._product_search_matches:
            return
        iid = self._product_search_matches[self._product_search_index]
        self.product_tree.selection_set(iid)
        self.product_tree.see(iid)
        self.product_tree.focus(iid)

    def _product_search_next(self, event=None):
        if not self._product_search_matches:
            return "break"
        self._product_search_index = (self._product_search_index + 1) % len(self._product_search_matches)
        self._select_product_search_match()
        return "break"

    def _product_search_prev(self, event=None):
        if not self._product_search_matches:
            return "break"
        self._product_search_index = (self._product_search_index - 1) % len(self._product_search_matches)
        self._select_product_search_match()
        return "break"

    def _clear_product_search(self):
        self.product_search_var.set("")
        self.product_search_entry.focus_set()

    def build_recipes_tab(self):
        frame = self.recipes_tab
        frame.grid_columnconfigure(0, weight=2)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        left = tk.Frame(frame)
        left.grid(row=0, column=0, sticky="nsew", padx=10, pady=12)
        left.grid_columnconfigure(1, weight=1)
        left.grid_rowconfigure(2, weight=1)

        tk.Label(left, text="Producte", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.recipe_product_entry = tk.Entry(left)
        self.recipe_product_entry.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        self.recipe_product_entry.bind("<KeyRelease>", self._on_recipe_product_keyrelease)
        self.recipe_product_entry.bind("<Down>", lambda event: self._on_suggestions_down_from(self.recipe_product_entry))

        tk.Label(left, text="Ingredients de la recepta", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(14, 4)
        )

        ingredients_frame = tk.Frame(left)
        ingredients_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        ingredients_frame.grid_columnconfigure(0, weight=1)
        ingredients_frame.grid_rowconfigure(0, weight=1)

        self.recipe_ingredients_tree = ttk.Treeview(
            ingredients_frame, columns=("SKU", "Nom", "Quantitat"), show="headings", height=10, selectmode="none"
        )
        for text, width in (("SKU", 120), ("Nom", 260), ("Quantitat", 90)):
            self.recipe_ingredients_tree.heading(text, text=text)
            self.recipe_ingredients_tree.column(text, width=width)
        self.recipe_ingredients_tree.tag_configure("placeholder", foreground="#999999", font=("Segoe UI", 9, "italic"))
        self.recipe_ingredients_tree.grid(row=0, column=0, sticky="nsew")

        def _on_recipe_ing_scroll(*scroll_args):
            self.recipe_ingredients_tree.yview(*scroll_args)
            self._update_recipe_ing_cell_highlight()

        recipe_ing_scroll = ttk.Scrollbar(ingredients_frame, orient="vertical", command=_on_recipe_ing_scroll)
        self.recipe_ingredients_tree.configure(yscrollcommand=recipe_ing_scroll.set)
        recipe_ing_scroll.grid(row=0, column=1, sticky="ns")
        self.recipe_ingredients_tree.bind("<Delete>", self._on_recipe_ingredient_delete)
        self.recipe_ingredients_tree.bind("<Double-1>", self._on_recipe_ingredient_double_click)
        self.recipe_ingredients_tree.bind("<Button-1>", self._on_recipe_ingredient_click, add="+")
        self.recipe_ingredients_tree.bind("<Control-v>", self._on_recipe_ingredients_paste)
        self.recipe_ingredients_tree.bind("<Key>", self._on_recipe_ingredient_key)
        self.recipe_ingredients_tree.bind("<MouseWheel>", lambda event: self.after(10, self._update_recipe_ing_cell_highlight))
        self.recipe_ingredients_tree.bind("<Configure>", lambda event: self._update_recipe_ing_cell_highlight())
        self._recipe_ing_active_cell = (None, None)
        self._recipe_ing_highlight_frames = None

        tk.Label(
            left,
            text="Clica una cel·la i escriu directament per editar-la (o doble clic). Enganxa (Ctrl+V) "
            "files i columnes copiades d'un full de càlcul. Sempre hi ha una fila buida a sota per "
            "afegir-ne una altra.",
            font=("Segoe UI", 8),
            fg="#555555",
            wraplength=420,
            justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 8))

        self.recipe_save_button = tk.Button(
            left, text="Afegir recepta", command=self._on_save_recipe_clicked, width=20, bg="#2f7ed8", fg="white"
        )
        self.recipe_save_button.grid(row=5, column=0, sticky="w", pady=(10, 0))
        tk.Button(left, text="Nova recepta", command=self._on_new_recipe_clicked, width=14).grid(
            row=5, column=1, sticky="w", padx=(10, 0), pady=(10, 0)
        )

        right = tk.Frame(frame)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=12)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        tk.Label(right, text="Receptes existents", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")

        self.recipe_list_tree = ttk.Treeview(right, columns=("Nom", "SKU", "Categoria"), show="headings")
        for text, width in (("Nom", 200), ("SKU", 90), ("Categoria", 130)):
            self.recipe_list_tree.heading(text, text=text)
            self.recipe_list_tree.column(text, width=width)
        self.recipe_list_tree.grid(row=1, column=0, sticky="nsew")
        recipe_list_scroll = ttk.Scrollbar(right, orient="vertical", command=self.recipe_list_tree.yview)
        self.recipe_list_tree.configure(yscrollcommand=recipe_list_scroll.set)
        recipe_list_scroll.grid(row=1, column=1, sticky="ns")
        self.recipe_list_tree.bind("<Double-1>", self._on_recipe_list_double_click)
        self._setup_sortable_tree(self.recipe_list_tree)

        search_row = tk.Frame(right)
        search_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.recipe_search_var = tk.StringVar(value="")
        self.recipe_search_entry = tk.Entry(search_row, textvariable=self.recipe_search_var, width=18)
        self.recipe_search_entry.grid(row=0, column=0, sticky="w")
        self.recipe_search_var.trace_add("write", self._on_recipe_search_changed)
        self.recipe_search_entry.bind("<Up>", self._recipe_search_prev)
        self.recipe_search_entry.bind("<Down>", self._recipe_search_next)
        tk.Button(search_row, text="▲", command=self._recipe_search_prev, width=3).grid(row=0, column=1, padx=2)
        tk.Button(search_row, text="▼", command=self._recipe_search_next, width=3).grid(row=0, column=2, padx=2)
        tk.Button(search_row, text="✕", command=self._clear_recipe_search, width=3).grid(row=0, column=3, padx=2)

        self._ensure_recipe_ingredient_blank_row()
        self._focus_first_recipe_ingredient_cell()
        self._build_prepared_mixture_history(frame)

    def _build_prepared_mixture_history(self, frame):
        history_frame = tk.Frame(frame)
        history_frame.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 12))
        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(1, weight=1)
        tk.Label(history_frame, text="Historial de receptes preparades", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4),
        )
        self.prepared_mixture_tree = ttk.Treeview(
            history_frame, columns=("Nom", "SKU", "LOT", "Caducitat", "Data de preparació"), show="headings", height=6,
        )
        for column, width in (("Nom", 300), ("SKU", 100), ("LOT", 120), ("Caducitat", 120), ("Data de preparació", 180)):
            self.prepared_mixture_tree.heading(column, text=column)
            self.prepared_mixture_tree.column(column, width=width)
        self.prepared_mixture_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(history_frame, orient="vertical", command=self.prepared_mixture_tree.yview)
        self.prepared_mixture_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self._setup_sortable_tree(self.prepared_mixture_tree)
        self.prepared_mixture_tree.bind("<Return>", self._on_prepared_mixture_select)
        self.prepared_mixture_tree.bind("<Double-1>", self._on_prepared_mixture_select)
        quantity_row = tk.Frame(history_frame)
        quantity_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Label(quantity_row, text="Quantitat", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self.prepared_mixture_quantity_entry = tk.Entry(quantity_row, width=12)
        self.prepared_mixture_quantity_entry.pack(side=tk.LEFT, padx=(8, 12))
        self.prepared_mixture_quantity_entry.bind("<Return>", self._on_package_prepared_mixture)
        tk.Button(quantity_row, text="Imprimir i registrar envasat", command=self._on_package_prepared_mixture).pack(side=tk.LEFT)

    def populate_prepared_mixture_history(self):
        selection = self.prepared_mixture_tree.selection()
        self.prepared_mixture_tree.delete(*self.prepared_mixture_tree.get_children())
        self._prepared_mixture_rows = {}
        for mixture in list_prepared_mixtures(self.conn):
            iid = str(mixture["id"])
            self._prepared_mixture_rows[iid] = mixture
            self.prepared_mixture_tree.insert(
                "", "end", iid=iid,
                values=(mixture["name"], mixture["sku"], mixture["lot"], mixture["expiry"], mixture["created_at"]),
            )
        for iid in selection:
            if iid in self._prepared_mixture_rows:
                self.prepared_mixture_tree.selection_set(iid)
        self.prepared_mixture_quantity_entry.delete(0, tk.END)

    def _on_prepared_mixture_select(self, event=None):
        if self.prepared_mixture_tree.selection():
            self.prepared_mixture_quantity_entry.focus_set()
            self.prepared_mixture_quantity_entry.select_range(0, tk.END)
        return "break"

    def _on_package_prepared_mixture(self, event=None):
        selection = self.prepared_mixture_tree.selection()
        mixture = self._prepared_mixture_rows.get(selection[0]) if selection else None
        if mixture is None:
            messagebox.showwarning("Barreja no seleccionada", "Selecciona una preparació de la llista.")
            return "break"
        try:
            quantity = int(self.prepared_mixture_quantity_entry.get().strip())
            if quantity <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Quantitat invàlida", "La quantitat ha de ser un número enter positiu.")
            self.prepared_mixture_quantity_entry.focus_set()
            return "break"
        self._record_prepared_mixture_packaging(mixture, quantity)
        self.prepared_mixture_quantity_entry.delete(0, tk.END)
        self.prepared_mixture_quantity_entry.focus_set()
        return "break"

    _RECIPE_ING_COLUMNS = ("SKU", "Nom", "Quantitat")
    _RECIPE_ING_PLACEHOLDERS = {"Nom": "Nom o SKU...", "SKU": "SKU", "Quantitat": "Quantitat"}
    _RECIPE_ING_ARROW_DELTAS = {"Up": (-1, 0), "Down": (1, 0), "Left": (0, -1), "Right": (0, 1)}

    def _focus_first_recipe_ingredient_cell(self):
        children = self.recipe_ingredients_tree.get_children()
        if children:
            self._set_recipe_ing_active_cell(children[0], "Nom")
        else:
            self._set_recipe_ing_active_cell(None, None)

    def _is_recipe_ing_row_placeholder(self, row_iid):
        return "placeholder" in self.recipe_ingredients_tree.item(row_iid, "tags")

    def _write_recipe_ingredient_cell(self, row_iid, column_name, value):
        """Escriu un valor real a una cel·la, convertint la fila d'ajuda (placeholder) en una fila de dades."""
        tree = self.recipe_ingredients_tree
        if self._is_recipe_ing_row_placeholder(row_iid):
            tree.item(row_iid, tags=())
            for col in self._RECIPE_ING_COLUMNS:
                if col != column_name:
                    tree.set(row_iid, col, "")
        tree.set(row_iid, column_name, value)

    def _autofill_recipe_ingredient_row(self, row_iid):
        """Completa el nom o el SKU d'una fila a partir del catàleg quan només s'ha indicat l'altre."""
        tree = self.recipe_ingredients_tree
        if self._is_recipe_ing_row_placeholder(row_iid):
            return
        name = tree.set(row_iid, "Nom")
        sku = tree.set(row_iid, "SKU")
        if sku and not name:
            row = get_catalog_row_by_sku(self.conn, sku)
            if row:
                tree.set(row_iid, "Nom", row["name"])
        elif name and not sku:
            row = get_catalog_row_by_name(self.conn, name)
            if row and row["sku"]:
                tree.set(row_iid, "SKU", row["sku"])

    def _ensure_recipe_ingredient_blank_row(self):
        """Garanteix que sempre hi hagi una fila d'ajuda buida al final, com les files noves d'un full de càlcul."""
        tree = self.recipe_ingredients_tree
        children = tree.get_children()
        if children and self._is_recipe_ing_row_placeholder(children[-1]):
            return
        placeholders = self._RECIPE_ING_PLACEHOLDERS
        tree.insert(
            "", "end",
            values=(placeholders["SKU"], placeholders["Nom"], placeholders["Quantitat"]),
            tags=("placeholder",),
        )

    def _set_recipe_ing_active_cell(self, row_iid, column_name):
        self._recipe_ing_active_cell = (row_iid, column_name)
        self._update_recipe_ing_cell_highlight()

    def _update_recipe_ing_cell_highlight(self):
        """Dibuixa un requadre blau sobre la cel·la activa, com la cel·la seleccionada d'un full de càlcul."""
        tree = self.recipe_ingredients_tree
        if self._recipe_ing_highlight_frames is None:
            color = "#2f7ed8"
            self._recipe_ing_highlight_frames = {
                "top": tk.Frame(tree, bg=color),
                "bottom": tk.Frame(tree, bg=color),
                "left": tk.Frame(tree, bg=color),
                "right": tk.Frame(tree, bg=color),
            }
        frames = self._recipe_ing_highlight_frames
        row_iid, column_name = self._recipe_ing_active_cell
        if not row_iid or row_iid not in tree.get_children():
            for frame in frames.values():
                frame.place_forget()
            return
        # Després d'inserir/esborrar files cal forçar el recalcul de geometria abans de llegir el bbox.
        tree.update_idletasks()
        bbox = tree.bbox(row_iid, column_name)
        if not bbox:
            for frame in frames.values():
                frame.place_forget()
            return
        x, y, width, height = bbox
        thickness = 2
        frames["top"].place(x=x, y=y, width=width, height=thickness)
        frames["bottom"].place(x=x, y=y + height - thickness, width=width, height=thickness)
        frames["left"].place(x=x, y=y, width=thickness, height=height)
        frames["right"].place(x=x + width - thickness, y=y, width=thickness, height=height)

    def _on_recipe_ingredient_click(self, event):
        tree = self.recipe_ingredients_tree
        tree.focus_set()
        if tree.identify("region", event.x, event.y) != "cell":
            return
        row_iid = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        try:
            column_name = self._RECIPE_ING_COLUMNS[int(column_id.replace("#", "")) - 1]
        except (IndexError, ValueError):
            return
        if not row_iid:
            return
        self._set_recipe_ing_active_cell(row_iid, column_name)

    def _on_recipe_ingredients_paste(self, event=None):
        with self._action("Receptes: enganxar ingredients"):
            return self._on_recipe_ingredients_paste_impl(event)

    def _on_recipe_ingredients_paste_impl(self, event=None):
        try:
            clipboard = self.clipboard_get()
        except tk.TclError:
            return "break"
        lines = [line for line in clipboard.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line != ""]
        if not lines:
            return "break"

        tree = self.recipe_ingredients_tree
        columns = self._RECIPE_ING_COLUMNS
        start_row_iid, start_column = self._recipe_ing_active_cell
        start_col_index = columns.index(start_column) if start_column in columns else 0
        rows = list(tree.get_children())
        start_row_index = rows.index(start_row_iid) if start_row_iid in rows else max(len(rows) - 1, 0)

        touched_rows = []
        for offset, line in enumerate(lines):
            cells = line.split("\t")
            row_index = start_row_index + offset
            if row_index < len(rows):
                row_iid = rows[row_index]
            else:
                row_iid = tree.insert("", "end", values=("", "", ""))
                rows.append(row_iid)
            for col_offset, raw_value in enumerate(cells):
                col_index = start_col_index + col_offset
                if col_index >= len(columns):
                    break
                value = raw_value.strip()
                if columns[col_index] == "Quantitat" and value:
                    try:
                        value = float(value.replace(",", "."))
                    except ValueError:
                        pass
                self._write_recipe_ingredient_cell(row_iid, columns[col_index], value)
            touched_rows.append(row_iid)

        for row_iid in touched_rows:
            self._autofill_recipe_ingredient_row(row_iid)
        self._ensure_recipe_ingredient_blank_row()
        self._update_recipe_ing_cell_highlight()
        return "break"

    def _on_recipe_ingredient_key(self, event):
        """Gestiona la navegació de teclat quan hi ha una cel·la activa però no s'està editant."""
        if event.keysym in self._RECIPE_ING_ARROW_DELTAS:
            delta_row, delta_col = self._RECIPE_ING_ARROW_DELTAS[event.keysym]
            self._move_recipe_ing_active_cell(delta_row, delta_col)
            return "break"
        row_iid, column_name = self._recipe_ing_active_cell
        if event.keysym == "Tab":
            if row_iid:
                if event.state & 0x1:
                    self._retreat_recipe_ing_after_shift_tab(row_iid, column_name)
                else:
                    self._advance_recipe_ing_after_tab(row_iid, column_name)
            return "break"
        if event.keysym == "Return":
            if row_iid:
                self._advance_recipe_ing_after_enter(row_iid)
            return "break"
        # Escriure directament sobre la cel·la activa hi comença l'edició (com un full de càlcul).
        if event.state & 0x4 or not event.char or not event.char.isprintable():
            return
        if not row_iid or row_iid not in self.recipe_ingredients_tree.get_children():
            return
        self._begin_recipe_ingredient_edit(row_iid, column_name, initial_text=event.char)
        return "break"

    def _move_recipe_ing_active_cell(self, delta_row, delta_col):
        tree = self.recipe_ingredients_tree
        rows = list(tree.get_children())
        if not rows:
            return
        row_iid, column_name = self._recipe_ing_active_cell
        row_index = rows.index(row_iid) if row_iid in rows else 0
        columns = self._RECIPE_ING_COLUMNS
        col_index = columns.index(column_name) if column_name in columns else 0
        row_index = min(max(row_index + delta_row, 0), len(rows) - 1)
        col_index = min(max(col_index + delta_col, 0), len(columns) - 1)
        self._set_recipe_ing_active_cell(rows[row_index], columns[col_index])

    def _advance_recipe_ing_after_tab(self, row_iid, column_name):
        """Tab: de Nom/SKU passa a Quantitat (mateixa fila); de Quantitat passa al Nom de la fila següent."""
        tree = self.recipe_ingredients_tree
        if row_iid not in tree.get_children():
            return
        if column_name == "Quantitat":
            self._advance_recipe_ing_after_enter(row_iid)
        else:
            self._set_recipe_ing_active_cell(row_iid, "Quantitat")

    def _retreat_recipe_ing_after_shift_tab(self, row_iid, column_name):
        """Maj+Tab: passa a la cel·la anterior; de la primera columna, a l'última de la fila anterior."""
        tree = self.recipe_ingredients_tree
        rows = list(tree.get_children())
        if row_iid not in rows:
            return
        columns = self._RECIPE_ING_COLUMNS
        col_index = columns.index(column_name) if column_name in columns else 0
        row_index = rows.index(row_iid)
        if col_index > 0:
            self._set_recipe_ing_active_cell(row_iid, columns[col_index - 1])
        elif row_index > 0:
            self._set_recipe_ing_active_cell(rows[row_index - 1], columns[-1])

    def _advance_recipe_ing_after_enter(self, row_iid):
        """Intro sempre selecciona la cel·la Nom de la fila següent."""
        tree = self.recipe_ingredients_tree
        rows = list(tree.get_children())
        if row_iid in rows and rows.index(row_iid) + 1 < len(rows):
            self._set_recipe_ing_active_cell(rows[rows.index(row_iid) + 1], "Nom")
        else:
            self._focus_first_recipe_ingredient_cell()

    def _on_recipe_ingredient_double_click(self, event):
        tree = self.recipe_ingredients_tree
        if tree.identify("region", event.x, event.y) != "cell":
            return
        row_iid = tree.identify_row(event.y)
        column_id = tree.identify_column(event.x)
        if not row_iid or not column_id:
            return
        try:
            column_name = self._RECIPE_ING_COLUMNS[int(column_id.replace("#", "")) - 1]
        except (IndexError, ValueError):
            return
        self._begin_recipe_ingredient_edit(row_iid, column_name)

    def _begin_recipe_ingredient_edit(self, row_iid, column_name, initial_text=None):
        tree = self.recipe_ingredients_tree
        bbox = tree.bbox(row_iid, column_name)
        if not bbox:
            return
        self._set_recipe_ing_active_cell(row_iid, column_name)
        x, y, width, height = bbox
        is_placeholder = self._is_recipe_ing_row_placeholder(row_iid)
        current_value = "" if is_placeholder else tree.set(row_iid, column_name)

        editor = tk.Entry(tree)
        if initial_text is not None:
            editor.insert(0, initial_text)
        else:
            editor.insert(0, current_value)
            editor.select_range(0, tk.END)
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.icursor(tk.END)

        def commit():
            new_value = editor.get().strip()
            self._hide_suggestions()
            editor.destroy()
            tree.focus_set()
            self._apply_recipe_ingredient_edit(row_iid, column_name, new_value)

        def cancel(event=None):
            self._hide_suggestions()
            editor.destroy()
            tree.focus_set()

        def on_return(event=None):
            commit()
            self._advance_recipe_ing_after_enter(row_iid)
            return "break"

        def on_tab(event=None):
            commit()
            self._advance_recipe_ing_after_tab(row_iid, column_name)
            return "break"

        def on_shift_tab(event=None):
            commit()
            self._retreat_recipe_ing_after_shift_tab(row_iid, column_name)
            return "break"

        def on_focus_out(event=None):
            # Si el focus ha anat cap a la llista de suggeriments, no es cancel·la l'edició.
            def check():
                if not editor.winfo_exists():
                    return
                if self.focus_get() is self.suggestions_listbox:
                    return
                cancel()

            self.after_idle(check)

        editor.bind("<Return>", on_return)
        editor.bind("<Tab>", on_tab)
        editor.bind("<Shift-Tab>", on_shift_tab)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", on_focus_out)

        if column_name == "Nom":
            editor.bind("<KeyRelease>", lambda e: self._on_recipe_ingredient_editor_keyrelease(editor, row_iid, column_name, e))
            editor.bind("<Down>", lambda e: self._on_suggestions_down_from(editor))

    def _on_recipe_ingredient_editor_keyrelease(self, editor, row_iid, column_name, event=None):
        if event is not None and event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = editor.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_catalog_products(self.conn, text)
        if not matches:
            self._hide_suggestions()
            return

        labels = [f"{row['name']}   [{row['sku']}]" if row["sku"] else row["name"] for row in matches]

        def apply_suggestion(chosen):
            # Triar un suggeriment només desa el nom (i l'SKU, autoemplenat); l'usuari ja decidirà
            # amb Tab/Intro/fletxes cap a on continuar, com li passaria si l'hagués escrit a mà.
            self._hide_suggestions()
            editor.destroy()
            tree = self.recipe_ingredients_tree
            tree.focus_set()
            self._apply_recipe_ingredient_edit(row_iid, column_name, chosen["name"])
            self._set_recipe_ing_active_cell(row_iid, column_name)

        self._show_suggestions(editor, matches, labels, apply_suggestion)

    def _apply_recipe_ingredient_edit(self, row_iid, column_name, new_value):
        with self._action(f"Receptes: editar {column_name} d'un ingredient"):
            self._apply_recipe_ingredient_edit_impl(row_iid, column_name, new_value)

    def _apply_recipe_ingredient_edit_impl(self, row_iid, column_name, new_value):
        tree = self.recipe_ingredients_tree
        if not new_value and self._is_recipe_ing_row_placeholder(row_iid):
            return
        if column_name == "Quantitat":
            if new_value:
                try:
                    value = float(new_value.replace(",", "."))
                except ValueError:
                    messagebox.showerror("Quantitat invàlida", f"'{new_value}' no és un número vàlid.")
                    return
                self._write_recipe_ingredient_cell(row_iid, column_name, value)
            else:
                self._write_recipe_ingredient_cell(row_iid, column_name, "")
        else:
            self._write_recipe_ingredient_cell(row_iid, column_name, new_value)
            self._autofill_recipe_ingredient_row(row_iid)
        self._ensure_recipe_ingredient_blank_row()
        self._update_recipe_ing_cell_highlight()

    def _on_recipe_ingredient_delete(self, event):
        tree = self.recipe_ingredients_tree
        row_iid, _ = self._recipe_ing_active_cell
        if row_iid and row_iid in tree.get_children() and not self._is_recipe_ing_row_placeholder(row_iid):
            with self._action("Receptes: esborrar ingredient"):
                tree.delete(row_iid)
        self._ensure_recipe_ingredient_blank_row()
        self._focus_first_recipe_ingredient_cell()
        return "break"

    def _on_recipe_product_keyrelease(self, event):
        if event.keysym in ("Up", "Down", "Tab", "Return", "Escape"):
            return
        text = self.recipe_product_entry.get().strip()
        if not text:
            self._hide_suggestions()
            return
        matches = search_catalog_products(self.conn, text)
        if not matches:
            self._hide_suggestions()
            return

        labels = [f"{row['name']}   [{row['sku']}]" if row["sku"] else row["name"] for row in matches]

        def apply_recipe_product_suggestion(chosen):
            self._select_recipe_product({"name": chosen["name"], "sku": chosen["sku"]})

        self._show_suggestions(self.recipe_product_entry, matches, labels, apply_recipe_product_suggestion)

    def _select_recipe_product(self, product):
        existing = get_recipe_by_sku(self.conn, product.get("sku")) if product.get("sku") else None
        if existing:
            self._load_recipe_into_form(existing["recipe"], existing["ingredients"])
        else:
            self._start_new_recipe_form(product)

    def _start_new_recipe_form(self, product):
        self.recipe_product_entry.delete(0, tk.END)
        self.recipe_product_entry.insert(0, product["name"])
        self._hide_suggestions()
        self.current_recipe_id = None
        self.current_recipe_sku = product.get("sku") or ""
        self.current_recipe_product_name = product["name"]
        self.recipe_ingredients_tree.delete(*self.recipe_ingredients_tree.get_children())
        self._recipe_ing_active_cell = (None, None)
        self._ensure_recipe_ingredient_blank_row()
        self._focus_first_recipe_ingredient_cell()
        self.recipe_save_button.config(text="Afegir recepta")

    def _load_recipe_into_form(self, recipe, ingredients):
        self.recipe_product_entry.delete(0, tk.END)
        self.recipe_product_entry.insert(0, recipe["name"])
        self._hide_suggestions()
        self.current_recipe_id = recipe["id"]
        self.current_recipe_sku = recipe["sku"] or ""
        self.current_recipe_product_name = recipe["name"]
        self.recipe_ingredients_tree.delete(*self.recipe_ingredients_tree.get_children())
        for ing in ingredients:
            self.recipe_ingredients_tree.insert(
                "", "end", values=(ing["sku"] or "", ing["name"], "" if ing["quantity"] is None else ing["quantity"])
            )
        self._ensure_recipe_ingredient_blank_row()
        self._focus_first_recipe_ingredient_cell()
        self.recipe_save_button.config(text="Actualitzar recepta")

    def _on_new_recipe_clicked(self):
        self.recipe_product_entry.delete(0, tk.END)
        self._hide_suggestions()
        self.current_recipe_id = None
        self.current_recipe_sku = ""
        self.current_recipe_product_name = ""
        self.recipe_ingredients_tree.delete(*self.recipe_ingredients_tree.get_children())
        self._recipe_ing_active_cell = (None, None)
        self._ensure_recipe_ingredient_blank_row()
        self._focus_first_recipe_ingredient_cell()
        self.recipe_save_button.config(text="Afegir recepta")
        self.recipe_product_entry.focus_set()

    def _on_save_recipe_clicked(self):
        product_text = self.recipe_product_entry.get().strip()
        if not product_text:
            messagebox.showwarning("Producte no indicat", "Selecciona un producte del catàleg per a la recepta.")
            self.recipe_product_entry.focus_set()
            return
        name = self.current_recipe_product_name or product_text
        sku = self.current_recipe_sku or get_catalog_sku_by_name(self.conn, product_text) or ""
        rows = self.recipe_ingredients_tree.get_children()
        ingredients = []
        for iid in rows:
            if self._is_recipe_ing_row_placeholder(iid):
                continue
            ing_sku, ing_name, ing_qty = self.recipe_ingredients_tree.item(iid, "values")
            if not ing_name and not ing_sku:
                continue
            quantity = None
            if ing_qty not in ("", None):
                try:
                    quantity = float(ing_qty)
                except (TypeError, ValueError):
                    quantity = None
            ingredients.append({"name": ing_name, "sku": ing_sku, "quantity": quantity})
        if not ingredients:
            messagebox.showwarning("Sense ingredients", "Afegeix almenys un ingredient a la recepta.")
            return
        with self._action(f"Receptes: desar recepta '{name}'"):
            recipe_id = upsert_recipe(self.conn, sku, name, ingredients, recipe_id=self.current_recipe_id)
        self.current_recipe_id = recipe_id
        self.current_recipe_sku = sku
        self.current_recipe_product_name = name
        self.populate_recipes_list()
        if hasattr(self, "package_recipe_lookup_tree"):
            self._populate_package_recipe_lookup()
        self._on_new_recipe_clicked()
        if self.recipe_list_tree.exists(str(recipe_id)):
            self.recipe_list_tree.see(str(recipe_id))

    def _on_recipe_list_double_click(self, event):
        selection = self.recipe_list_tree.selection()
        if not selection:
            return
        data = get_recipe_by_id(self.conn, int(selection[0]))
        if data is None:
            return
        self._load_recipe_into_form(data["recipe"], data["ingredients"])

    def populate_recipes_list(self):
        for item in self.recipe_list_tree.get_children():
            self.recipe_list_tree.delete(item)
        for row in sorted(list_recipes(self.conn), key=lambda recipe: self._text_sort_key(recipe["name"])):
            category = ""
            if row["sku"]:
                catalog_row = get_catalog_row_by_sku(self.conn, row["sku"])
                if catalog_row is not None:
                    category = catalog_row["category"] or ""
            self.recipe_list_tree.insert("", "end", iid=str(row["id"]), values=(row["name"], row["sku"] or "", category))
        self._reapply_tree_sort(self.recipe_list_tree)
        self._on_recipe_search_changed()

    def _on_recipe_search_changed(self, *_args):
        term = self.recipe_search_var.get().strip().lower()
        self._recipe_search_matches = []
        self._recipe_search_index = -1
        if not term:
            self.recipe_list_tree.selection_remove(*self.recipe_list_tree.selection())
            return
        for iid in self.recipe_list_tree.get_children():
            values = [str(value).lower() for value in self.recipe_list_tree.item(iid, "values")]
            if any(term in value for value in values):
                self._recipe_search_matches.append(iid)
        if self._recipe_search_matches:
            self._recipe_search_index = 0
            self._select_recipe_search_match()
        else:
            self.recipe_list_tree.selection_remove(*self.recipe_list_tree.selection())

    def _select_recipe_search_match(self):
        if not self._recipe_search_matches:
            return
        iid = self._recipe_search_matches[self._recipe_search_index]
        self.recipe_list_tree.selection_set(iid)
        self.recipe_list_tree.see(iid)
        self.recipe_list_tree.focus(iid)

    def _recipe_search_next(self, event=None):
        if not self._recipe_search_matches:
            return "break"
        self._recipe_search_index = (self._recipe_search_index + 1) % len(self._recipe_search_matches)
        self._select_recipe_search_match()
        return "break"

    def _recipe_search_prev(self, event=None):
        if not self._recipe_search_matches:
            return "break"
        self._recipe_search_index = (self._recipe_search_index - 1) % len(self._recipe_search_matches)
        self._select_recipe_search_match()
        return "break"

    def _clear_recipe_search(self):
        self.recipe_search_var.set("")
        self.recipe_search_entry.focus_set()

    def build_historial_tab(self):
        frame = self.historial_tab
        tk.Label(frame, text="Cerca per lot proveïdor, lot d'envasat, nom, data d'envasat o caducitat", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=(12, 4))
        self.search_var = tk.Entry(frame, width=50)
        self.search_var.grid(row=0, column=1, padx=10, pady=(12, 4), sticky="w")
        self.search_var.bind("<Return>", lambda event: self.refresh_history())
        tk.Button(frame, text="Buscar", command=self.refresh_history, width=12).grid(row=0, column=2, padx=10, pady=(12, 4), sticky="w")

        tk.Button(frame, text="Exportar sessió actual", command=self.export_session, width=22, bg="#5a4db5", fg="white").grid(row=1, column=1, sticky="w", padx=10, pady=8)

        self.history_tree = ttk.Treeview(frame, columns=("Nom", "Lot envasat", "Lot proveïdor", "Caducitat", "Origen", "Quantitat"), show="headings")
        for text in ("Nom", "Lot envasat", "Lot proveïdor", "Caducitat", "Origen", "Quantitat"):
            self.history_tree.heading(text, text=text)
            self.history_tree.column(text, width=180)
        self.history_tree.grid(row=2, column=0, columnspan=3, padx=10, pady=10, sticky="nsew")
        self._attach_scrollbars(self.history_tree, horizontal=True)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=1)

    def refresh_materials(self):
        materials = list_raw_materials(self.conn)
        self.populate_received_table(materials)

    def populate_received_table(self, materials):
        for row in self.received_tree.get_children():
            self.received_tree.delete(row)
        for row in materials[:20]:
            sku = get_catalog_sku_by_name(self.conn, row["name"]) or ""
            self.received_tree.insert(
                "", "end",
                values=(row["name"], row["supplier"], sku, row["supplier_lot"], row["provenance"], row["expiry"], row["barcode"], row["created_at"]),
            )

    def _register_received_material(self, name, supplier, supplier_lot, provenance, expiry):
        expiry_date = parse_date(expiry)
        sku = get_catalog_sku_by_name(self.conn, name)
        if not sku:
            raise ValueError(
                f"No hi ha cap SKU relacionat amb '{name}' al catàleg importat. Importa'l a la pestanya Productes abans de continuar."
            )
        today_prefix = f"{date.today().strftime('%Y%m%d')}{sku}"
        sequence = count_barcodes_with_prefix(self.conn, today_prefix) + 1
        if sequence > 99:
            raise ValueError(f"Ja s'han registrat 99 entrades avui per '{name}'. No es pot generar un nou codi de barres.")
        barcode = generate_barcode_from_sku(sku, sequence=sequence)
        expiry = expiry_date.isoformat() if expiry_date else ""
        with self._action(f"Recepció: {name} (lot {supplier_lot})"):
            add_raw_material(self.conn, name, supplier, supplier_lot, provenance, expiry, barcode)
        return get_material_by_barcode(self.conn, barcode)

    def save_received_material(self, should_print: bool = True):
        name = self.raw_name.get().strip()
        supplier = self.raw_supplier.get().strip()
        supplier_lot = self.raw_supplier_lot.get().strip() or generate_packed_lot("PROV")
        provenance = self.raw_provenance.get().strip()
        expiry_text = self.raw_expiry.get().strip()
        if not name:
            messagebox.showwarning("Dades incompletes", "Cal indicar el nom.")
            self.raw_name.focus_set()
            return

        try:
            material = self._register_received_material(name, supplier, supplier_lot, provenance, expiry_text)
        except ValueError as exc:
            messagebox.showerror("Recepció no guardada", str(exc))
            return

        barcode = material["barcode"]
        expiry = material["expiry"]
        self._clear_received_form()
        self.refresh_materials()

        if not should_print:
            return

        try:
            label_path = create_label_image(name, supplier_lot, provenance, expiry, barcode, supplier=supplier)
            print_label_file(label_path, self.printer_name)
        except Exception as exc:
            messagebox.showwarning(
                "Etiqueta no generada",
                f"El producte s'ha guardat correctament, però hi ha hagut un problema generant l'etiqueta:\n{exc}",
            )
            return

        messagebox.showinfo("Dada registrada", f"Producte registrat: {name} ({supplier_lot})")

    def _clear_received_form(self):
        self.raw_name.delete(0, tk.END)
        self.raw_supplier.delete(0, tk.END)
        self.raw_supplier_lot.delete(0, tk.END)
        self.raw_provenance.delete(0, tk.END)
        self.raw_expiry.delete(0, tk.END)
        self.raw_name_sku_var.set("")
        self._hide_suggestions()
        self.raw_name.focus_set()

    def refresh_history(self):
        term = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        if term:
            rows = search_packaged(self.conn, term)
        else:
            rows = list_packaged_products(self.conn)

        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for row in rows:
            self.history_tree.insert(
                "",
                "end",
                values=(
                    row["name"],
                    row["packed_lot"],
                    row["supplier_lot"],
                    row["expiry"],
                    row["provenance"],
                    row["quantity"],
                ),
            )

    def export_session(self):
        export_dir = os.path.join("output", "sessions")
        os.makedirs(export_dir, exist_ok=True)
        target_path = os.path.join(export_dir, f"sessio_{self.session_id}.csv")
        count = export_session_csv(self.conn, self.session_id, target_path)
        messagebox.showinfo("Exportació completada", f"S'han exportat {count} registres a:\n{target_path}")

    # ---- Desfer / refer ----

    def show_history_window(self):
        window = self.history_window
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.focus_set()
            self._refresh_history_view()
            return
        window = tk.Toplevel(self)
        window.title("Historial d'accions")
        window.geometry("760x420")
        window.minsize(480, 260)
        self.history_window = window
        window.protocol("WM_DELETE_WINDOW", self._close_history_window)
        window.bind("<Escape>", lambda event: self._close_history_window())
        self._build_history_window_contents(window)
        self._refresh_history_view()

    def _close_history_window(self):
        window = self.history_window
        self.history_window = None
        self.actions_tree = None
        if window is not None:
            window.destroy()

    def _build_history_window_contents(self, frame):
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        button_row = tk.Frame(frame)
        button_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(12, 4))
        self.undo_button = tk.Button(button_row, text="Desfer", width=12, command=self.undo_action)
        self.undo_button.pack(side="left")
        self.redo_button = tk.Button(button_row, text="Refer", width=12, command=self.redo_action)
        self.redo_button.pack(side="left", padx=(8, 0))

        tk.Label(
            frame,
            text="Fes doble clic a una acció per desfer-la i totes les posteriors (es recupera l'estat del programa i de la "
            "base de dades d'abans d'aquella acció). Les accions desfetes es poden refer fins que en facis una de nova. "
            "L'historial s'esborra en tancar la sessió.",
            font=("Segoe UI", 8),
            fg="#555555",
            justify="left",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 6))

        tree_frame = tk.Frame(frame)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 12))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)
        self.actions_tree = ttk.Treeview(tree_frame, columns=("Hora", "Acció", "Estat"), show="headings")
        for text, width in (("Hora", 90), ("Acció", 650), ("Estat", 110)):
            self.actions_tree.heading(text, text=text)
            self.actions_tree.column(text, width=width, stretch=(text == "Acció"))
        self.actions_tree.tag_configure("undone", foreground="#999999", font=("Segoe UI", 9, "italic"))
        self.actions_tree.tag_configure("discarded", foreground="#bbbbbb", font=("Segoe UI", 9, "overstrike"))
        self.actions_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.actions_tree.yview)
        self.actions_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.actions_tree.bind("<Double-1>", self._on_action_double_click)
        self._history_rows: dict = {}
        self._refresh_history_view()

    def _refresh_history_view(self):
        if self.history_window is None or self.actions_tree is None:
            return
        tree = self.actions_tree
        tree.delete(*tree.get_children())
        self._history_rows = {}
        for entry in self.history.entries:
            iid = str(entry.id)
            self._history_rows[iid] = entry
            tags = ()
            if entry.status == UNDONE:
                tags = ("undone",)
            elif entry.status == DISCARDED:
                tags = ("discarded",)
            tree.insert("", "end", iid=iid, values=(entry.timestamp, entry.description, entry.status.capitalize()), tags=tags)
        if self.history.entries:
            tree.see(str(self.history.entries[-1].id))
        self.undo_button.config(state="normal" if self.history.can_undo else "disabled")
        self.redo_button.config(state="normal" if self.history.can_redo else "disabled")

    def _on_action_double_click(self, event):
        iid = self.actions_tree.identify_row(event.y)
        entry = self._history_rows.get(iid)
        if entry is None:
            return
        position = self.history.position_of(entry)
        if position is None:
            messagebox.showinfo(
                "Acció descartada", "Aquesta acció es va descartar en fer una acció nova després de desfer-la.",
                parent=self.history_window,
            )
            return
        self._go_to_history_position(position)

    def undo_action(self):
        self._go_to_history_position(self.history.position - 1)

    def redo_action(self):
        self._go_to_history_position(self.history.position + 1)

    def _on_undo_shortcut(self, event=None):
        if self.grab_current() is None:
            self.undo_action()

    def _on_redo_shortcut(self, event=None):
        if self.grab_current() is None:
            self.redo_action()

    def _go_to_history_position(self, position):
        snapshot = self.history.move_to(position, self._capture_snapshot)
        if snapshot is None:
            return
        self._restore_snapshot(snapshot)
        self._refresh_history_view()

    @contextmanager
    def _action(self, description):
        """Registra a l'historial l'acció executada dins el bloc si canvia la BD o l'estat del formulari.

        Les accions niuades (p. ex. una acció que n'executa d'altres) compten com una de sola.
        """
        if self._action_depth or self.conn is None:
            yield
            return
        self._action_depth += 1
        try:
            before = self._capture_snapshot()
            changes_before = self.conn.total_changes
            yield
        finally:
            self._action_depth -= 1
        if self.conn.total_changes != changes_before or self._capture_ui_state() != before.ui:
            self.history.record(description, before)
            self._refresh_history_view()

    def _capture_snapshot(self) -> Snapshot:
        self.conn.commit()
        changes = self.conn.total_changes
        cache = self._db_snapshot_cache
        if cache is None or cache[0] != changes:
            cache = (changes, zlib.compress(self.conn.serialize(), 1))
            self._db_snapshot_cache = cache
        return Snapshot(db=cache[1], ui=self._capture_ui_state())

    def _restore_snapshot(self, snapshot: Snapshot):
        self.conn.commit()
        memory_db = sqlite3.connect(":memory:")
        try:
            memory_db.deserialize(zlib.decompress(snapshot.db))
            memory_db.backup(self.conn)
        finally:
            memory_db.close()
        self._db_snapshot_cache = None
        self._action_depth += 1
        try:
            self._refresh_all_views()
            self._apply_ui_state(snapshot.ui)
        finally:
            self._action_depth -= 1

    def _refresh_all_views(self):
        self.refresh_materials()
        self.refresh_history()
        self.populate_package_history()
        self.refresh_product_catalog_table()
        self.populate_recipes_list()
        self.populate_prepared_mixture_history()
        viewing = self.package_viewing_recipe
        if viewing is not None and get_recipe_by_id(self.conn, viewing["id"]) is not None:
            self._open_package_recipe_view(viewing["id"])
        else:
            self._close_package_recipe_view()

    def _capture_ui_state(self) -> dict:
        recipe_tree = self.recipe_ingredients_tree
        return {
            "package": {
                "ingredients": copy.deepcopy(self.package_ingredients),
                "final_product": copy.deepcopy(self.package_final_product),
                "final_auto": self.package_final_product_auto,
                "reuse_lot": self.package_reuse_lot,
                "mixture_id": self.package_prepared_mixture_id,
                "final_text": self.package_final_product_entry.get(),
                "quantity_text": self.package_quantity_entry.get(),
            },
            "recipe": {
                "id": self.current_recipe_id,
                "sku": self.current_recipe_sku,
                "name": self.current_recipe_product_name,
                "product_text": self.recipe_product_entry.get(),
                "button_text": self.recipe_save_button.cget("text"),
                "rows": [
                    (tuple(recipe_tree.item(iid, "values")), tuple(recipe_tree.item(iid, "tags")))
                    for iid in recipe_tree.get_children()
                ],
            },
        }

    def _apply_ui_state(self, state: dict):
        package = state["package"]
        self.package_ingredients = copy.deepcopy(package["ingredients"])
        self.ingredients_list.delete(0, tk.END)
        for ingredient in self.package_ingredients:
            sku_part = ingredient.get("sku") or "sense SKU"
            self.ingredients_list.insert(
                tk.END,
                f"{ingredient['name']}  [{sku_part}]  Lot:{ingredient['supplier_lot']}  Caduca:{ingredient['expiry']}",
            )
        self.package_final_product = copy.deepcopy(package["final_product"])
        self.package_final_product_auto = package["final_auto"]
        self.package_reuse_lot = package["reuse_lot"]
        self.package_prepared_mixture_id = package["mixture_id"]
        self.package_final_product_entry.delete(0, tk.END)
        self.package_final_product_entry.insert(0, package["final_text"])
        self.package_quantity_entry.delete(0, tk.END)
        self.package_quantity_entry.insert(0, package["quantity_text"])
        self._refresh_package_final_product_weight()
        self._refresh_package_recipe_matching()
        self._hide_suggestions()
        self.package_status.set("Estat restaurat des de l'historial d'accions.")

        recipe = state["recipe"]
        self.current_recipe_id = recipe["id"]
        self.current_recipe_sku = recipe["sku"]
        self.current_recipe_product_name = recipe["name"]
        self.recipe_product_entry.delete(0, tk.END)
        self.recipe_product_entry.insert(0, recipe["product_text"])
        self.recipe_save_button.config(text=recipe["button_text"])
        recipe_tree = self.recipe_ingredients_tree
        recipe_tree.delete(*recipe_tree.get_children())
        for values, tags in recipe["rows"]:
            recipe_tree.insert("", "end", values=values, tags=tags)
        self._ensure_recipe_ingredient_blank_row()
        self._recipe_ing_active_cell = (None, None)
        self._focus_first_recipe_ingredient_cell()


def main():
    app = TraceabilityApp()
    app.mainloop()
