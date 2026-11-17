"""Run the adapt tool on several very different sets of flake images (outer-loop stress test, no API by default).

    .venv/bin/python scripts/stress_test.py [--budget 20] [--only name1,name2]
Images come from the hackathon dataset (MICROGRAPHS_DIR); results go to results/flakes/adapt/<set>/.
"""

import argparse
import glob
import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adapt import adapt  # noqa: E402

D = Path(os.environ.get("MICROGRAPHS_DIR", "/Users/cesaregarberino/Downloads/hackathon_micrographs"))
L = D / "00_LAMELLAR_and_ALIGNED"
TI64_DEV = [D / "01_BEST_Ti64_lamellar_bimodal" / "data" / n
            for n in json.loads((Path(__file__).resolve().parents[1] / "data" / "splits.json").read_text())["Ti64"]["dev"]]
SETS = {
    "ti64_dev": "TI64_DEV",
    "banded_islands_ma17081847": ["L03", "L07", "L11", "L14", "L16", "L17", "L19"],
    "ti_basketweave_ma19010080": ["L08", "L20", "L21", "L22"],
    "colour_lamellar_ma16020804": ["L09", "L12", "L13"],
    "duplex_bands_mixed": ["L01", "L04", "L05", "L24"],
    "purple_flakes_L10": ["L10"],
    "tial_lamellar_L26": ["L26"],
    "pearlite_L02": ["L02"],
    "needles_L23": ["L23"],
    "widmanstaetten_L18": ["L18"],
    "bainite_L06": ["L06"],
    "tial_regions_L26": ["L26"],  # the TiAl image again, with the regions reading set by hand
}
MODES = {"tial_regions_L26": "regions"}


def files(prefixes):
    return [Path(glob.glob(str(L / f"{p}__*"))[0]) for p in prefixes]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--only")
    ap.add_argument("--sem", help="path of the scientist's SEM image, adapted as set 'sem_user'")
    a = ap.parse_args()
    todo = dict(SETS)
    if a.only:
        todo = {k: v for k, v in todo.items() if k in a.only.split(",")}
    out_file = Path("results/flakes/stress_test_summary.json")
    # a partial rerun (--only) updates its sets in the summary and keeps the others
    summary = json.loads(out_file.read_text()) if a.only and out_file.exists() else {}
    if a.sem:
        todo = {"sem_user": None, **todo} if not a.only or "sem_user" in a.only else todo
    for name, prefixes in todo.items():
        t = time.time()
        images = [Path(a.sem)] if name == "sem_user" else (TI64_DEV if prefixes == "TI64_DEV" else files(prefixes))
        try:
            log = adapt(images, name, budget=30 if name == "ti64_dev" else a.budget, mode=MODES.get(name))
            hold = next(r for r in log["twin_scores"] if r["which"] == "chosen" and r["split"] == "holdout")
            summary[name] = {"mode": log["mode"], "mode_reason": log["mode_reason"],
                             "twin_realism_ok": log["twin"]["realism_ok"],
                             "twin_profile_mismatch": log["twin"].get("realism_worst_log_ratio"),
                             "twin_structure_mismatch": log["twin"].get("structure_worst_log_ratio"),
                             "twin_trusted": log.get("twin_trusted"),
                             "upsample_factor": log.get("upsample_factor"),
                             "twin_holdout_worst_family": hold["metrics"]["worst_family_tolerance_ratio"],
                             "twin_holdout_pooled": hold["metrics"]["worst_tolerance_ratio"],
                             "consistency": log["consistency"].get("worst_consistency_ratio"),
                             "consistency_ratios": log["consistency"].get("consistency_ratios"),
                             "median_n_flakes_used": log["consistency"].get("median_n_flakes_used"),
                             "warm_started_from": log.get("warm_start"),
                             "two_scales": log.get("two_scales"),
                             "seconds": round(time.time() - t)}
        except Exception as e:  # keep going: one failing set must not stop the others
            traceback.print_exc()
            summary[name] = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps({name: summary[name]}), flush=True)
    out_file.write_text(json.dumps(summary, indent=2) + "\n")
