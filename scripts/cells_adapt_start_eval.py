"""Score the method on a coarse-cell split with the start settings src.adapt gives each image (src.twin.set_profile
of the image, then src.adapt.resolved_start), instead of the method's own "auto" settings: the path the private
round-2 rerun took (in commit 2f5ed6b that path carried flake_width_px null, so the method lost the width).

    .venv/bin/python scripts/cells_adapt_start_eval.py --data data/coarse_cells_cal3 --split dev --out results/...
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _one(args):
    from src import twin
    from src.adapt import resolved_start
    from src.methods import METHODS

    image, out = args
    prof = twin.set_profile([Path(image)])
    start = resolved_start(prof, "boundaries")
    res = METHODS["flakes"]([Path(image)], start, Path(out))
    s = res["image_summaries"][Path(image).stem]
    return Path(image).stem, {"flake_width_px_given": start.get("flake_width_px"),
                              "flake_width_px_used": s.get("flake_width_px_used"),
                              "profile_width_px": prof["by_mode"]["boundaries"]["flake_width_px"],
                              "soft_fraction": s.get("soft_fraction"), "n_flakes": s.get("n_flakes")}


if __name__ == "__main__":
    import numpy as np
    from cells_eval import score_masks_clean, show
    from src import score_masks

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    names = json.loads((a.data / "split.json").read_text())[a.split]
    a.out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(a.workers) as ex:
        info = dict(ex.map(_one, [(str(a.data / "images" / f"{n}.png"), str(a.out)) for n in names]))
    res = score_masks.score_dirs(a.out / "labels", a.data / "truth" / "masks")
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
    res["method"], res["params"], res["info"] = "flakes (src.adapt start settings)", None, info
    (a.out / "score.json").write_text(json.dumps(score_masks_clean(res), indent=2) + "\n")
    show(res)
