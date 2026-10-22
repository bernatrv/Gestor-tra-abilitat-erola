from __future__ import annotations

import ctypes
import json
import math
import os
import struct
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageOps, PngImagePlugin


SETTINGS_PATH = Path("data") / "printing.json"
DEFAULT_SETTINGS = {
    "label_printer": "Brother QL-600",
    "document_printer": "",
    "label_roll_width_mm": 62.0,
    "label_roll_length_mm": 100.0,
}
RECEIPT_LABEL_WIDTH_MM = 62.0
RECEIPT_LABEL_HEIGHT_MM = 30.0


def load_label_settings():
    try:
        with SETTINGS_PATH.open(encoding="utf-8") as source:
            saved = json.load(source)
        if "label_printer" not in saved and "printer" in saved:
            saved["label_printer"] = saved["printer"]
        if "label_roll_width_mm" not in saved and "width_mm" in saved:
            saved["label_roll_width_mm"] = saved["width_mm"]
        if "label_roll_length_mm" not in saved and "height_mm" in saved:
            saved["label_roll_length_mm"] = saved["height_mm"]
        return validate_label_settings({**DEFAULT_SETTINGS, **saved})
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_SETTINGS)


def validate_label_settings(settings):
    result = {
        "label_printer": str(settings.get("label_printer", settings.get("printer", DEFAULT_SETTINGS["label_printer"]))).strip(),
        "document_printer": str(settings.get("document_printer", DEFAULT_SETTINGS["document_printer"])).strip(),
        "label_roll_width_mm": settings.get("label_roll_width_mm", settings.get("width_mm", DEFAULT_SETTINGS["label_roll_width_mm"])),
        "label_roll_length_mm": settings.get("label_roll_length_mm", settings.get("height_mm", DEFAULT_SETTINGS["label_roll_length_mm"])),
    }
    for key in ("label_roll_width_mm", "label_roll_length_mm"):
        value = float(result[key])
        if not math.isfinite(value) or not 1 <= value <= 1000:
            raise ValueError("Les mides han d'estar entre 1 i 1000 mm.")
        result[key] = value
    return result


def save_label_settings(settings):
    settings = validate_label_settings(settings)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(settings, target, indent=2)
        os.replace(temporary, SETTINGS_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def list_printers():
    if os.name == "nt":
        import win32print

        return sorted(row[2] for row in win32print.EnumPrinters(6))
    result = subprocess.run(["lpstat", "-a"], check=True, capture_output=True, text=True)
    return sorted(line.split()[0] for line in result.stdout.splitlines() if line.strip())


def list_printer_choices():
    names = list_printers()
    if os.name != "nt":
        return [(name, name) for name in names]
    import win32print

    choices = []
    for name in names:
        handle = win32print.OpenPrinter(name)
        try:
            port = win32print.GetPrinter(handle, 2).get("pPortName", "")
        finally:
            win32print.ClosePrinter(handle)
        label = f"{name} ({port})" if port else name
        choices.append((label, name))
    return choices


def get_default_printer():
    if os.name == "nt":
        import win32print

        try:
            return win32print.GetDefaultPrinter()
        except win32print.error:
            return ""
    result = subprocess.run(["lpstat", "-d"], check=False, capture_output=True, text=True)
    return result.stdout.partition(":")[2].strip() if result.returncode == 0 else ""


def printer_preferences(printer_name, owner=0):
    if os.name != "nt":
        raise OSError("Configura el paper i el tall des de les preferències CUPS de la impressora.")
    import win32con
    import win32print

    handle = win32print.OpenPrinter(printer_name)
    try:
        mode = win32print.GetPrinter(handle, 9)["pDevMode"] or win32print.GetPrinter(handle, 2)["pDevMode"]
        result = win32print.DocumentProperties(owner, handle, printer_name, mode, mode,
                                              win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER | win32con.DM_IN_PROMPT)
        if result == 1:
            win32print.SetPrinter(handle, 9, {"pDevMode": mode}, 0)
        return result == 1
    finally:
        win32print.ClosePrinter(handle)


def _label_device_context(printer_name, settings):
    import win32con
    import win32gui
    import win32print
    import win32ui

    handle = win32print.OpenPrinter(printer_name)
    try:
        mode = win32print.GetPrinter(handle, 9)["pDevMode"] or win32print.GetPrinter(handle, 2)["pDevMode"]
        mode.Copies = 1
        mode.Fields |= win32con.DM_COPIES
        width = round(settings["label_roll_width_mm"] * 10)
        length = round(settings["label_roll_length_mm"] * 10)
        orientation = win32con.DMORIENT_PORTRAIT
        mode.PaperSize = win32con.DMPAPER_USER
        mode.PaperWidth = width
        mode.PaperLength = length
        mode.Orientation = orientation
        mode.Fields |= win32con.DM_PAPERSIZE | win32con.DM_PAPERWIDTH | win32con.DM_PAPERLENGTH | win32con.DM_ORIENTATION
        result = win32print.DocumentProperties(0, handle, printer_name, mode, mode,
                                              win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
        if result != 1:
            raise OSError("El controlador no ha acceptat la configuració de l'etiqueta.")
        if abs(mode.PaperWidth - width) > 10 or abs(mode.PaperLength - length) > 10:
            raise ValueError("La impressora no accepta la mida de rotlle indicada. Revisa les preferències del controlador.")
        return win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer_name, mode))
    finally:
        win32print.ClosePrinter(handle)


