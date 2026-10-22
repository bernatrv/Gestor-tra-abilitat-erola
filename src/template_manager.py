from __future__ import annotations

import os
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.db import list_catalog_products
from src.label_templates import (
    ACTION_LABELS, FIELD_LABELS, assign_action_template, assign_product_template, configure_label_template, delete_label_template,
    import_label_template, load_template_config, product_template_key, set_templates_enabled, template_bytes,
)


FIXED_FIELD = "Fix (no modificar)"


class TemplateManager(tk.Toplevel):
    def __init__(self, app):
        self.config_data = load_template_config(app.conn)
        super().__init__(app)
        self.app = app
        self.title("Plantilles d'etiquetes")
        self.transient(app)
        self.geometry(f"{min(980, app.winfo_screenwidth() - 60)}x{min(620, app.winfo_screenheight() - 100)}")
        self.minsize(700, 420)
        self._template_id = None
        self._fields = {}
        self._edit_paths = {}
        self._template_choices = {}
        self.enabled = tk.BooleanVar(self, value=self.config_data["enabled"])
        ttk.Checkbutton(self, text="Utilitzar plantilles P-touch per als envasats", variable=self.enabled,
                        command=self._set_enabled).pack(anchor="w", padx=12, pady=10)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        template_panel = ttk.Frame(self.notebook, padding=8)
        product_panel = ttk.Frame(self.notebook, padding=8)
        action_panel = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(template_panel, text="Plantilles")
        self.notebook.add(product_panel, text="Productes")
        self.notebook.add(action_panel, text="Accions")
        self._build_templates(template_panel)
        self._build_products(product_panel)
        self._build_actions(action_panel)
        self._refresh_templates()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda event: self.destroy())
        self.update_idletasks()
        self.wait_visibility()
        self.update_idletasks()
        self.grab_set()

    def _run(self, description, operation):
        try:
            with self.app._action(description):
                return True, operation()
        except Exception as exc:
            messagebox.showerror("Plantilles d'etiquetes", str(exc), parent=self)
            return False, None

    def _set_enabled(self):
        success, _ = self._run("Plantilles: activar/desactivar", lambda: set_templates_enabled(self.app.conn, self.enabled.get()))
        if not success:
            self.enabled.set(not self.enabled.get())

    def _build_templates(self, panel):
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=2)
        panel.rowconfigure(1, weight=1)
        actions = ttk.Frame(panel)
        actions.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for label, command in (("Importar...", self._import), ("Substituir...", self._replace),
                               ("Editar a P-touch", self._edit), ("Recollir canvis", self._collect_edits),
                               ("Exportar...", self._export), ("Eliminar", self._delete)):
            ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 4))
        left = ttk.Frame(panel)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.templates_tree = ttk.Treeview(left, columns=("name", "file"), show="headings", selectmode="browse")
        self.templates_tree.heading("name", text="Plantilla")
        self.templates_tree.heading("file", text="Fitxer")
        self.templates_tree.column("name", width=160)
        self.templates_tree.column("file", width=180)
        self.templates_tree.grid(row=0, column=0, sticky="nsew")
        self.app._attach_scrollbars(self.templates_tree, horizontal=True)
        self.templates_tree.bind("<<TreeviewSelect>>", self._select_template)
        details = ttk.Frame(panel)
        details.grid(row=1, column=1, sticky="nsew")
        details.columnconfigure(1, weight=1)
        details.rowconfigure(2, weight=1)
        ttk.Label(details, text="Nom de plantilla").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        self.name = tk.StringVar(self)
        ttk.Entry(details, textvariable=self.name).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Button(details, text="Desar nom", command=self._save_details).grid(row=0, column=2, padx=(6, 0))
        field_panel = ttk.Frame(details)
        field_panel.grid(row=2, column=0, columnspan=3, sticky="nsew")
        field_panel.columnconfigure(0, weight=1)
        field_panel.rowconfigure(0, weight=1)
        self.fields_tree = ttk.Treeview(field_panel, columns=("object", "field"), show="headings", selectmode="browse")
        self.fields_tree.heading("object", text="Nom d'objecte P-touch")
        self.fields_tree.heading("field", text="Dada")
        self.fields_tree.column("object", width=180)
        self.fields_tree.column("field", width=200)
        self.fields_tree.grid(row=0, column=0, sticky="nsew")
        self.app._attach_scrollbars(self.fields_tree, horizontal=True)
        self.fields_tree.bind("<<TreeviewSelect>>", self._select_field)
        ttk.Label(details, text="Objecte").grid(row=3, column=0, sticky="w", pady=6)
        self.object_name = tk.StringVar(self)
        ttk.Entry(details, textvariable=self.object_name).grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(details, text="Dada").grid(row=4, column=0, sticky="w", pady=6)
        self.field_name = tk.StringVar(self, value=FIXED_FIELD)
        ttk.Combobox(details, textvariable=self.field_name, state="readonly",
                     values=[FIXED_FIELD, *FIELD_LABELS.values()]).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Button(details, text="Desar camp", command=self._save_field).grid(row=4, column=2, padx=(6, 0))

    def _build_products(self, panel):
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        search_row = ttk.Frame(panel)
        search_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text="Cerca").grid(row=0, column=0, padx=(0, 8))
        self.product_search = tk.StringVar(self)
        ttk.Entry(search_row, textvariable=self.product_search).grid(row=0, column=1, sticky="ew")
        self.product_search.trace_add("write", lambda *args: self._refresh_products())
        table = ttk.Frame(panel)
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.products_tree = ttk.Treeview(table, columns=("SKU", "Nom", "Plantilla"), show="headings", selectmode="extended")
        for column, width in (("SKU", 130), ("Nom", 320), ("Plantilla", 240)):
            self.products_tree.heading(column, text=column)
            self.products_tree.column(column, width=width)
        self.products_tree.grid(row=0, column=0, sticky="nsew")
        self.app._attach_scrollbars(self.products_tree, horizontal=True)
        self.app._setup_sortable_tree(self.products_tree)
        controls = ttk.Frame(panel)
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Plantilla d'envasat").grid(row=0, column=0, padx=(0, 8))
        self.product_template = tk.StringVar(self, value="Cap plantilla")
        self.product_combo = ttk.Combobox(controls, textvariable=self.product_template, state="readonly")
        self.product_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(controls, text="Assignar als seleccionats", command=self._assign).grid(row=0, column=2)

    def _build_actions(self, panel):
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        ttk.Label(panel, text="Les plantilles d'envasat s'assignen a cada producte des de la pestanya Productes.",
                  wraplength=620).grid(row=0, column=0, sticky="w", pady=(0, 8))
        table = ttk.Frame(panel)
        table.grid(row=1, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.actions_tree = ttk.Treeview(table, columns=("action", "template"), show="headings", selectmode="browse")
        self.actions_tree.heading("action", text="Acció")
        self.actions_tree.heading("template", text="Plantilla")
        self.actions_tree.column("action", width=260)
        self.actions_tree.column("template", width=300)
        self.actions_tree.grid(row=0, column=0, sticky="nsew")
        self.app._attach_scrollbars(self.actions_tree, horizontal=True)
        self.actions_tree.bind("<<TreeviewSelect>>", self._select_action)
        controls = ttk.Frame(panel)
        controls.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Plantilla").grid(row=0, column=0, padx=(0, 8))
        self.action_template = tk.StringVar(self, value="Cap plantilla")
        self.action_combo = ttk.Combobox(controls, textvariable=self.action_template, state="readonly")
        self.action_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(controls, text="Assignar a l'acció", command=self._assign_action).grid(row=0, column=2)

    def _refresh_actions(self):
        selected = self.actions_tree.selection()
        self.actions_tree.delete(*self.actions_tree.get_children())
        for action, label in ACTION_LABELS.items():
            identity = self.config_data["actions"].get(action)
            template = self.config_data["templates"].get(identity, {})
            self.actions_tree.insert("", "end", iid=action, values=(label, template.get("name", "Cap plantilla")))
        if selected:
            self.actions_tree.selection_set(selected)
            self._select_action()

    def _select_action(self, event=None):
        selected = self.actions_tree.selection()
        if selected:
            identity = self.config_data["actions"].get(selected[0])
            self.action_template.set(next(label for label, key in self._template_choices.items() if key == identity))

    def _assign_action(self):
        selected = self.actions_tree.selection()
        if not selected:
            messagebox.showwarning("Acció no seleccionada", "Selecciona l'acció que vols configurar.", parent=self)
            return
        identity = self._template_choices[self.action_template.get()]
        success, _ = self._run("Plantilles: assignar acció", lambda: assign_action_template(self.app.conn, selected[0], identity))
        if success:
            self._refresh_templates()

    def _refresh_templates(self, identity=None):
        self.config_data = load_template_config(self.app.conn)
        self.templates_tree.delete(*self.templates_tree.get_children())
        self._template_choices = {"Cap plantilla": None}
        for key, template in self.config_data["templates"].items():
            self.templates_tree.insert("", "end", iid=key, values=(template["name"], template["filename"]))
            self._template_choices[f"{template['name']} [{key[:6]}]"] = key
        self.action_combo.configure(values=list(self._template_choices))
        if self.action_template.get() not in self._template_choices:
            self.action_template.set("Cap plantilla")
        self.product_combo.configure(values=list(self._template_choices))
        if self.product_template.get() not in self._template_choices:
            self.product_template.set("Cap plantilla")
        selected = identity or self._template_id
        if selected and self.templates_tree.exists(selected):
            self.templates_tree.selection_set(selected)
            self._select_template()
        else:
            self._template_id = None
            self.name.set("")
            self.object_name.set("")
            self._fields = {}
            self.fields_tree.delete(*self.fields_tree.get_children())
        self._refresh_products()
        self._refresh_actions()

    def _select_template(self, event=None):
        selected = self.templates_tree.selection()
        if not selected:
            return
        self._template_id = selected[0]
        template = self.config_data["templates"][self._template_id]
        self.name.set(template["name"])
        self._fields = dict(template["fields"])
        self.object_name.set("")
        self.field_name.set(FIXED_FIELD)
        self.fields_tree.delete(*self.fields_tree.get_children())
        for name in sorted(set(template["objects"]) | set(self._fields)):
            self.fields_tree.insert("", "end", values=(name, FIELD_LABELS.get(self._fields.get(name), FIXED_FIELD)))

    def _select_field(self, event=None):
        selected = self.fields_tree.selection()
        if selected:
            name, field = self.fields_tree.item(selected[0], "values")
            self.object_name.set(name)
            self.field_name.set(field)

    def _save_field(self):
        name = self.object_name.get().strip()
        if not self._template_id or not name:
            messagebox.showwarning("Camp no definit", "Selecciona una plantilla i indica el nom exacte de l'objecte P-touch.", parent=self)
            return
        previous = dict(self._fields)
        field = next((key for key, label in FIELD_LABELS.items() if label == self.field_name.get()), None)
        if field is None:
            self._fields.pop(name, None)
        else:
            self._fields[name] = field
        if not self._save_details():
            self._fields = previous

    def _save_details(self):
        if not self._template_id:
            return False
        success, _ = self._run("Plantilla: modificar camps i nom", lambda: configure_label_template(
            self.app.conn, self._template_id, self.name.get(), self._fields))
        if success:
            self._refresh_templates()
        return success

    def _import(self):
        paths = filedialog.askopenfilenames(parent=self, title="Importar plantilles d'etiquetes", filetypes=[("P-touch", "*.lbx")])
        for path in paths:
            success, identity = self._run("Plantilla: importar", lambda path=path: import_label_template(self.app.conn, path))
            if success:
                self._refresh_templates(identity)

    def _replace(self):
        if not self._template_id:
            return
        path = filedialog.askopenfilename(parent=self, title="Substituir plantilla", filetypes=[("P-touch", "*.lbx")])
        if path:
            success, _ = self._run("Plantilla: substituir", lambda: import_label_template(self.app.conn, path, self._template_id))
            if success:
                self._refresh_templates()

    def _edit(self):
        if not self._template_id:
            return
        try:
            directory = Path("data") / "template_edits"
            directory.mkdir(parents=True, exist_ok=True)
            descriptor, path = tempfile.mkstemp(prefix=f"{self._template_id}_", suffix=".lbx", dir=directory)
            with os.fdopen(descriptor, "wb") as target:
                target.write(template_bytes(self.config_data["templates"][self._template_id]))
            self._edit_paths[self._template_id] = path
            if os.name == "nt":
                os.startfile(str(Path(path).resolve()))
            else:
                subprocess.Popen(["xdg-open", str(Path(path).resolve())])
        except Exception as exc:
            messagebox.showerror("Editor no disponible", str(exc), parent=self)

    def _collect_edits(self):
        path = self._edit_paths.get(self._template_id)
        if not path:
            messagebox.showwarning("Sense edició oberta", "Obre primer la plantilla amb Editar a P-touch, desa-la a l'editor i després recull els canvis.", parent=self)
            return
        success, _ = self._run("Plantilla: recollir edició", lambda: import_label_template(self.app.conn, path, self._template_id))
        if success:
            self._refresh_templates()

    def _export(self):
        if not self._template_id:
            return
        template = self.config_data["templates"][self._template_id]
        path = filedialog.asksaveasfilename(parent=self, title="Exportar plantilla", initialfile=template["filename"],
                                          defaultextension=".lbx", filetypes=[("P-touch", "*.lbx")])
        if path:
            try:
                Path(path).write_bytes(template_bytes(template))
            except Exception as exc:
                messagebox.showerror("Plantilla no exportada", str(exc), parent=self)

    def _delete(self):
        if self._template_id and messagebox.askyesno("Eliminar plantilla", "Vols eliminar la plantilla seleccionada?", parent=self):
            success, _ = self._run("Plantilla: eliminar", lambda: delete_label_template(self.app.conn, self._template_id))
            if success:
                self._refresh_templates()

    def _refresh_products(self):
        self.products_tree.delete(*self.products_tree.get_children())
        query = self.app._text_sort_key(self.product_search.get())
        self._products = {}
        config = load_template_config(self.app.conn)
        for row in list_catalog_products(self.app.conn):
            template_id = config["products"].get(product_template_key(row["sku"], row["name"]))
            template = config["templates"].get(template_id, {})
            values = (row["sku"] or "", row["name"], template.get("name", ""))
            if query and query not in self.app._text_sort_key(" ".join(values)):
                continue
            identity = str(row["id"])
            self._products[identity] = dict(row)
            self.products_tree.insert("", "end", iid=identity, values=values)
        self.app._reapply_tree_sort(self.products_tree)

    def _assign(self):
        products = [self._products[identity] for identity in self.products_tree.selection()]
        if not products:
            return
        identity = self._template_choices.get(self.product_template.get())
        success, _ = self._run("Plantilles: assignar productes", lambda: assign_product_template(self.app.conn, products, identity))
        if success:
            self._refresh_products()