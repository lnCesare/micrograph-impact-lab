"""Compare two stress-test summaries set by set (regression check after a method change).

    .venv/bin/python scripts/stress_compare.py results/flakes/stress_v4_before_coarse/stress_test_summary.json results/flakes/stress_test_summary.json
Ratios are measured / tolerance (≤ 1 passes): `consistency` is the worst rotation-consistency ratio on the real images,
`twin_holdout_worst_family` the worst family on the twin's holdout. A set is flagged when a ratio that passed now fails,
when a ratio grows by more than `--slack` (default 0.25, in tolerance units), when the reading or twin trust changes,
or when the median flake count changes by more than a factor 1.5.
"""

import argparse
import json
from pathlib import Path

RATIOS = ("consistency", "twin_holdout_worst_family")


def compare(before: dict, after: dict, slack: float = 0.25) -> list[dict]:
    rows = []
    for name in before:
        b, a = before[name], after.get(name)
        if a is None:
            rows.append({"set": name, "flags": ["missing after"]})
            continue
        flags = []
        for k in RATIOS:
            x, y = b.get(k), a.get(k)
            if x is None or y is None:
                continue
            if x <= 1 < y:
                flags.append(f"{k} now fails ({x:.3f} -> {y:.3f})")
            elif y - x > slack:
                flags.append(f"{k} worse by {y - x:.3f} ({x:.3f} -> {y:.3f})")
        for k in ("mode", "twin_trusted", "upsample_factor"):
            if b.get(k) != a.get(k):
                flags.append(f"{k} {b.get(k)} -> {a.get(k)}")
        nb, na = b.get("median_n_flakes_used"), a.get("median_n_flakes_used")
        if nb and na and max(nb, na) / max(min(nb, na), 1) > 1.5:
            flags.append(f"median flakes {nb} -> {na}")
        rows.append({"set": name, "flags": flags,
                     **{f"{k}_before": b.get(k) for k in RATIOS}, **{f"{k}_after": a.get(k) for k in RATIOS},
                     "flakes_before": nb, "flakes_after": na, "mode": a.get("mode")})
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--slack", type=float, default=0.25)
    a = ap.parse_args()
    rows = compare(json.loads(a.before.read_text()), json.loads(a.after.read_text()), a.slack)
    f = lambda v: "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
    print("| set | reading | rotation consistency | twin holdout worst family | median flakes | flags |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        if "mode" not in r:
            print(f"| {r['set']} | | | | | {'; '.join(r['flags'])} |")
            continue
        print(f"| {r['set']} | {r['mode']} | {f(r['consistency_before'])} → {f(r['consistency_after'])} "
              f"| {f(r['twin_holdout_worst_family_before'])} → {f(r['twin_holdout_worst_family_after'])} "
              f"| {f(r['flakes_before'])} → {f(r['flakes_after'])} | {'; '.join(r['flags']) or 'ok'} |")
    print(f"\n{sum(bool(r['flags']) for r in rows)} of {len(rows)} sets flagged")
