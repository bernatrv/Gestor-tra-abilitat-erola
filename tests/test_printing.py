import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from src import printing


def test_bpac_factory_marks_methods_without_com_type_information(monkeypatch):
    if os.name != "nt":
        pytest.skip("Brother b-PAC Windows")
    import win32com.client
    from src.label_templates import _create_bpac_document

    document = Mock()
    dispatch = Mock(return_value=document)
    monkeypatch.setattr(win32com.client, "Dispatch", dispatch)
    assert _create_bpac_document() is document
    dispatch.assert_called_once_with("bpac.Document")
    document._FlagAsMethod.assert_called_once_with(
        "Open", "Close", "GetObject", "SetPrinter", "StartPrint", "PrintOut", "EndPrint",
    )
    document.Close.assert_not_called()
    document.StartPrint.assert_not_called()


def test_label_settings_persist_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr(printing, "SETTINGS_PATH", tmp_path / "settings" / "printing.json")
    assert printing.load_label_settings() == printing.DEFAULT_SETTINGS
    settings = {
        **printing.DEFAULT_SETTINGS, "label_printer": "ColorWorks", "document_printer": "Microsoft Print to PDF",
        "label_roll_width_mm": 80, "label_roll_length_mm": 50,
    }
    printing.save_label_settings(settings)
    assert printing.load_label_settings() == settings
    assert list(printing.SETTINGS_PATH.parent.glob("*.tmp")) == []
    for value in (float("nan"), float("inf"), 0, 1001):
        with pytest.raises(ValueError):
            printing.save_label_settings({**settings, "label_roll_width_mm": value})
    assert printing.load_label_settings() == settings


def test_old_printer_settings_migrate_to_two_printers_and_manual_roll(tmp_path, monkeypatch):
    monkeypatch.setattr(printing, "SETTINGS_PATH", tmp_path / "printing.json")
    printing.SETTINGS_PATH.write_text(json.dumps({
        "printer": "Brother QL-600 (Copiar 1)", "custom_size": False, "width_mm": 29, "height_mm": 89.8,
    }), encoding="utf-8")
    assert printing.load_label_settings() == {
        "label_printer": "Brother QL-600 (Copiar 1)", "document_printer": "",
        "label_roll_width_mm": 29, "label_roll_length_mm": 89.8,
    }


def test_devnames_contains_selected_driver_device_and_port():
    import struct

    payload = printing._devnames_payload("Brother QL-600", "Brother QL-600 (Copiar 1)", "USB001")
    offsets = struct.unpack_from("<4H", payload)
    strings = payload[8:].decode("utf-16-le").split("\0")
    assert offsets[0] == 4
    assert offsets[1] == offsets[0] + len("Brother QL-600") + 1
    assert offsets[2] == offsets[1] + len("Brother QL-600 (Copiar 1)") + 1
    assert strings[:3] == ["Brother QL-600", "Brother QL-600 (Copiar 1)", "USB001"]


def test_spool_aborts_failed_jobs_and_releases_context(monkeypatch):
    context = Mock()
    draw = Mock(side_effect=OSError("Printer offline"))
    monkeypatch.setattr(printing, "_draw_page", draw)
    with pytest.raises(OSError):
        printing._spool(context, "Test", [Image.new("RGB", (10, 10))])
    context.StartDoc.assert_called_once_with("Test")
    context.AbortDoc.assert_called_once()
    context.EndDoc.assert_not_called()
    context.DeleteDC.assert_called_once()


def test_spool_success_releases_context(monkeypatch):
    context = Mock()
    monkeypatch.setattr(printing, "_draw_page", Mock())
    printing._spool(context, "Test", [Image.new("RGB", (10, 10))])
    context.EndDoc.assert_called_once()
    context.AbortDoc.assert_not_called()
    context.DeleteDC.assert_called_once()


def test_label_page_draws_at_physical_size_and_accounts_for_printer_offset(monkeypatch):
    bounds = []

    class Dib:
        def __init__(self, image):
            self.image = image

        def draw(self, handle, target):
            bounds.append(target)

    monkeypatch.setitem(sys.modules, "PIL.ImageWin", SimpleNamespace(Dib=Dib))
    context = Mock()
    caps = {8: 696, 10: 285, 110: 732, 111: 355, 112: 18, 113: 35}
    context.GetDeviceCaps.side_effect = caps.__getitem__
    printing._draw_page(context, Image.new("RGB", (732, 354)), fit_to_printable=False)
    assert bounds == [(-18, -35, 714, 319)]
    context.StartPage.assert_called_once()
    context.EndPage.assert_called_once()


def test_document_rendering_uses_selected_pages_and_native_context(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows GDI")
    import pymupdf

    path = tmp_path / "document.pdf"
    with pymupdf.open() as document:
        for index in range(3):
            document.new_page().insert_text((40, 40), f"Page {index}")
        document.save(path)
    context = Mock()
    selector = Mock(return_value=(context, range(1, 2)))
    monkeypatch.setattr(printing, "_document_device_context", selector)
    draw = Mock()
    monkeypatch.setattr(printing, "_draw_page", draw)
    assert printing.print_document_file(path, owner=123, printer_name="Microsoft Print to PDF")["status"] == "printed"
    selector.assert_called_once_with(3, 123, "Microsoft Print to PDF")
    draw.assert_called_once()
    assert draw.call_args.args[1].width > 1000
    context.EndDoc.assert_called_once()
    selector.return_value = (None, range(0))
    assert printing.print_document_file(path)["status"] == "cancelled"


def test_label_uses_selected_driver_without_sending_job(tmp_path, monkeypatch):
    path = tmp_path / "label.png"
    Image.new("RGB", (900, 500)).save(path)
    context = Mock()
    factory = Mock(return_value=context)
    spool = Mock()
    monkeypatch.setattr(printing, "_label_device_context", factory)
    monkeypatch.setattr(printing, "_spool", spool)
    settings = {**printing.DEFAULT_SETTINGS, "label_roll_width_mm": 80, "label_roll_length_mm": 50}
    printing.print_label_windows(path, "Brother QL-600", settings)
    factory.assert_called_once_with("Brother QL-600", settings)
    assert spool.call_args.args[0] is context
    assert spool.call_args.args[2][0].size == (900, 500)


@pytest.mark.parametrize("device_caps", [(991, 306), (306, 991)])
def test_generic_label_uses_manual_roll_size_independent_of_device_caps(
    tmp_path, monkeypatch, device_caps,
):
    from src import label_print

    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    path = label_print._render_product_label(
        "Arròs ecològic", "ENTRY-1", ["Proveïdor: Erola", "Caducitat: 31/12/2027"], "ENTRY-1",
    )
    context = Mock()
    context.GetDeviceCaps.side_effect = lambda index: device_caps[0] if index == 8 else device_caps[1]
    spool = Mock()
    monkeypatch.setattr(printing, "_label_device_context", Mock(return_value=context))
    monkeypatch.setattr(printing, "_spool", spool)

    settings = {**printing.DEFAULT_SETTINGS, "label_roll_width_mm": 29, "label_roll_length_mm": 89.8}
    printing.print_label_windows(str(path), "Brother QL-600", settings)

    with Image.open(path) as rendered:
        expected = (round(settings["label_roll_width_mm"] * 300 / 25.4),
                    round(settings["label_roll_length_mm"] * 300 / 25.4))
        assert rendered.size == expected
        metadata = json.loads(rendered.info["erola.label.v1"])
        assert metadata == {
            "product_name": "Arròs ecològic", "lot_number": "ENTRY-1",
            "lines": ["Proveïdor: Erola", "Caducitat: 31/12/2027"], "barcode_value": "ENTRY-1",
            "label_type": "packaged",
        }
    spool.assert_called_once()
    assert spool.call_args.args[2][0].size == expected


def test_receipt_label_matches_connected_brother_roll_without_spooling(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Brother QL-600 Windows")
    import win32print
    from src import label_print

    printer_name = "Brother QL-600 (Copiar 1)"
    available = {row[2] for row in win32print.EnumPrinters(6)}
    if printer_name not in available:
        pytest.skip("No hi ha cap Brother QL-600 connectada")

    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    path = label_print.create_label_image(
        "Arròs ecològic", "ENTRY-1", "Catalunya", "31/12/2027", "ENTRY-1", supplier="Erola",
    )
    captured = {}

    def capture_spool(context, title, images, fit_to_printable=True):
        captured["size"] = images[0].size
        captured["caps"] = (context.GetDeviceCaps(8), context.GetDeviceCaps(10))
        captured["fit_to_printable"] = fit_to_printable
        context.DeleteDC()

    monkeypatch.setattr(printing, "_spool", capture_spool)
    settings = {**printing.DEFAULT_SETTINGS, "label_roll_width_mm": 29, "label_roll_length_mm": 89.8}
    printing.print_label_windows(path, printer_name, {**settings, "label_printer": printer_name})
    assert captured["size"] == (round(62 * 300 / 25.4), round(30 * 300 / 25.4))
    assert all(dimension > 0 for dimension in captured["caps"])
    assert captured["caps"][0] > captured["caps"][1]
    assert captured["fit_to_printable"] is False
    with Image.open(path) as image:
        assert image.size == captured["size"]
        assert "erola.label.v1" in image.info


def test_receipt_text_fits_long_narrow_roll_layout(monkeypatch):
    from src import label_print

    original = label_print.ImageDraw.ImageDraw.text

    def assert_text_fits(draw, position, text, *args, **kwargs):
        bounds = draw.textbbox(position, text, font=kwargs["font"])
        assert 0 <= bounds[0] <= bounds[2] <= draw._image.width
        assert 0 <= bounds[1] <= bounds[3] <= draw._image.height
        return original(draw, position, text, *args, **kwargs)

    monkeypatch.setattr(label_print.ImageDraw.ImageDraw, "text", assert_text_fits)
    image = label_print._build_product_label_image(
        "Arròs ecològic i farina integral de producció local",
        "ENTRADA-20261022-123456789",
        ["Proveïdor: Associació de Productors de la Selva", "Lot d'entrada: ENTRADA-20261022-123456789",
         "Origen: Catalunya", "Caducitat: 31/12/2027", "Pes: 0,25 kg"],
        "20261022SKU12345",
        canvas_size=(306, 991), rotate=True,
    )
    assert image.size == (991, 306)
    from PIL import ImageChops
    assert ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox() is not None


def test_receipt_label_uses_fixed_landscape_canvas_and_print_media(tmp_path, monkeypatch):
    from PIL import ImageChops
    from src import label_print

    monkeypatch.setattr(label_print, "OUTPUT_DIR", str(tmp_path))
    path = label_print.create_label_image(
        "Arròs ecològic i farina integral de producció local", "ENTRY-1", "Catalunya", "31/12/2027",
        "ENTRADA-20261022-123456789", supplier="Associació de Productors de la Selva", weight="0,25 kg",
    )
    with Image.open(path) as source:
        assert source.size == (732, 354)
        metadata = json.loads(source.info["erola.label.v1"])
        assert metadata["label_type"] == "receipt"
        ink = ImageChops.difference(source.convert("RGB"), Image.new("RGB", source.size, "white")).getbbox()
        assert 20 <= ink[0] and ink[2] <= source.width - 20
        assert ink[3] >= source.height - 50

    context = Mock()
    factory = Mock(return_value=context)
    spool = Mock()
    monkeypatch.setattr(printing, "_label_device_context", factory)
    monkeypatch.setattr(printing, "_spool", spool)
    settings = {**printing.DEFAULT_SETTINGS, "label_roll_width_mm": 29, "label_roll_length_mm": 89.8}
    printing.print_label_windows(path, "Brother QL-600", settings)
    used_settings = factory.call_args.args[1]
    assert used_settings["label_roll_width_mm"] == 62
    assert used_settings["label_roll_length_mm"] == 30
    assert spool.call_args.args[2][0].size == (732, 354)
    assert spool.call_args.kwargs["fit_to_printable"] is False


def test_template_registry_import_replace_and_assign(tmp_path):
    import zipfile
    from src.db import initialize_db
    from src import label_templates as templates

    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/><objectStyle objectName="Lot"/></label>')
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        identity = templates.import_label_template(conn, path)
        templates.configure_label_template(conn, identity, "Bossa", {"Producte": "producte", "Lot": "lot"})
        templates.assign_product_template(conn, [{"sku": "001", "name": "Arros"}], identity)
        assert templates.get_product_template(conn, "001", "Arros") is None
        templates.set_templates_enabled(conn, True)
        assert templates.get_product_template(conn, "001", "Nou nom")["name"] == "Bossa"
        templates.import_label_template(conn, path, identity)
        assert templates.get_product_template(conn, "001", "Arros")["fields"]["Lot"] == "lot"
        with pytest.raises(ValueError, match="associats"):
            templates.delete_label_template(conn, identity)
        with pytest.raises(ValueError, match="no té cap plantilla"):
            templates.get_product_template(conn, "002", "Oli")
        template = templates.get_product_template(conn, "001", "Arros")
        job = tmp_path / "job.json"
        templates.save_template_job(job, template, {"producte": "Arros", "lot": "L-1"}, 3)
        assert job.is_file()
        templates.assign_product_template(conn, [{"sku": "001", "name": "Arros"}], None)
        templates.delete_label_template(conn, identity)
        assert templates.load_template_config(conn)["templates"] == {}
    finally:
        conn.close()


def test_action_templates_are_independent_from_packaging_products(tmp_path):
    import json
    import zipfile
    from src import label_templates as templates
    from src.db import initialize_db, set_setting

    conn = initialize_db(str(tmp_path / "actions.db"))
    try:
        set_setting(conn, templates.SETTINGS_KEY, json.dumps({"enabled": False, "templates": {}, "products": {}}))
        assert templates.load_template_config(conn)["actions"] == {}
        assert list(templates.ACTION_LABELS) == ["recepcio"]
        path = tmp_path / "Etiqueta.lbx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/></label>')
        receipt = templates.import_label_template(conn, path)
        specific = templates.import_label_template(conn, path)
        templates.configure_label_template(conn, receipt, "Recepció", {"Producte": "producte"})
        templates.configure_label_template(conn, specific, "Específica", {"Producte": "producte"})
        templates.assign_action_template(conn, "recepcio", receipt)
        assert templates.get_action_template(conn, "recepcio")["name"] == "Recepció"
        templates.set_templates_enabled(conn, True)
        with pytest.raises(ValueError, match="no té cap plantilla"):
            templates.get_product_template(conn, "001", "Arròs")
        templates.assign_product_template(conn, [{"sku": "001", "name": "Arròs"}], specific)
        assert templates.get_product_template(conn, "001", "Arròs")["name"] == "Específica"
        with pytest.raises(ValueError, match="accions associades"):
            templates.delete_label_template(conn, receipt)
        with pytest.raises(ValueError, match="no és vàlida"):
            templates.assign_action_template(conn, "envasat", specific)
        backup = initialize_db(str(tmp_path / "backup.db"))
        try:
            conn.backup(backup)
            assert templates.get_action_template(backup, "recepcio")["name"] == "Recepció"
        finally:
            backup.close()
        templates.assign_action_template(conn, "recepcio", None)
        with pytest.raises(ValueError, match="no té cap plantilla"):
            templates.get_action_template(conn, "recepcio")
        with pytest.raises(ValueError, match="ja no existeix"):
            templates.assign_action_template(conn, "recepcio", "missing")
        templates.delete_label_template(conn, receipt)
    finally:
        conn.close()


def test_legacy_packaging_action_is_dropped_without_affecting_products(tmp_path):
    import base64
    import io
    import json
    import zipfile
    from src import label_templates as templates
    from src.db import initialize_db, set_setting

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/></label>')
    template = {"name": "Bossa", "filename": "Bossa.lbx", "objects": ["Producte"], "fields": {"Producte": "producte"},
                "content": base64.b64encode(buffer.getvalue()).decode("ascii")}
    conn = initialize_db(str(tmp_path / "legacy.db"))
    try:
        set_setting(conn, templates.SETTINGS_KEY, json.dumps({
            "enabled": True, "templates": {"t1": template}, "products": {},
            "actions": {"recepcio": "t1", "envasat": "t1"},
        }))
        assert templates.load_template_config(conn)["actions"] == {"recepcio": "t1"}
        with pytest.raises(ValueError, match="no té cap plantilla"):
            templates.get_product_template(conn, "001", "Arròs")
        templates.assign_action_template(conn, "recepcio", None)
        templates.delete_label_template(conn, "t1")
    finally:
        conn.close()


@pytest.mark.parametrize("actions", [[], {"unknown": "x"}, {"recepcio": "missing"}, {"recepcio": None}])
def test_invalid_action_configuration_is_reported(tmp_path, actions):
    import json
    from src import label_templates as templates
    from src.db import initialize_db, set_setting

    conn = initialize_db(str(tmp_path / "invalid.db"))
    try:
        set_setting(conn, templates.SETTINGS_KEY, json.dumps({
            "enabled": False, "templates": {}, "products": {}, "actions": actions,
        }))
        with pytest.raises(ValueError, match="configuració"):
            templates.load_template_config(conn)
    finally:
        conn.close()


def test_invalid_template_replacement_keeps_original_and_fixed_layout(tmp_path):
    import zipfile
    from src.db import initialize_db
    from src import label_templates as templates

    path = tmp_path / "Bossa.lbx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("label.xml", '<label xmlns:p="urn:test"><p:objectStyle name="Lot"/><image>fixed logo</image></label>')
        archive.writestr("image.png", b"fixed image")
    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        original = path.read_bytes()
        identity = templates.import_label_template(conn, path)
        template = templates.load_template_config(conn)["templates"][identity]
        assert template["objects"] == ["Lot"]
        assert templates.template_bytes(template) == original
        with pytest.raises(ValueError, match="camps variables"):
            templates.save_template_job(tmp_path / "job.json", template, {"lot": "L-1"}, 1)
        before = conn.serialize()
        path.write_bytes(b"not an LBX")
        with pytest.raises(ValueError, match="LBX"):
            templates.import_label_template(conn, path, identity)
        assert conn.serialize() == before
        backup = initialize_db(str(tmp_path / "backup.db"))
        try:
            conn.backup(backup)
            assert templates.template_bytes(templates.load_template_config(backup)["templates"][identity]) == original
        finally:
            backup.close()
    finally:
        conn.close()


def test_template_corrupt_config_does_not_fall_back_to_generic_label(tmp_path):
    from src import label_templates as templates
    from src.db import initialize_db, set_setting

    conn = initialize_db(str(tmp_path / "test.db"))
    try:
        set_setting(conn, templates.SETTINGS_KEY, "broken")
        with pytest.raises(ValueError, match="configuració"):
            templates.get_product_template(conn, "001", "Arros")
    finally:
        conn.close()


@pytest.mark.parametrize("missing_object, print_success", [(False, True), (True, True), (False, False)])
def test_bpac_changes_only_variable_objects_and_closes(tmp_path, monkeypatch, missing_object, print_success):
    if os.name != "nt":
        pytest.skip("Brother b-PAC Windows")
    import base64
    import io
    import zipfile
    from src import label_templates as templates

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("label.xml", '<label><objectStyle objectName="Producte"/><objectStyle objectName="Lot"/><objectStyle objectName="Logo"/></label>')
    template = {"name": "Bossa", "content": base64.b64encode(buffer.getvalue()).decode(),
                "fields": {"Producte": "producte", "Lot": "lot"}}
    path = templates.save_template_job(tmp_path / "job.json", template, {"producte": "Arròs", "lot": "LOT-1"}, 7)
    document = Mock()
    product, lot = Mock(), Mock()
    document.GetObject.side_effect = [product, None if missing_object else lot]
    document.PrintOut.return_value = print_success
    monkeypatch.setattr(templates, "_create_bpac_document", Mock(return_value=document))
    result = templates.print_template_job(path, "Brother QL-600")
    assert result["status"] == ("error" if missing_object or not print_success else "printed")
    assert product.Text == "Arròs"
    assert [call.args[0] for call in document.GetObject.call_args_list] == ["Producte", "Lot"]
    if missing_object:
        document.StartPrint.assert_not_called()
        document.PrintOut.assert_not_called()
    else:
        assert lot.Text == "LOT-1"
        document.PrintOut.assert_called_once_with(7, 0)
        document.EndPrint.assert_called_once()
    document.Close.assert_called_once()
    assert Path(path).is_file()