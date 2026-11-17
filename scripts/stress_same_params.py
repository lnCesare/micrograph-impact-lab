"""Method-level regression check on the stress sets: rerun each set's stored final params with the code under test.

    .venv/bin/python scripts/stress_same_params.py --adapt-root <old results/flakes/adapt> --out same_new.json
    (cd <old checkout> && <python> <this file> --adapt-root ... --code-root . --out same_old.json)
    .venv/bin/python scripts/stress_same_params.py --compare same_old.json same_new.json

The stress test runs the whole adapt tool, whose result also depends on its history:
the knowledge memory (warm starts) and the search cache (the budget counts new evaluations only, so a warm cache searches deeper).
This check holds the params fixed, so a difference can only come from the method's code.
Per set: the per-image S, mean direction, median aspect ratio and number of flakes used, and the rotation check.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

SETS = ["sem_user", "ti64_dev", "banded_islands_ma17081847", "ti_basketweave_ma19010080", "colour_lamellar_ma16020804",
        "duplex_bands_mixed", "purple_flakes_L10", "tial_lamellar_L26", "pearlite_L02", "needles_L23",
        "widmanstaetten_L18", "bainite_L06", "tial_regions_L26"]
KEYS = ("order_parameter", "mean_direction_deg", "median_aspect_ratio", "n_used_orientation")


def run_set(adapt_dir: Path, work: Path) -> dict:
    from PIL import Image

    from src import consistency
    from src.config import load_config
    from src.methods import METHODS

    log = json.loads((adapt_dir / "adapt_log.json").read_text())
    params = json.loads((adapt_dir / "params.json").read_text())
    factor = log.get("upsample_factor") or 1
    images = []
    for p in map(Path, log["images"]):
        if factor > 1 and p.parent.name != "upsampled":  # as src.adapt.upsample_if_fine (the log may hold the enlarged copies)
            im = Image.open(p)
            p_up = work / f"{p.stem}.png"
            im.resize((im.width * factor, im.height * factor), Image.BICUBIC).save(p_up)
            p = p_up
        images.append(p)
    res = METHODS["flakes"](images, params, work / "real")
    cfg = load_config(Path("config.yaml"))
    cons = consistency.check(METHODS["flakes"], images[:6], params, work / "consistency",
                             {"image_summaries": res["image_summaries"]}, cfg["flakes"]["consistency_tolerances"])
    return {"images": {n: {k: s.get(k) for k in KEYS} for n, s in res["image_summaries"].items()},
            "rotation": cons["metrics"].get("worst_consistency_ratio")}


def compare(a: dict, b: dict, tol: float = 1e-6) -> list[str]:
    lines = []
    for name in a:
        if name not in b:
            lines.append(f"{name}: missing")
            continue
        diffs = []
        ra, rb = a[name]["rotation"], b[name]["rotation"]
        if (ra is None) != (rb is None) or (ra is not None and abs(ra - rb) > tol):
            diffs.append(f"rotation {ra} -> {rb}")
        for img, va in a[name]["images"].items():
            vb = b[name]["images"].get(img, {})
            for k in KEYS:
                x, y = va.get(k), vb.get(k)
                if (x is None) != (y is None) or (x is not None and abs(x - y) > tol * max(1, abs(x))):
                    diffs.append(f"{img[:12]} {k} {x} -> {y}")
        lines.append(f"{name}: " + ("identical" if not diffs else f"{len(diffs)} differences: " + "; ".join(diffs[:6])))
    return lines


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapt-root", type=Path)
    ap.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only")
    ap.add_argument("--compare", nargs=2, type=Path)
    a = ap.parse_args()
    if a.compare:
        print("\n".join(compare(*(json.loads(p.read_text()) for p in a.compare))))
        sys.exit()
    sys.path.insert(0, str(a.code_root.resolve()))
    results = {}
    for name in (a.only.split(",") if a.only else SETS):
        with tempfile.TemporaryDirectory() as tmp:
            results[name] = run_set(a.adapt_root / name, Path(tmp))
        print(name, "rotation", results[name]["rotation"], flush=True)
        a.out.write_text(json.dumps(results, indent=1) + "\n")
