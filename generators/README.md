These Python scripts BUILT the two calculators (openpyxl). To modify a calculator
structurally (add tabs, rows, waterfall tiers), edit the script and re-run it, then
recalculate with LibreOffice headless:
  python3 build_acq.py --out /tmp/new_acq.xlsx   -> an acquisition model
  python3 build_dev.py --out /tmp/new_dev.xlsx   -> a development model
  soffice --headless --convert-to xlsx --outdir . <file>  (or any recalc method)
Never hand-edit formula cells in the xlsx; change the generator instead.

WRITE PATH - read before running either script.
With no arguments each script targets the repo-root master it originally built
(../Multifamily_Acquisition_Model.xlsx, ../Multifamily_Development_Model.xlsx)
and REFUSES to overwrite it. Use --out to write elsewhere, or --force to
overwrite deliberately.

Why the guard exists: openpyxl saves formulas as strings with NO cached values,
so an overwritten master reads as empty in every formula cell until it is
recalculated, and every downstream tool then silently sees None. Recalculation
needs LibreOffice *Calc*; some containers carry libreoffice-core without it
(tools/recalc.py detects this), and there the overwrite is NOT recoverable.
If you use --force, recalculate immediately and verify the Checks tab.

A master is reproducible from its generator: regenerating the acquisition model
and diffing formula strings cell by cell gives 0 differences, so neither master
has been hand-edited and this repair path is safe.
