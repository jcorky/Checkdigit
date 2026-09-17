#!/usr/bin/env bash
# Runs the recorded Phase C benchmark set from checkdigit/. Datasets are
# regenerated with `python3 -m workspace.bench generate|generate-edi|generate-xml`.
set -u
cd "$(dirname "$0")/../checkdigit" || exit 1
export PYTHONUTF8=1
B=../bench
rm -rf $B/runs/redi $B/runs/rxml $B/runs/rmp $B/runs/r3m_clean
echo "[chain] edifact 1M"
python3 -u -m workspace.bench run-format --dataset $B/fleet_1m.edi --root $B/runs/redi
echo "[chain] xml 1M"
python3 -u -m workspace.bench run-format --dataset $B/fleet_1m.xml --root $B/runs/rxml
echo "[chain] two worker processes, edifact + xml"
python3 -u -m workspace.bench run-multiprocess --datasets $B/fleet_1m.edi $B/fleet_1m.xml --root $B/runs/rmp --workers 2
echo "[chain] csv 3M clean"
python3 -u -m workspace.bench run --dataset $B/fleet_3m.csv --root $B/runs/r3m_clean
python3 -u -m workspace.bench verify --dataset $B/fleet_3m.csv --root $B/runs/r3m_clean
echo "[chain] done"
