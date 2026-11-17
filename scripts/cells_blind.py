"""Blind test on coarse cells: freeze the method (code version, params) first, then generate a fresh coarse-cell set
from an OS-entropy seed (not printed, not stored), run the method once, score it with src.score_masks.

    .venv/bin/python scripts/cells_blind.py --out results/flakes/cells/blind --data data/coarse_cells_blind
The set is single-use: once its scores are known, a change to the method needs a new blind set.
"""

import argparse
import json
import secrets
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cells_eval import run, score_masks_clean  # noqa: E402

from src.execute import code_version  # noqa: E402
from src.synth_cells import default_specs, write_dataset  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--params", default="{}")
    ap.add_argument("--per-family", type=int, default=4)
    a = ap.parse_args()
    if a.data.exists():
        raise SystemExit(f"{a.data} exists: a blind set is generated once")
    a.out.mkdir(parents=True, exist_ok=True)
    frozen = {"time": datetime.now().isoformat(timespec="seconds"), "code_version": code_version(),
              "params": json.loads(a.params), "per_family": a.per_family}
    (a.out / "frozen.json").write_text(json.dumps(frozen, indent=2) + "\n")  # written before the set exists
    write_dataset(a.data, default_specs(secrets.randbits(63), a.per_family, holdout=a.per_family),
                  holdout=a.per_family, per_family=a.per_family)  # (all images in "holdout")
    res = run(a.data, "holdout", "flakes", frozen["params"], a.out)
    res["frozen"] = frozen
    (a.out / "score.json").write_text(json.dumps(score_masks_clean(res), indent=2) + "\n")
    s = res["summary"]
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()
                      if k not in ("masks_without_labels", "labels_without_masks")}))
    for f, v in res["by_family"].items():
        print("  ", f, {k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.items()})
