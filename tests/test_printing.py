import os
from pathlib import Path
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
    settings = {**printing.DEFAULT_SETTINGS, "printer": "ColorWorks", "custom_size": True, "width_mm": 80, "height_mm": 50}
    printing.save_label_settings(settings)
    assert printing.load_label_settings() == settings
    assert list(printing.SETTINGS_PATH.parent.glob("*.tmp")) == []
    for value in (float("nan"), float("inf"), 0, 1001):
        with pytest.raises(ValueError):
            printing.save_label_settings({**settings, "width_mm": value})
    assert printing.load_label_settings() == settings


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
    assert printing.print_document_file(path, owner=123)["status"] == "printed"
    selector.assert_called_once_with(3, 123)
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
    settings = {**printing.DEFAULT_SETTINGS, "custom_size": True}
    printing.print_label_windows(path, "Brother QL-600", settings)
    factory.assert_called_once_with("Brother QL-600", settings)
    assert spool.call_args.args[0] is context
    assert spool.call_args.args[2][0].size == (900, 500)


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