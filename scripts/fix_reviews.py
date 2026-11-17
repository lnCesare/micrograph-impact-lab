"""Rebuild review/ (original next to drawing) for adapt results whose review images were not written, and point their
reports at them. Needed once for results made before 2026-11-17 05:10: image names with a dot ("L08__10.3390_x.png")
had their drawing saved as "L08__10.jpg", and the pairing missed it, so the reports showed the drawings alone.

    .venv/bin/python scripts/fix_reviews.py results/flakes/adapt/*
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.adapt import side_by_side  # noqa: E402

if __name__ == "__main__":
    for d in map(Path, sys.argv[1:]):
        log_file = d / "adapt_log.json"
        if not log_file.exists():
            continue
        images = [Path(p) for p in json.loads(log_file.read_text())["images"]]
        overlays = sorted(str(p) for p in (d / "overlays").glob("*.jpg"))
        have = {p.stem for p in (d / "review").glob("*.jpg")} if (d / "review").exists() else set()
        if len(have) >= len(images):
            continue
        written = side_by_side(images, overlays, d / "review")
        for report in (d / "report.md", d / "report.html"):
            if not report.exists():
                continue
            text = report.read_text()
            for w in map(Path, written):
                short = Path(w.stem).stem  # the drawing's (truncated) name
                text = text.replace(f"overlays/{short}.jpg", f"review/{w.name}")
            report.write_text(text)
        print(d.name, f"{len(written)} review images")
