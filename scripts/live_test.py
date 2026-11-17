"""Run the adapt tool WITH the agent stage (Claude reviews the drawings and plans; billed) on chosen stress-test sets.

    source ~/.zshrc && .venv/bin/python scripts/live_test.py --only sem_user,bainite_L06 --rounds 2 [--sem data/user/sem_bse.png]
Results go to results/flakes/adapt/<set>_live/ (the no-API results of the stress test stay next to them), a summary
with the reviews, the plans and the token use to results/flakes/live_test_summary.json.
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stress_test import SETS, TI64_DEV, files  # noqa: E402

from src.adapt import adapt  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", required=True)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--sem")
    ap.add_argument("--dry-run", action="store_true", help="one canned agent round, no API (checks the plumbing)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    out = Path("results/flakes/live_test_summary.json" if not a.dry_run else "results/flakes/_dry_run/live_test_summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    summary = json.loads(out.read_text()) if out.exists() else {}
    for name in a.only.split(","):
        t = time.time()
        if name == "sem_user":
            images = [Path(a.sem)]
        else:
            prefixes = SETS[name]
            images = TI64_DEV if prefixes == "TI64_DEV" else files(prefixes)
        try:
            log = adapt(images, f"{name}_live" + ("_dry" if a.dry_run else ""), budget=a.budget,
                        live=0 if a.dry_run else a.rounds, dry_run=a.dry_run, workers=a.workers,
                        record_knowledge=not a.dry_run,
                        **({"results_root": Path("results/flakes/_dry_run"), "twin_root": Path("data/twins_dry_run")}
                           if a.dry_run else {}))
            tin = sum(u["input_tokens"] for u in log["usage"])
            tout = sum(u["output_tokens"] for u in log["usage"])
            hold = next(r for r in log["twin_scores"] if r["which"] == "chosen" and r["split"] == "holdout")
            key = f"{name}_live"
            summary[key] = {
                "mode": log["mode"], "mode_reason": log["mode_reason"], "twin_trusted": log["twin_trusted"],
                "set_by_eye": log.get("set_by_eye"),
                "twin_holdout_worst_family": hold["metrics"]["worst_family_tolerance_ratio"],
                "consistency": log["consistency"].get("worst_consistency_ratio"),
                "reviews": log["reviews"], "plans": log["plans"],
                "tokens": {"input": tin, "output": tout}, "usd_list_price": round((4 * tin + 20 * tout) / 1e6, 3),
                "seconds": round(time.time() - t)}
        except Exception as e:
            traceback.print_exc()
            key = f"{name}_live"
            summary[key] = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps({f"{name}_live": {k: v for k, v in summary[f"{name}_live"].items()
                                            if k not in ("reviews", "plans")}}), flush=True)
        out.write_text(json.dumps(summary, indent=2, default=str) + "\n")