class _PrintDialog(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
        ("hDevMode", wintypes.HGLOBAL), ("hDevNames", wintypes.HGLOBAL), ("hDC", wintypes.HDC),
        ("Flags", wintypes.DWORD), ("nFromPage", wintypes.WORD), ("nToPage", wintypes.WORD),
        ("nMinPage", wintypes.WORD), ("nMaxPage", wintypes.WORD), ("nCopies", wintypes.WORD),
        ("hInstance", wintypes.HINSTANCE), ("lCustData", ctypes.c_ssize_t),
        ("lpfnPrintHook", ctypes.c_void_p), ("lpfnSetupHook", ctypes.c_void_p),
        ("lpPrintTemplateName", wintypes.LPCWSTR), ("lpSetupTemplateName", wintypes.LPCWSTR),
        ("hPrintTemplate", wintypes.HGLOBAL), ("hSetupTemplate", wintypes.HGLOBAL),
    ]


def _devnames_payload(driver_name, printer_name, output_name):
    strings = [driver_name, printer_name, output_name]
    offsets = []
    cursor = 4
    for value in strings:
        offsets.append(cursor)
        cursor += len(value.encode("utf-16-le")) // 2 + 1
    return struct.pack("<4H", *offsets, 0) + b"".join(value.encode("utf-16-le") + b"\0\0" for value in strings)


def _create_devnames_handle(printer_name):
    import win32print

    handle = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(handle, 2)
        payload = _devnames_payload(info["pDriverName"], info["pPrinterName"], info.get("pPortName", ""))
    finally:
        win32print.ClosePrinter(handle)

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.restype = wintypes.HGLOBAL
    memory = kernel.GlobalAlloc(0x2 | 0x40, len(payload))
    if not memory:
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = kernel.GlobalLock(memory)
    if not pointer:
        kernel.GlobalFree(memory)
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(pointer, payload, len(payload))
    kernel.GlobalUnlock(memory)
    return memory


def _document_device_context(page_count, owner, printer_name=None):
    import win32ui

    dialog = _PrintDialog()
    dialog.lStructSize = ctypes.sizeof(dialog)
    if not owner:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetActiveWindow.restype = wintypes.HWND
        owner = user.GetActiveWindow()
    dialog.hwndOwner = owner
    dialog.Flags = 0x100 | 0x40000 | 0x10 | 0x4 | 0x100000
    if printer_name:
        dialog.hDevNames = _create_devnames_handle(printer_name)
    dialog.nFromPage = dialog.nMinPage = dialog.nCopies = 1
    dialog.nToPage = dialog.nMaxPage = min(page_count, 65535)
    api = ctypes.WinDLL("comdlg32", use_last_error=True)
    api.PrintDlgW.argtypes = [ctypes.POINTER(_PrintDialog)]
    api.PrintDlgW.restype = wintypes.BOOL
    api.CommDlgExtendedError.restype = wintypes.DWORD
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalFree.restype = wintypes.HGLOBAL
    try:
        if not api.PrintDlgW(ctypes.byref(dialog)):
            error = api.CommDlgExtendedError()
            if error:
                raise OSError(f"No s'ha pogut obrir el diàleg d'impressió (Windows {error}).")
            return None, range(0)
        pages = range(dialog.nFromPage - 1, dialog.nToPage) if dialog.Flags & 0x2 else range(page_count)
        return win32ui.CreateDCFromHandle(dialog.hDC), pages
    finally:
        for handle in (dialog.hDevMode, dialog.hDevNames):
            if handle:
                kernel.GlobalFree(handle)


