"""Segment a split of a coarse-cell dataset with a method and score the labels with src.score_masks.

    .venv/bin/python scripts/cells_eval.py --data data/coarse_cells --split dev --method flakes --params '{}' --out results/flakes/cells/<tag>
    .venv/bin/python scripts/cells_eval.py ... --method watershed --params '{"markers": "hmaxima", "h_px": 2}'
    .venv/bin/python scripts/cells_eval.py ... --adapt   # the full adapt tool (no API), its label PNGs
Writes <out>/labels/*.png, <out>/score.json (summary, per image, per family) and prints the summary.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from src import score_masks  # noqa: E402
from src.methods import METHODS  # noqa: E402


def _one(args):
    method, image, params, out = args
    res = METHODS[method]([Path(image)], params, Path(out))
    s = res.get("image_summaries", {}).get(Path(image).stem, {})
    keep = {k: s.get(k) for k in ("flake_width_px_used", "mode", "n_scratches", "n_flakes", "n_used_orientation",
                                   "n_pockets", "out_of_focus_fraction") if k in s}
    return Path(image).stem, keep


def run(data: Path, split: str, method: str, params: dict, out: Path, workers: int = 6) -> dict:
    names = json.loads((data / "split.json").read_text())[split]
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        info = dict(ex.map(_one, [(method, str(data / "images" / f"{n}.png"), params, str(out)) for n in names]))
    res = score_masks.score_dirs(out / "labels", data / "truth" / "masks")
    fam = {}
    for n, r in res["images"].items():
        fam.setdefault(n.rsplit("_", 1)[0], []).append(r)
    res["by_family"] = {f: {"f1": 2 * sum(r["tp"] for r in rs) / max(sum(r["n_pred"] + r["n_true"] for r in rs), 1),
                            "merges": sum(r["merges"] for r in rs), "splits": sum(r["splits"] for r in rs),
                            "pocket_fp": sum(r["pocket_fp"] for r in rs),
                            "median_aspect_ratio_bias": float(np.median([r["aspect_ratio_rel_error"] for r in rs
                                                                         if r["aspect_ratio_rel_error"] is not None] or [np.nan])),
                            "max_direction_error_deg": max((r["direction_error_deg"] or 0) for r in rs)}
                        for f, rs in fam.items()}
    res["method"], res["params"], res["info"], res["seconds"] = method, params, info, round(time.time() - t0)
    (out / "score.json").write_text(json.dumps(score_masks_clean(res), indent=2) + "\n")
    return res


def score_masks_clean(o):
    if isinstance(o, dict):
        return {k: score_masks_clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [score_masks_clean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def show(res: dict) -> None:
    s = res["summary"]
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()
                      if k not in ("masks_without_labels", "labels_without_masks")}))
    for f, v in res["by_family"].items():
        print("  ", f, {k: (round(x, 3) if isinstance(x, float) else x) for k, x in v.items()})
    widths = [i.get("flake_width_px_used") for i in res["info"].values() if i.get("flake_width_px_used")]
    if widths:
        print("   width used (px): median", round(float(np.median(widths)), 1), "range", round(min(widths), 1), "-", round(max(widths), 1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/coarse_cells"))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--method", default="flakes")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    show(run(a.data, a.split, a.method, json.loads(a.params), a.out, a.workers))
