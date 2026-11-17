#!/bin/zsh
# Unattended run of the frozen code (2026-11-17 ~17:30): blind calibrated set, holdout, adapt fast vs thorough on a
# fresh simulated private set, stress same-params, lamellar_synth / Ti64 baselines, full tests. Each step logs and
# continues on failure. Results: results/flakes/cells_cal/frozen/
cd "/Users/cesaregarberino/Desktop/Image Analyzer/micrograph-impact-lab"
F=results/flakes/cells_cal/frozen
PY=.venv/bin/python
step() { echo "=== $(date +%H:%M:%S) $1" | tee -a $F/pipeline.log; }
step "code version: $($PY -c 'from src.execute import code_version; print(code_version())')"
step "blind calibrated set (12 images, entropy seed, params {})"
$PY -W ignore scripts/cells_blind.py --out $F/blind --data data/coarse_cells_cal_blind --per-family 4 > $F/blind.log 2>&1
step "calibrated dev set: holdout, once"
$PY -W ignore scripts/cells_eval.py --data data/coarse_cells_cal --split holdout --out $F/holdout > $F/holdout.log 2>&1
step "calibrated dev set: dev (frozen code)"
$PY -W ignore scripts/cells_eval.py --data data/coarse_cells_cal --split dev --out $F/dev > $F/dev.log 2>&1
step "simulated private set (6 sharp + 2 soft, entropy seed)"
$PY -W ignore - > $F/sim_make.log 2>&1 <<'PYEOF'
import secrets, sys
sys.path.insert(0, ".")
from dataclasses import replace
from src.synth_cells import default_specs, write_dataset
specs = default_specs(secrets.randbits(63), per_family=3, holdout=0)
specs = [s for s in specs if s.family != "soft"] + [s for s in specs if s.family == "soft"][:2]
specs = [replace(s, name=f"sim_{i}") for i, s in enumerate(specs)]
write_dataset("data/coarse_cells_cal_sim", specs, holdout=0, per_family=100)  # (all in "dev")
print("wrote", len(specs))
PYEOF
step "simulated private set: method defaults"
$PY -W ignore scripts/cells_eval.py --data data/coarse_cells_cal_sim --split dev --out $F/sim_defaults > $F/sim_defaults.log 2>&1
step "simulated private set: adapt fast (default) with the example card"
/usr/bin/time -p $PY -W ignore -m src.adapt data/coarse_cells_cal_sim/images --name cal_sim_fast --calibration data/cards/coarse_cells_example.json --no-knowledge > $F/sim_adapt_fast.log 2>&1
$PY -W ignore -m src.score_masks --labels results/flakes/adapt/cal_sim_fast/labels --masks data/coarse_cells_cal_sim/truth/masks --json $F/sim_adapt_fast_score.json > $F/sim_adapt_fast_score.log 2>&1
step "simulated private set: adapt thorough with the example card"
/usr/bin/time -p $PY -W ignore -m src.adapt data/coarse_cells_cal_sim/images --name cal_sim_thorough --thorough --calibration data/cards/coarse_cells_example.json --no-knowledge > $F/sim_adapt_thorough.log 2>&1
$PY -W ignore -m src.score_masks --labels results/flakes/adapt/cal_sim_thorough/labels --masks data/coarse_cells_cal_sim/truth/masks --json $F/sim_adapt_thorough_score.json > $F/sim_adapt_thorough_score.log 2>&1
step "stress sets: same params, frozen code"
$PY -W ignore scripts/stress_same_params.py --adapt-root /private/tmp/claude-501/-Users-cesaregarberino-Library-Application-Support-Claude-scratch-workspaces-46ae8818-cab8-4195-8f92-430f1c157544-893f40a6-36cc-454f-b695-cb19ddae4f55-scratch-2026-11-16-7d1809/4775e40e-6e3f-41a4-8c3e-d54289a72830/scratchpad/pre/micrograph-impact-lab/results/flakes/adapt --out $F/stress_same_params_new.json > $F/stress_same_params.log 2>&1
$PY scripts/stress_same_params.py --compare results/flakes/stress_same_params/old_code.json $F/stress_same_params_new.json > $F/stress_same_params_compare.txt 2>&1
step "baselines: lamellar_synth and Ti64"
$PY -W ignore -m src.execute --dataset lamellar_synth --out results/flakes/baseline_v11/lamellar_synth > $F/baseline_lamellar.log 2>&1
$PY -W ignore -m src.execute --dataset Ti64 --out results/flakes/baseline_v11/Ti64 > $F/baseline_ti64.log 2>&1
step "full test suite"
$PY -W ignore -m pytest -q -p no:cacheprovider > $F/pytest.log 2>&1
step "done"