def _draw_page(context, image, fit_to_printable=True):
    from PIL import ImageWin

    width, height = context.GetDeviceCaps(8), context.GetDeviceCaps(10)
    if width <= 0 or height <= 0:
        raise OSError("La impressora no indica una àrea imprimible vàlida.")
    if fit_to_printable:
        scale = min(width / image.width, height / image.height)
        target_width, target_height = max(1, round(image.width * scale)), max(1, round(image.height * scale))
        left, top = (width - target_width) // 2, (height - target_height) // 2
    else:
        physical_width = context.GetDeviceCaps(110)
        physical_height = context.GetDeviceCaps(111)
        if image.width > physical_width or image.height > physical_height:
            raise ValueError("La imatge és més gran que el paper configurat a la impressora.")
        left = -context.GetDeviceCaps(112)
        top = -context.GetDeviceCaps(113)
        target_width, target_height = image.size
    context.StartPage()
    ImageWin.Dib(image.convert("RGB")).draw(context.GetHandleOutput(),
                                           (left, top, left + target_width, top + target_height))
    context.EndPage()


def _spool(context, title, images, fit_to_printable=True):
    started = False
    try:
        context.StartDoc(title)
        started = True
        for image in images:
            _draw_page(context, image, fit_to_printable=fit_to_printable)
        context.EndDoc()
        started = False
    except Exception:
        if started:
            context.AbortDoc()
        raise
    finally:
        context.DeleteDC()


def print_label_windows(path, printer_name, settings):
    with Image.open(path) as source:
        metadata = source.info.get("erola.label.v1")
        image = ImageOps.exif_transpose(source).convert("RGB")
    label = json.loads(metadata) if metadata else None
    is_receipt = bool(label and (
        label.get("label_type") == "receipt"
        or ("label_type" not in label and label.get("barcode_value"))
    ))
    if is_receipt:
        settings = {
            **settings,
            "label_roll_width_mm": RECEIPT_LABEL_WIDTH_MM,
            "label_roll_length_mm": RECEIPT_LABEL_HEIGHT_MM,
        }
    settings = validate_label_settings(settings)
    context = _label_device_context(printer_name, settings)
    try:
        if label:
            from src.label_print import _build_product_label_image

            if is_receipt:
                from src.label_print import _build_receipt_label_image

                image = _build_receipt_label_image(
                    label["product_name"], label["lines"], label.get("barcode_value"),
                )
            else:
                dpi = 300
                roll_width = settings["label_roll_width_mm"]
                roll_length = settings["label_roll_length_mm"]
                width = round(roll_width * dpi / 25.4)
                height = round(roll_length * dpi / 25.4)
                rotate = width > height
                canvas = (height, width) if rotate else (width, height)
                image = _build_product_label_image(
                    label["product_name"], label["lot_number"], label["lines"], label.get("barcode_value"),
                    canvas_size=canvas, rotate=rotate,
                )
            pnginfo = PngImagePlugin.PngInfo()
            pnginfo.add_text("erola.label.v1", metadata)
            image.save(path, format="PNG", dpi=(300, 300), pnginfo=pnginfo)
            with Image.open(path) as printed_image:
                image = printed_image.convert("RGB")
    except Exception:
        context.DeleteDC()
        raise
    _spool(context, Path(path).stem, [image], fit_to_printable=not is_receipt)


def print_document_file(path, owner=0, printer_name=None):
    try:
        if os.name != "nt":
            subprocess.Popen(["xdg-open", str(Path(path).resolve())])
            return {"status": "opened", "message": "Document obert al visor PDF per imprimir-lo."}
        import pymupdf

        with pymupdf.open(path) as document:
            context, pages = _document_device_context(len(document), owner, printer_name)
            if context is None:
                return {"status": "cancelled", "message": "Impressió cancel·lada."}

            def images():
                for index in pages:
                    pixmap = document[index].get_pixmap(dpi=300, alpha=False, colorspace=pymupdf.csRGB)
                    yield Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)

            _spool(context, Path(path).stem, images())
        return {"status": "printed", "message": "Document enviat a la cua d'impressió."}
    except Exception as exc:
        return {"status": "error", "message": f"No s'ha pogut imprimir: {exc}\nFitxer desat a {path}"}