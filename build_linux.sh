#!/usr/bin/env bash
set -eu

cd -- "$(dirname -- "$0")"

if [ "$(uname -s)" != "Linux" ]; then
    printf '%s\n' "Aquest executable s'ha de construir en Linux, no en Windows." >&2
    exit 1
fi

PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    printf '%s\n' 'Cal Python 3.11 o superior, amb venv i Tkinter, per construir erola producció.' >&2
    exit 1
fi

if ! "$PYTHON" -c 'import tkinter' >/dev/null 2>&1; then
    printf '%s\n' "Falta Tkinter. A Linux Mint, instal·la el paquet python3-tk corresponent al teu Python." >&2
    exit 1
fi

if ! "$PYTHON" -m venv .venv-linux; then
    printf '%s\n' "No s'ha pogut crear l'entorn virtual. Instal·la python3-venv per a la versió de Python utilitzada." >&2
    exit 1
fi

.venv-linux/bin/python -m pip install -r requirements.txt pyinstaller
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/erola-build.XXXXXX")"
trap 'rm -rf -- "$WORK_DIR"' EXIT
.venv-linux/bin/python -m PyInstaller --noconfirm --distpath dist/linux --workpath "$WORK_DIR" GestorTracabilitat.spec
chmod +x 'dist/linux/erola producció'
printf '\n%s\n' 'Executable generat a: dist/linux/erola producció'