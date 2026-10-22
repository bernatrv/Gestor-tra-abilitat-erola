from __future__ import annotations

import base64
import copy
import io
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from src.db import get_setting, set_setting


SETTINGS_KEY = "packaged_label_templates_v1"
FIELD_LABELS = {
    "producte": "Nom del producte", "sku": "SKU", "lot": "Lot (entrada o envasat)",
    "caducitat": "Caducitat (AAAA-MM-DD)", "caducitat_dmy": "Caducitat (DD/MM/AAAA)",
    "origen": "Origen", "pes_kg": "Pes (valor en kg)", "pes": "Pes amb unitat kg",
    "data_envasat": "Data d'envasat", "codi_barres": "Codi de barres",
    "proveidor": "Proveïdor (recepció)", "data_recepcio": "Data de recepció",
}
ACTION_LABELS = {"recepcio": "Recepció de productes"}
MAX_TEMPLATE_BYTES = 16 * 1024 * 1024


def read_template(data):
    if not data or len(data) > MAX_TEMPLATE_BYTES:
        raise ValueError("La plantilla és buida o supera els 16 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("label.xml")
            if info.file_size > MAX_TEMPLATE_BYTES:
                raise ValueError("El disseny de la plantilla supera els 16 MB.")
            xml = archive.read(info)
        if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
            raise ValueError("La plantilla conté declaracions XML no admeses.")
        root = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError, RuntimeError) as exc:
        raise ValueError("El fitxer no és una plantilla LBX vàlida amb label.xml.") from exc
    names = set()
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag.lower() == "objectname" and element.text:
            names.add(element.text.strip())
        for key, value in element.attrib.items():
            attribute = key.rsplit("}", 1)[-1].lower()
            if attribute == "objectname" or (tag.lower() == "objectstyle" and attribute == "name"):
                if value.strip():
                    names.add(value.strip())
    return sorted(names)


def load_template_config(conn):
    raw = get_setting(conn, SETTINGS_KEY)
    if raw is None:
        return {"enabled": False, "templates": {}, "products": {}, "actions": {}}
    try:
        config = json.loads(raw)
        if not isinstance(config["templates"], dict) or not isinstance(config["products"], dict):
            raise ValueError
        if not isinstance(config["enabled"], bool):
            raise ValueError
        config.setdefault("actions", {})
        if isinstance(config["actions"], dict):
            # Packaging templates are assigned per product; drop the former packaging action.
            config["actions"].pop("envasat", None)
        if not isinstance(config["actions"], dict) or any(
            action not in ACTION_LABELS or not isinstance(identity, str) or identity not in config["templates"]
            for action, identity in config["actions"].items()
        ):
            raise ValueError
        return config
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("La configuració de plantilles no és vàlida. Revisa-la al menú Impressió.") from exc


def import_label_template(conn, path, template_id=None):
    source = Path(path)
    if source.suffix.lower() != ".lbx":
        raise ValueError("Selecciona una plantilla .lbx de P-touch Editor.")
    if source.stat().st_size > MAX_TEMPLATE_BYTES:
        raise ValueError("La plantilla supera els 16 MB.")
    data = source.read_bytes()
    objects = read_template(data)
    config = load_template_config(conn)
    if template_id is not None and template_id not in config["templates"]:
        raise ValueError("La plantilla seleccionada ja no existeix.")
    identity = template_id or uuid.uuid4().hex
    previous = config["templates"].get(identity, {})
    config["templates"][identity] = {
        "name": previous.get("name", source.stem), "filename": source.name,
        "content": base64.b64encode(data).decode("ascii"), "objects": objects,
        "fields": previous.get("fields", {}),
    }
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))
    return identity


def configure_label_template(conn, template_id, name, fields):
    if not name.strip():
        raise ValueError("Cal indicar el nom de la plantilla.")
    if any(not key.strip() or value not in FIELD_LABELS for key, value in fields.items()):
        raise ValueError("El nom d'objecte o la dada variable no són vàlids.")
    config = load_template_config(conn)
    template = config["templates"][template_id]
    template["name"] = name.strip()
    template["fields"] = dict(fields)
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))


def product_template_key(sku, name):
    return f"sku:{sku.strip()}" if sku and sku.strip() else "name:" + " ".join(name.casefold().split())


def assign_product_template(conn, products, template_id):
    config = load_template_config(conn)
    if template_id is not None and template_id not in config["templates"]:
        raise ValueError("La plantilla seleccionada ja no existeix.")
    for product in products:
        key = product_template_key(product["sku"], product["name"])
        if template_id is None:
            config["products"].pop(key, None)
        else:
            config["products"][key] = template_id
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))


def set_templates_enabled(conn, enabled):
    config = load_template_config(conn)
    config["enabled"] = bool(enabled)
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))


def assign_action_template(conn, action, template_id):
    if action not in ACTION_LABELS:
        raise ValueError("L'acció seleccionada no és vàlida.")
    config = load_template_config(conn)
    if template_id is not None and template_id not in config["templates"]:
        raise ValueError("La plantilla seleccionada ja no existeix.")
    if template_id is None:
        config["actions"].pop(action, None)
    else:
        config["actions"][action] = template_id
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))


