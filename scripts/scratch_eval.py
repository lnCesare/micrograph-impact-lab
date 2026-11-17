"""Scratch detection scored on a dataset with exact scratch truth (coarse cells, lamellar_synth, twins).

    .venv/bin/python scripts/scratch_eval.py --data data/coarse_cells --names scratched_0,scratched_1 --params '{}'
Per image and pooled: pixel recall of the crossing scratches (hairline and thick separately when the dataset says
which is which), precision of the detected scratch pixels, and false scratch pixels on images without scratches.
"""

import argparse
import json
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from scipy import ndimage as ndi  # noqa: E402

from src.methods import METHODS  # noqa: E402


def _one(args):
    image, params = args
    with tempfile.TemporaryDirectory() as tmp:
        res = METHODS["flakes"]([Path(image)], params, Path(tmp))
    name = Path(image).stem
    return name, res["defect_maps"][name] == 1, len(res["defects"][name])


def run(data: Path, names: list[str], params: dict, workers: int = 6) -> dict:
    with ProcessPoolExecutor(workers) as ex:
        found = {n: (m, k) for n, m, k in ex.map(_one, [(str(data / "images" / f"{n}.png"), params) for n in names])}
    rows = {}
    for n in names:
        code = np.array(Image.open(data / "truth" / "scratch" / f"{n}.png"))
        f, k = found[n]
        f = f[: code.shape[0], : code.shape[1]]
        grow = lambda m: ndi.binary_dilation(m, iterations=2)
        truth = code > 0
        rows[n] = {"lines_found": k, "truth_px": int(truth.sum()), "found_px": int(f.sum()),
                   "recall_crossing": float((grow(f) & (code == 255)).sum() / max((code == 255).sum(), 1)) if (code == 255).any() else None,
                   "precision": float((f & grow(truth)).sum() / f.sum()) if f.any() else None,
                   "false_px": int((f & ~grow(truth)).sum())}
    cross = [r for r in rows.values() if r["recall_crossing"] is not None]
    pooled = {"recall_crossing_median": float(np.median([r["recall_crossing"] for r in cross])) if cross else None,
              "precision_pooled": (sum(r["found_px"] - r["false_px"] for r in rows.values()) / max(sum(r["found_px"] for r in rows.values()), 1)),
              "false_px_total": sum(r["false_px"] for r in rows.values()),
              "false_px_on_clean": sum(r["false_px"] for r in rows.values() if r["truth_px"] == 0)}
    return {"pooled": pooled, "images": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path("data/coarse_cells"))
    ap.add_argument("--names", required=True)
    ap.add_argument("--params", default="{}")
    a = ap.parse_args()
    res = run(a.data, a.names.split(","), json.loads(a.params))
    print(json.dumps(res["pooled"]))
    for n, r in res["images"].items():
        print("  ", n, r)
