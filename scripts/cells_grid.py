"""Score several param sets of the flakes method on a coarse-cell split (development tool; prints one line each).

    .venv/bin/python scripts/cells_grid.py --split dev --grid '[{"bridge_ends_px": 0}, {}]'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cells_eval import run  # noqa: E402

KEYS = ("f1", "precision", "recall", "merges", "splits", "pocket_fp", "median_aspect_ratio_bias",
        "median_abs_order_error", "max_direction_error_deg")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/coarse_cells"))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--grid", required=True)
    ap.add_argument("--out", type=Path, default=Path("results/flakes/cells/grid"))
    a = ap.parse_args()
    for k, params in enumerate(json.loads(a.grid)):
        res = run(a.data, a.split, "flakes", params, a.out / f"{k}")
        s = res["summary"]
        print(json.dumps(params), {key: (round(s[key], 3) if isinstance(s[key], float) else s[key]) for key in KEYS}, flush=True)
