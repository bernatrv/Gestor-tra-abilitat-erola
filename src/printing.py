from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageOps


SETTINGS_PATH = Path("data") / "printing.json"
DEFAULT_SETTINGS = {"printer": "Brother QL-600", "custom_size": False, "width_mm": 62.0, "height_mm": 100.0}


def load_label_settings():
    try:
        with SETTINGS_PATH.open(encoding="utf-8") as source:
            saved = json.load(source)
        return validate_label_settings({**DEFAULT_SETTINGS, **saved})
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_SETTINGS)


def validate_label_settings(settings):
    result = dict(settings)
    result["printer"] = str(result.get("printer", "")).strip()
    for key in ("width_mm", "height_mm"):
        value = float(result[key])
        if not math.isfinite(value) or not 1 <= value <= 1000:
            raise ValueError("Les mides han d'estar entre 1 i 1000 mm.")
        result[key] = value
    result["custom_size"] = bool(result.get("custom_size"))
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
        if settings["custom_size"]:
            mode.PaperSize = win32con.DMPAPER_USER
            mode.PaperWidth = round(settings["width_mm"] * 10)
            mode.PaperLength = round(settings["height_mm"] * 10)
            mode.Fields |= win32con.DM_PAPERSIZE | win32con.DM_PAPERWIDTH | win32con.DM_PAPERLENGTH
        result = win32print.DocumentProperties(0, handle, printer_name, mode, mode,
                                              win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
        if result != 1:
            raise OSError("El controlador no ha acceptat la configuració de l'etiqueta.")
        if settings["custom_size"] and (
            abs(mode.PaperWidth - round(settings["width_mm"] * 10)) > 10
            or abs(mode.PaperLength - round(settings["height_mm"] * 10)) > 10
        ):
            raise ValueError("La mida no és compatible amb el controlador. Tria el rotlle a Preferències del controlador.")
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


def _document_device_context(page_count, owner):
    import win32ui

    dialog = _PrintDialog()
    dialog.lStructSize = ctypes.sizeof(dialog)
    if not owner:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetActiveWindow.restype = wintypes.HWND
        owner = user.GetActiveWindow()
    dialog.hwndOwner = owner
    dialog.Flags = 0x100 | 0x40000 | 0x10 | 0x4 | 0x100000
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


def _draw_page(context, image):
    from PIL import ImageWin

    width, height = context.GetDeviceCaps(8), context.GetDeviceCaps(10)
    if width <= 0 or height <= 0:
        raise OSError("La impressora no indica una àrea imprimible vàlida.")
    scale = min(width / image.width, height / image.height)
    target_width, target_height = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    left, top = (width - target_width) // 2, (height - target_height) // 2
    context.StartPage()
    ImageWin.Dib(image.convert("RGB")).draw(context.GetHandleOutput(),
                                           (left, top, left + target_width, top + target_height))
    context.EndPage()


def _spool(context, title, images):
    started = False
    try:
        context.StartDoc(title)
        started = True
        for image in images:
            _draw_page(context, image)
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
        image = ImageOps.exif_transpose(source).convert("RGB")
    context = _label_device_context(printer_name, validate_label_settings(settings))
    _spool(context, Path(path).stem, [image])


def print_document_file(path, owner=0):
    try:
        if os.name != "nt":
            subprocess.Popen(["xdg-open", str(Path(path).resolve())])
            return {"status": "opened", "message": "Document obert al visor PDF per imprimir-lo."}
        import pymupdf

        with pymupdf.open(path) as document:
            context, pages = _document_device_context(len(document), owner)
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