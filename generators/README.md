These Python scripts BUILT the two calculators (openpyxl). To modify a calculator
structurally (add tabs, rows, waterfall tiers), edit the script and re-run it, then
recalculate with LibreOffice headless:
  python3 build_acq.py   -> Multifamily_Acquisition_Model.xlsx
  python3 build_dev.py   -> Multifamily_Development_Model.xlsx
  soffice --headless --convert-to xlsx --outdir . <file>  (or any recalc method)
Never hand-edit formula cells in the xlsx; change the generator instead.
Note: scripts write to /home/claude/models/ - change the save path at the bottom
of each script to this folder.
