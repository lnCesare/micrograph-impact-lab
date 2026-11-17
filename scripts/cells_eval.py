"""Segment a split of a coarse-cell dataset with a method and score the labels with src.score_masks.

    .venv/bin/python scripts/cells_eval.py --data data/coarse_cells --split dev --method flakes --params '{}' --out results/flakes/cells/<tag>
    .venv/bin/python scripts/cells_eval.py ... --method watershed --params '{"markers": "hmaxima", "h_px": 2}'
    .venv/bin/python scripts/cells_eval.py ... --adapt   # the full adapt tool (no API), its label PNGs
Writes <out>/labels/*.png, <out>/score.json (summary, per image, per family) and prints the summary.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# --code-root: the method of another checkout (e.g. a git worktree of an old commit) makes the labels in a subprocess
# that imports only that checkout's src.methods; the scoring stays with this checkout's src.score_masks
CODE_ROOT = os.environ.get("CELLS_CODE_ROOT")
sys.path.insert(0, CODE_ROOT or str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402


def _one(args):
    from src.methods import METHODS

    method, image, params, out = args
    res = METHODS[method]([Path(image)], params, Path(out))
    s = res.get("image_summaries", {}).get(Path(image).stem, {})
    keep = {k: s.get(k) for k in ("flake_width_px_used", "mode", "n_scratches", "n_flakes", "n_used_orientation",
                                   "n_pockets", "out_of_focus_fraction", "soft_fraction") if k in s}
    return Path(image).stem, keep


def labels(data: Path, split: str, method: str, params: dict, out: Path, workers: int = 6) -> dict:
    names = json.loads((data / "split.json").read_text())[split]
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(workers) as ex:
        return dict(ex.map(_one, [(method, str(data / "images" / f"{n}.png"), params, str(out)) for n in names]))


def run(data: Path, split: str, method: str, params: dict, out: Path, workers: int = 6, code_root: Path | None = None) -> dict:
    from src import score_masks

    t0 = time.time()
    data, out = Path(data).resolve(), Path(out).resolve()
    if code_root:
        out.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--labels-only", "--data", str(data), "--split", split,
                        "--method", method, "--params", json.dumps(params), "--out", str(out), "--workers", str(workers)],
                       check=True, cwd=str(code_root), env={**os.environ, "CELLS_CODE_ROOT": str(Path(code_root).resolve())})
        info = json.loads((out / "info.json").read_text())
    else:
        info = labels(data, split, method, params, out, workers)
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
    res["code_root"] = str(code_root) if code_root else None
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
    ap.add_argument("--code-root", type=Path, help="run the method of another checkout (e.g. a worktree of an old commit)")
    ap.add_argument("--labels-only", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.labels_only:
        info = labels(a.data, a.split, a.method, json.loads(a.params), a.out, a.workers)
        (a.out / "info.json").write_text(json.dumps(score_masks_clean(info), indent=2) + "\n")
        sys.exit()
    show(run(a.data, a.split, a.method, json.loads(a.params), a.out, a.workers, a.code_root))
