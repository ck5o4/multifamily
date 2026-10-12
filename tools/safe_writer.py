"""Guarded openpyxl writer. Refuses to touch any cell that is not a documented
blue input cell or inside a declared paste range, and refuses any cell that
currently holds a formula."""

import shutil
from pathlib import Path
import cellmap

import openpyxl

from cellmap import BLUE_RGB


class FormulaGuardError(Exception):
    pass


def parse_ref(ref):
    sheet, cell = ref.split("!")
    return sheet, cell


class ModelWriter:
    def __init__(self, path, spec):
        self.path = Path(path)
        self.spec = spec
        self.wb = openpyxl.load_workbook(self.path)
        self.writes = []
        self._ranges = spec.get("paste_ranges", [])

    def _in_paste_range(self, sheet, row, col):
        for s, r0, r1, c0, c1 in self._ranges:
            if s == sheet and r0 <= row <= r1 and c0 <= col <= c1:
                return True
        return False

    def _is_blue_input(self, cell):
        # Compare on RGB only. openpyxl versions disagree on the leading alpha
        # byte (FF0000FF vs 000000FF) and the workbooks were built across two.
        f = cell.font
        if not f or not f.color:
            return False
        rgb = getattr(f.color, "rgb", None)
        return isinstance(rgb, str) and rgb.upper()[-6:] == BLUE_RGB[-6:]

    def _assert_writable(self, sheet, cell, value=None):
        cur = cell.value
        if isinstance(cur, str) and cur.startswith("="):
            raise FormulaGuardError(
                f"{sheet}!{cell.coordinate} holds a formula ({cur[:40]}...) - refusing to write"
            )
        # Sweep 2026-10-05: the guard inspected only the EXISTING value, never
        # the incoming one, so a string starting with "=" could be written INTO
        # a documented input cell and openpyxl would store it with
        # data_type='f' - a live formula in an input cell. Reachable from a
        # file, not just a hand-written call: parse_rent_roll keys an
        # unresolved row on its raw label and write_rent_roll puts that label
        # verbatim into Inputs!F3:F10, so a broker CSV row
        # "103,=SUM(H3:H10)*9,850,900" landed `=SUM(H3:H10)*9` in Inputs!F4.
        # In the development model Inputs!H3 does VLOOKUP(F3, ...), so column F
        # is load-bearing.
        if isinstance(value, str) and value.lstrip().startswith("="):
            raise FormulaGuardError(
                f"refusing to write a FORMULA into {sheet}!{cell.coordinate}: "
                f"{value[:40]!r}. Input cells hold values, not formulas; a "
                f"parsed label or rent that looks like this is a parse error "
                f"or a spreadsheet-injection attempt in the source file."
            )
        # Sweep 2026-10-12: the 10-05 guard above tests only a leading "=", so
        # an Excel error token still got through, and it does not stay inert.
        # openpyxl stores "#N/A" with data_type='e'; the LibreOffice recalc
        # round-trip promotes it to `=#N/A` with data_type='f' - a live formula
        # in a documented input cell, the exact invariant the 10-05 fix was
        # written to establish. Reproduced on Inputs!F3 via write_rent_roll from
        # a broker roll carrying #N/A in the Unit Type column (the ordinary
        # cause is a broken VLOOKUP in the seller's own export); #REF! behaves
        # identically. scan_errors does catch the result and MODEL INTEGRITY
        # reads FAIL, so no false PURSUE - but the write should never land.
        # (Checked and NOT a hole: "+SUM(A1)" and "@SUM(A1)" round-trip as
        # strings with data_type='s', so the CSV-injection prefixes need no
        # guard of their own.)
        if isinstance(value, str) and value.strip() in cellmap.ERROR_TOKENS:
            raise FormulaGuardError(
                f"refusing to write the ERROR TOKEN {value.strip()!r} into "
                f"{sheet}!{cell.coordinate}. openpyxl stores it as an error and "
                f"the LibreOffice recalc turns it into a live formula in an "
                f"input cell. It means the source file's own formula was broken "
                f"- fix or drop that row rather than carrying the error in."
            )
        if self._is_blue_input(cell) or self._in_paste_range(sheet, cell.row, cell.column):
            return
        raise FormulaGuardError(
            f"{sheet}!{cell.coordinate} is not a documented input cell - refusing to write"
        )

    def set(self, ref, value):
        sheet, coord = parse_ref(ref)
        ws = self.wb[sheet]
        cell = ws[coord]
        self._assert_writable(sheet, cell, value)
        old = cell.value
        cell.value = value
        self.writes.append((ref, old, value))

    def set_rc(self, sheet, row, col, value):
        ws = self.wb[sheet]
        cell = ws.cell(row=row, column=col)
        self._assert_writable(sheet, cell, value)
        old = cell.value
        cell.value = value
        self.writes.append((f"{sheet}!{cell.coordinate}", old, value))

    def clear_block(self, sheet, r0, r1, c0, c1):
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                self.set_rc(sheet, r, c, None)

    def save(self, dest=None):
        out = Path(dest) if dest else self.path
        self.wb.save(out)
        return out


def clone_model(master_path, dest_path):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(master_path, dest_path)
    return dest_path
