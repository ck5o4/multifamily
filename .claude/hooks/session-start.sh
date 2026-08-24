#!/bin/bash
# Installs the Python deps the underwriting tools need, so scheduled cloud runs
# can import openpyxl without a manual pip install.
# (Sweep 2026-08-24: every scheduled run was starting with all three test
# suites dead on `ModuleNotFoundError: No module named 'openpyxl'`.)
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt
