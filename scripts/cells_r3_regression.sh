#!/bin/zsh
# Round-3 regression (unattended): baselines, stress sets with fixed params, full tests.
cd "/Users/cesaregarberino/Desktop/Image Analyzer/micrograph-impact-lab"
export MICROGRAPHS_DIR=/Users/cesaregarberino/Downloads/hackathon_micrographs
F=results/flakes/cells_cal3/r3_regression
PY=.venv/bin/python
step() { echo "=== $(date +%H:%M:%S) $1" | tee -a $F/pipeline.log; }
step "code version: $($PY -c 'from src.execute import code_version; print(code_version())')"
step "baselines"
$PY -W ignore -m src.execute --dataset lamellar_synth --out results/flakes/baseline_v12/lamellar_synth > $F/baseline_lamellar.log 2>&1
$PY -W ignore -m src.execute --dataset Ti64 --out results/flakes/baseline_v12/Ti64 > $F/baseline_ti64.log 2>&1
step "stress sets, fixed params"
$PY -W ignore scripts/stress_same_params.py --adapt-root /private/tmp/claude-501/-Users-cesaregarberino-Library-Application-Support-Claude-scratch-workspaces-46ae8818-cab8-4195-8f92-430f1c157544-893f40a6-36cc-454f-b695-cb19ddae4f55-scratch-2026-11-16-7d1809/4775e40e-6e3f-41a4-8c3e-d54289a72830/scratchpad/pre/micrograph-impact-lab/results/flakes/adapt --out $F/stress_same_params_new.json > $F/stress_same_params.log 2>&1
$PY scripts/stress_same_params.py --compare results/flakes/stress_same_params/old_code.json $F/stress_same_params_new.json > $F/stress_same_params_compare.txt 2>&1
step "full test suite"
$PY -W ignore -m pytest -q -p no:cacheprovider > $F/pytest.log 2>&1
step "done"
