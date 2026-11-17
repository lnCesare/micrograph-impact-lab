"""Measure flakes on any images, outside the loop: the tool a scientist runs on their own micrographs.

    python -m src.measure path/to/images/*.png --out measurements/run1
    python -m src.measure img.tif --params '{"crop_bottom_px": 60, "um_per_px": 0.14}' --out measurements/run2
    python -m src.measure img.png --from-iteration 7 --out measurements/run3   # params of results/flakes/iter_007

Writes flakes.csv (one row per flake), image_summaries.csv (one row per image: mean direction, order parameter,
misalignment, aspect ratio, ...), scratches.csv, and a colour drawing per image in overlays/.
"""

import argparse
import json
from pathlib import Path

import pandas as pd

from src.methods import METHODS


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--params", default="{}", help="JSON object of flakes params (see src/methods/flakes.py)")
    parser.add_argument("--from-iteration", type=int, help="use the params of results/flakes/iter_NNN")
    parser.add_argument("--results", type=Path, default=Path("results/flakes"))
    args = parser.parse_args(argv)

    params = {}
    if args.from_iteration:
        params = json.loads((args.results / f"iter_{args.from_iteration:03d}" / "plan.json").read_text())["params"]
    params.update(json.loads(args.params))
    result = METHODS["flakes"](args.images, params, args.out, overlay=[p.stem for p in args.images])
    args.out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"image": n, "flake": i + 1, **f} for n, fl in result["predictions"].items()
                  for i, f in enumerate(fl)]).to_csv(args.out / "flakes.csv", index=False)
    summaries = pd.DataFrame([{"image": n, **s} for n, s in result["image_summaries"].items()])
    summaries.to_csv(args.out / "image_summaries.csv", index=False, float_format="%.4g")
    scratches = [{"image": n, **d} for n, items in result["defects"].items() for d in items]
    if scratches:
        pd.DataFrame(scratches).to_csv(args.out / "scratches.csv", index=False)
    (args.out / "params.json").write_text(json.dumps(result["params_used"], indent=2) + "\n")
    cols = ["image", "mean_direction_deg", "order_parameter", "local_order_parameter", "mean_misalignment_deg",
            "median_aspect_ratio", "n_used_orientation", "n_scratches", "polarity"]
    print(summaries[cols].to_string(index=False))


if __name__ == "__main__":
    main()
