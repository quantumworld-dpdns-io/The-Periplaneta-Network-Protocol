#!/usr/bin/env bash
# One-command reproduction of the sensor-network design study.
#   bash netsim/reproduce.sh          # full sweep (several minutes)
#   bash netsim/reproduce.sh --quick  # smoke run
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r netsim/requirements.txt
fi
.venv/bin/python -m pytest -q netsim/tests
.venv/bin/python -m netsim.cli describe > netsim/out/model.json 2>/dev/null || { mkdir -p netsim/out; .venv/bin/python -m netsim.cli describe > netsim/out/model.json; }
.venv/bin/python -m netsim.cli sweep "$@"
# keep a committed copy of the headline outputs next to the other generated reports
mkdir -p docs/netsim
cp netsim/out/RESULTS.md docs/netsim/RESULTS.md
cp netsim/out/design_curves_noise0.15.png docs/netsim/design_curves_noise0.15.png
# submission artefacts: every number in the abstract must match summary.csv; one page at >= 12 pt; subtitles from the script
.venv/bin/python -m netsim.verify_abstract
.venv/bin/python -m netsim.make_abstract_pdf
.venv/bin/python -m netsim.make_subtitles
echo "results: netsim/out/{RESULTS.md,summary.csv,runs.csv,design_curves_*.png}; committed copies + ABSTRACT.pdf + VIDEO_SCRIPT.srt in docs/netsim/"