def get_action_template(conn, action):
    if action not in ACTION_LABELS:
        raise ValueError("L'acció seleccionada no és vàlida.")
    config = load_template_config(conn)
    identity = config["actions"].get(action)
    if identity not in config["templates"]:
        raise ValueError(f"L'acció '{ACTION_LABELS[action]}' no té cap plantilla assignada. Revisa Impressió > Plantilles d'etiquetes.")
    return copy.deepcopy(config["templates"][identity])


def delete_label_template(conn, template_id):
    config = load_template_config(conn)
    if template_id in config["products"].values():
        raise ValueError("Hi ha productes associats a aquesta plantilla. Canvia primer la seva assignació.")
    if template_id in config["actions"].values():
        raise ValueError("Hi ha accions associades a aquesta plantilla. Canvia primer la seva assignació.")
    del config["templates"][template_id]
    set_setting(conn, SETTINGS_KEY, json.dumps(config, ensure_ascii=False))


def get_product_template(conn, sku, name):
    config = load_template_config(conn)
    if not config["enabled"]:
        return None
    identity = config["products"].get(product_template_key(sku, name))
    if identity not in config["templates"]:
        raise ValueError(f"El producte '{name}' no té cap plantilla assignada. Revisa Impressió > Plantilles d'etiquetes.")
    return copy.deepcopy(config["templates"][identity])


def template_bytes(template):
    data = base64.b64decode(template["content"], validate=True)
    read_template(data)
    return data


def save_template_job(path, template, values, copies):
    template_bytes(template)
    if not template["fields"]:
        raise ValueError("La plantilla no té camps variables configurats. Defineix-los a Impressió > Plantilles d'etiquetes.")
    if not isinstance(copies, int) or isinstance(copies, bool) or copies < 1:
        raise ValueError("El nombre d'etiquetes ha de ser un enter positiu.")
    if any(key not in values for key in template["fields"].values()):
        missing = sorted(set(template["fields"].values()) - values.keys())
        raise ValueError("Falten dades per omplir la plantilla en aquesta acció: " + ", ".join(FIELD_LABELS.get(key, key) for key in missing))
    with open(path, "w", encoding="utf-8") as target:
        json.dump({"format": "erola.label-job.v1", "template": template, "values": values, "copies": copies},
                  target, ensure_ascii=False, indent=2)
    return str(path)


def _create_bpac_document():
    try:
        import win32com.client

        document = win32com.client.Dispatch("bpac.Document")
        document._FlagAsMethod("Open", "Close", "GetObject", "SetPrinter", "StartPrint", "PrintOut", "EndPrint")
        return document
    except Exception as exc:
        raise OSError("Cal instal·lar Brother b-PAC de la mateixa arquitectura (32/64 bits) que el programa.") from exc


def print_template_job(path, printer_name):
    try:
        if os.name != "nt":
            raise OSError("Les plantilles P-touch amb b-PAC només es poden imprimir a Windows.")
        if not printer_name:
            raise ValueError("Selecciona una impressora Brother a Impressió > Configurar impressores.")
        with open(path, encoding="utf-8") as source:
            job = json.load(source)
        if job.get("format") != "erola.label-job.v1":
            raise ValueError("La feina d'etiquetes no té un format vàlid.")
        copies = job["copies"]
        if not isinstance(copies, int) or isinstance(copies, bool) or copies < 1:
            raise ValueError("El nombre d'etiquetes no és vàlid.")
        fields = job["template"]["fields"]
        if not fields or any(field not in FIELD_LABELS or field not in job["values"] for field in fields.values()):
            raise ValueError("La feina no té els camps variables correctament configurats.")
        import pythoncom

        pythoncom.CoInitialize()
        try:
            with tempfile.TemporaryDirectory(prefix="erola-label-") as directory:
                template_path = Path(directory) / "label.lbx"
                template_path.write_bytes(template_bytes(job["template"]))
                document = _create_bpac_document()
                opened = started = False
                try:
                    if not document.Open(str(template_path)):
                        raise OSError("b-PAC no ha pogut obrir la plantilla.")
                    opened = True
                    if not document.SetPrinter(printer_name, True):
                        raise OSError("b-PAC no ha acceptat la impressora. Selecciona una Brother compatible.")
                    for name, field in job["template"]["fields"].items():
                        target = document.GetObject(name)
                        if target is None:
                            raise ValueError(f"No s'ha trobat l'objecte variable '{name}' a la plantilla.")
                        target.Text = str(job["values"][field])
                    if not document.StartPrint("erola - " + job["template"]["name"], 0):
                        raise OSError("b-PAC no ha pogut iniciar la impressió.")
                    started = True
                    if not document.PrintOut(job["copies"], 0):
                        raise OSError("b-PAC no ha confirmat l'enviament. Revisa la cua abans de tornar a imprimir.")
                    started = False
                    if document.EndPrint() is False:
                        raise OSError("b-PAC no ha confirmat el final de la feina. Revisa la cua abans de repetir-la.")
                finally:
                    if started:
                        document.EndPrint()
                    if opened:
                        document.Close()
        finally:
            pythoncom.CoUninitialize()
        return {"status": "printed", "message": "Etiquetes enviades amb la plantilla P-touch."}
    except Exception as exc:
        return {"status": "error", "message": f"{exc}\nFeina conservada a: {path}"}