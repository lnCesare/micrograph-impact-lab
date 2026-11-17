"""Qualitative check of the flakes method on public coarse-cell micrographs (no ground truth, no API).

    .venv/bin/python scripts/public_cells_check.py --out results/flakes/cells/public_E21 [--code-root <other checkout>] [--tag after]
Images: Zhang et al. 2023, Materials 16(3) 1222, doi:10.3390/ma16031222, Fig. 6 f, g, i (SLM AlSi10Mg, optical), CC BY 4.0;
the hackathon copies `E21_elongated__10.3390_ma16031222_g006_{f,g,i}_fullres.jpg` in MICROGRAPHS_DIR/00_COARSE_ELONGATED_GRAINS.
Each image is measured as published (438 x 289 px) and enlarged x3 (bicubic, about the size of the private coarse-cell images).
`--code-root` runs another copy of the code (e.g. a snapshot from before a change) for a before/after comparison;
`--params` sets method params (e.g. '{"mode": "boundaries"}').
Writes <out>/<tag>.json (per-image summary) and, with --draw <tag1>,<tag2>, a small side-by-side figure <out>/<tag1>_vs_<tag2>.jpg.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

D = Path(os.environ.get("MICROGRAPHS_DIR", "/Users/cesaregarberino/Downloads/hackathon_micrographs")) / "00_COARSE_ELONGATED_GRAINS"
NAMES = [f"E21_elongated__10.3390_ma16031222_g006_{p}_fullres" for p in "fgi"]
KEEP = ("mode", "flake_width_px_used", "n_flakes", "order_parameter", "mean_direction_deg", "median_aspect_ratio",
        "flake_area_fraction", "n_pockets", "n_scratches", "out_of_focus_fraction")


def inputs(work: Path) -> list[Path]:
    paths = []
    for n in NAMES:
        im = Image.open(D / f"{n}.jpg").convert("RGB")
        paths.append(work / f"{n}.png")
        im.save(paths[-1])
        paths.append(work / f"{n}_x3.png")
        im.resize((im.width * 3, im.height * 3), Image.BICUBIC).save(paths[-1])
    return paths


def measure(code_root: Path, params: dict, out: Path, tag: str) -> None:
    sys.path.insert(0, str(code_root))
    from src.methods import METHODS
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        res = METHODS["flakes"](inputs(work), params, out / f"{tag}_run")
    rows = {n: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items() if k in KEEP}
            for n, s in res["image_summaries"].items()}
    (out / f"{tag}.json").write_text(json.dumps({"code_root": str(code_root), "params": params, "images": rows}, indent=2) + "\n")
    for n, r in rows.items():
        print(n.replace("E21_elongated__10.3390_ma16031222_g006_", ""), r)


def draw(out: Path, tags: list[str], width: int = 1400) -> None:
    from skimage.segmentation import find_boundaries
    rows = []
    for n in NAMES:
        rgb = np.array(Image.open(D / f"{n}.jpg").convert("RGB")).repeat(3, 0).repeat(3, 1)
        panels = [rgb]
        for t in tags:
            lab = np.array(Image.open(out / f"{t}_run" / "labels" / f"{n}_x3.png"))
            lab = np.array(Image.fromarray(lab).resize(rgb.shape[1::-1], Image.NEAREST))
            o = rgb.copy()
            o[lab == 0] = (o[lab == 0] * 0.45).astype(np.uint8)  # not counted as a flake: darkened
            o[find_boundaries(lab, mode="inner")] = (255, 0, 0)
            panels.append(o)
        rows.append(np.concatenate([np.pad(x, ((6, 6), (6, 6), (0, 0)), constant_values=255) for x in panels], 1))
    im = Image.fromarray(np.concatenate(rows, 0))
    im.thumbnail((width, width))
    im.save(out / f"{'_vs_'.join(tags)}.jpg", quality=82)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("results/flakes/cells/public_E21"))
    ap.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--params", default="{}")
    ap.add_argument("--tag", default="after")
    ap.add_argument("--draw", help="comma-separated tags already measured, e.g. before,after")
    a = ap.parse_args()
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.draw:
        draw(a.out, a.draw.split(","))
    else:
        measure(a.code_root.resolve(), json.loads(a.params), a.out, a.tag)
