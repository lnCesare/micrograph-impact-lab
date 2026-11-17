"""One HTML page over a stress test: per image set, the reading, the twin checks, the rotation check, the
measurements, a verdict by eye, and the first original-next-to-drawing image, linking each set's full report.

    .venv/bin/python scripts/stress_index.py [--verdicts results/flakes/stress_test_verdicts.json]
Reads results/flakes/stress_test_summary.json and results/flakes/adapt/<set>/; writes
results/flakes/stress_test_index.html (links are relative, so the page works from the folder).
"""

import argparse
import html
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FLAKES = ROOT / "results" / "flakes"


def fmt(v, nd=2):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return html.escape(str(v))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdicts", type=Path, default=FLAKES / "stress_test_verdicts.json",
                    help="JSON {set: verdict by eye}, written after looking at the drawings")
    a = ap.parse_args()
    summary = json.loads((FLAKES / "stress_test_summary.json").read_text())
    verdicts = json.loads(a.verdicts.read_text()) if a.verdicts.exists() else {}
    rows = []
    for name, s in summary.items():
        d = FLAKES / "adapt" / name
        review = sorted((d / "review").glob("*.jpg")) if (d / "review").exists() else []
        thumb = f'<a href="adapt/{name}/review/{review[0].name}"><img src="adapt/{name}/review/{review[0].name}"></a>' if review else ""
        meas = ""
        if (d / "image_summaries.csv").exists():
            t = pd.read_csv(d / "image_summaries.csv")
            med = lambda c: t[c].median() if c in t and t[c].notna().any() else None
            meas = (f"direction {fmt(med('mean_direction_deg'), 0)}°, S {fmt(med('order_parameter'))}, "
                    f"AR {fmt(med('median_aspect_ratio'), 1)}, flakes/image {fmt(med('n_used_orientation'), 0)}")
        if "error" in s:
            checks = f"<b>error</b>: {html.escape(s['error'])}"
        else:
            trusted = s.get("twin_trusted")
            checks = (f"twin {'trusted' if trusted else '<b>not trusted</b>'} "
                      f"(profile x{2.718281828 ** (s.get('twin_profile_mismatch') or 0):.2f}, "
                      f"shape x{2.718281828 ** (s.get('twin_structure_mismatch') or 0):.2f}); "
                      f"twin holdout {fmt(s.get('twin_holdout_worst_family'))}; "
                      f"rotation {fmt(s.get('consistency'))}"
                      + (f"; upsampled {s['upsample_factor']}x" if (s.get('upsample_factor') or 1) > 1 else ""))
        rows.append(f"""<tr><td><a href="adapt/{name}/report.html"><b>{html.escape(name)}</b></a><br>
<span class="m">{html.escape(s.get('mode', ''))}: {html.escape(s.get('mode_reason', ''))}</span></td>
<td>{checks}</td><td>{meas}</td><td>{html.escape(verdicts.get(name, ''))}</td><td class="t">{thumb}</td></tr>""")
    live_rows = []
    live_file = FLAKES / "live_test_summary.json"
    live = json.loads(live_file.read_text()) if live_file.exists() else {}
    for name, s in live.items():
        if "error" in s:
            continue
        d = FLAKES / "adapt" / name
        review = sorted((d / "review").glob("*.jpg")) if (d / "review").exists() else []
        thumb = f'<a href="adapt/{name}/review/{review[0].name}"><img src="adapt/{name}/review/{review[0].name}"></a>' if review else ""
        rounds = "<br>".join(
            f"round {i}: review <b>{html.escape(r['verdict'])}</b>, reading right: {html.escape(r['reading_correct'])}; "
            f"plan: reading {html.escape(p['reading'])}" + (f", phase {html.escape(p['flake_phase'])}" if p.get("flake_phase") not in (None, "auto") else "")
            for i, (r, p) in enumerate(zip(s.get("reviews", []), s.get("plans", [])), 1))
        first = (s.get("reviews") or [{}])[0].get("reason", "")
        live_rows.append(f"""<tr><td><a href="adapt/{name}/report.html"><b>{html.escape(name)}</b></a><br>
<span class="m">final reading: {html.escape(s.get('mode', ''))}; {html.escape(s.get('mode_reason', '')[:160])}</span></td>
<td>{rounds}<br><span class="m">first review: {html.escape(first[:420])}...</span></td>
<td>rotation {fmt(s.get('consistency'))}; twin {'trusted' if s.get('twin_trusted') else 'not trusted'}{'; settings set from the drawings' if s.get('set_by_eye') else ''}; ${s.get('usd_list_price')}</td>
<td class="t">{thumb}</td></tr>""")
    live_html = ("" if not live_rows else f"""<h2>Live agent runs (Claude reviews each original next to its drawing, then plans)</h2>
<p>Same image sets, with <code>--live 2</code>: the reviewer judges whether the outlined regions are the flakes a metallographer
would measure, and the planner switches the reading or changes settings. Compare each drawing with the one in the table above.</p>
<table><tr><th>set</th><th>what the agent did</th><th>checks, cost</th><th>original | drawing after the agent</th></tr>
{''.join(live_rows)}</table>""")
    page = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flakes stress test</title><style>
:root {{ --bg:#fff; --fg:#1d1d1f; --mut:#666; --line:#ddd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161616; --fg:#eee; --mut:#aaa; --line:#333; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:16px; }}
table {{ border-collapse:collapse; width:100%; }} td, th {{ border-bottom:1px solid var(--line); padding:8px; vertical-align:top; text-align:left; }}
.m {{ color:var(--mut); font-size:12px; }} .t img {{ width:420px; max-width:40vw; }} a {{ color:inherit; }}
</style></head><body>
<h1>Flakes stress test</h1>
<p>Each row is one image set run through <code>src/adapt.py</code> without the API.
<i>Twin trusted</i>: the code-generated twin resembles the images (profile within x1.35, flake shape as measured by the method within x1.5), so its accuracy numbers are evidence about them.
<i>Twin holdout</i>: worst family tolerance ratio on held-out twin images (≤ 1 is within the tolerances).
<i>Rotation</i>: the central disk turned by 30° and measured again (≤ 1 passes).
The verdict column is a judgement by eye of the drawings, not a measurement. Click a set for its full report, the thumbnail for the full image.</p>
<table><tr><th>set</th><th>checks</th><th>median measurements</th><th>by eye</th><th>original | drawing</th></tr>
{''.join(rows)}</table>{live_html}</body></html>"""
    (FLAKES / "stress_test_index.html").write_text(page)
    print(FLAKES / "stress_test_index.html")


if __name__ == "__main__":
    main()
