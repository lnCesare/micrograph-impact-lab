"""Knowledge memory: what was learned on earlier image sets, used to start a new one from the closest.

Each adapted image set adds one entry (results/flakes/knowledge.jsonl): its profile, the mode, the params the
search chose, and the scores (twin dev and holdout, real-image consistency). For a new set, `nearest` ranks the
entries by distance in profile space (log flake width, log boundary width, noise / contrast, minority fraction,
coherence; a different mode is far away), and `warm_start` turns the nearest entry's params into a starting point
for the new set, rescaling its pixel params to the new flake width.
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from src.methods.profile import SCALED

PIXEL_POWERS = {k: v[1] for k, v in SCALED.items()} | {"boundary_width_px": 1, "sigma": 1}


def _features(profile: dict, mode: str) -> np.ndarray:
    m = profile["by_mode"][mode]
    fw = m.get("flake_width_px") or 8.0
    bw = m.get("boundary_width_px") or fw
    return np.array([np.log(fw), 0.5 * np.log(bw), 10 * profile.get("noise_to_contrast", 0.05),
                     2 * profile.get("minority_fraction", 0.3), profile.get("coherence", 0.6)])


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def record(path: Path, entry: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": datetime.now().isoformat(timespec="seconds"), **entry}
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


SUCCESS_MAX_WORST_FAMILY = 1.5  # twin holdout: an entry worse than this is remembered, but never used to start from


def succeeded(entry: dict) -> bool:
    """The settings worked on a twin that could be trusted (it resembled the images): a good score on a twin that
    looks nothing like the images says nothing about them."""
    w = (entry.get("twin_holdout") or {}).get("worst_family_tolerance_ratio")
    trusted = entry.get("twin_trusted", entry.get("realism_ok", False))
    return bool(trusted) and w is not None and w <= SUCCESS_MAX_WORST_FAMILY


def nearest(entries: list[dict], profile: dict, mode: str, k: int = 3, only_succeeded: bool = True) -> list[tuple[float, dict]]:
    """Closest entries in profile space; by default only those whose settings worked (twin holdout <= 1.5)."""
    x = _features(profile, mode)
    scored = []
    for e in entries:
        if only_succeeded and not succeeded(e):
            continue
        d = float(np.linalg.norm(_features(e["profile"], e["mode"]) - x)) + (5.0 if e["mode"] != mode else 0.0)
        scored.append((d, e))
    return sorted(scored, key=lambda t: t[0])[:k]


def warm_start(entry: dict, profile: dict, mode: str) -> dict:
    """The entry's chosen params, pixel params rescaled from its flake width to this set's."""
    old = entry["profile"]["by_mode"][entry["mode"]].get("flake_width_px") or 8.0
    new = profile["by_mode"][mode].get("flake_width_px") or old
    ratio = new / old
    p = dict(entry["best_params"])
    for key, power in PIXEL_POWERS.items():
        if isinstance(p.get(key), (int, float)) and not isinstance(p.get(key), bool):
            v = p[key] * ratio**power
            p[key] = int(round(v)) if isinstance(p[key], int) else round(float(v), 3)
    p["mode"] = mode
    return p
