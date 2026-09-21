"""Recalculate a workbook with LibreOffice headless, then scan for formula
errors and read the Checks tab. openpyxl drops cached values on save, so this
step is mandatory before any number is trusted."""

import shutil
import subprocess
import tempfile
from pathlib import Path

import openpyxl

from cellmap import ERROR_TOKENS

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
]

_WITHOUT_IT = (
    "Without it, written inputs are saved but every formula reads as empty."
)

INSTALL_HINT = (
    "LibreOffice not found. Install it (required to recalculate formulas):\n"
    "  macOS:  brew install --cask libreoffice\n"
    "  Debian: sudo apt-get install libreoffice-calc\n"
    "or download from https://www.libreoffice.org/download/\n"
    + _WITHOUT_IT
)

NO_CALC_HINT = (
    "LibreOffice is installed at {soffice}, but the Calc import filter is not.\n"
    "`soffice --convert-to xlsx` fails with 'source file could not be loaded' on\n"
    "every spreadsheet, including a trivial one. libreoffice-core alone is not enough:\n"
    "  Debian: sudo apt-get install libreoffice-calc\n"
    "  macOS:  the .app bundle already includes it\n"
    + _WITHOUT_IT
)


class RecalcUnavailable(Exception):
    pass


def find_soffice():
    for p in SOFFICE_CANDIDATES:
        if Path(p).exists():
            return p
    for name in ("soffice", "libreoffice"):
        w = shutil.which(name)
        if w:
            return w
    return None


def calc_filter_present(soffice):
    """Is the Calc (spreadsheet) import/export filter installed alongside soffice?

    Sweep 2026-09-21: this container carries libreoffice-core but not
    libreoffice-calc. `soffice` is on PATH, so find_soffice() reported recalc as
    available, and every conversion then died with the opaque 'source file could
    not be loaded' - after a full intake run. Detection is fail-open: an
    unrecognised install layout returns True rather than blocking a working one.
    """
    program = Path(soffice).resolve().parent
    if not program.is_dir():
        return True
    for pattern in ("*scfilt*", "*calc*"):
        if any(program.glob(pattern)):
            return True
    # A real install always ships the Calc library next to the binary; a program
    # dir we can read, with neither marker, is missing the filter.
    return False


def recalc(path, timeout=180):
    """Recalculate in place. Returns the path. Raises RecalcUnavailable."""
    soffice = find_soffice()
    if not soffice:
        raise RecalcUnavailable(INSTALL_HINT)
    if not calc_filter_present(soffice):
        raise RecalcUnavailable(NO_CALC_HINT.format(soffice=soffice))
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory() as td:
        cmd = [soffice, "--headless", "--norestore", "--convert-to", "xlsx",
               "--outdir", td, str(path)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RecalcUnavailable(
                f"LibreOffice timed out after {timeout}s recalculating {path.name}. "
                "An already-open or hung LibreOffice instance is the usual cause - "
                "close it and re-run.") from None
        produced = Path(td) / path.name
        if not produced.exists():
            raise RecalcUnavailable(
                f"LibreOffice produced no output.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            )
        shutil.copy2(produced, path)
    return path


def scan_errors(path):
    """-> list of 'Sheet!Cell = #ERR' strings."""
    wb = openpyxl.load_workbook(path, data_only=True)
    found = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.strip() in ERROR_TOKENS:
                    found.append(f"{ws.title}!{c.coordinate} = {v.strip()}")
    return found


def read_checks(path, spec):
    sheet, r0, r1 = spec["checks"]
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[sheet]
    out = []
    for r in range(r0, r1 + 1):
        out.append((ws.cell(r, 1).value, ws.cell(r, 2).value))
    return out


def read_values(path, refs):
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {}
    for name, ref in refs.items():
        sheet, coord = ref.split("!")
        out[name] = wb[sheet][coord].value
    return out
