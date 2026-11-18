"""The adapt tool end to end on tiny images (no API: dry run), and its knowledge memory."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import knowledge
from src.adapt import adapt, bootstrap
from src.synth_lamellar import Spec, generate


def _images(tmp_path, n=2):
    paths = []
    for i in range(n):
        g = generate(Spec(f"r{i}", "t", seed=40 + i, kappa=4, shape=(256, 320), n_colonies=4, n_scratches=1))["image"]
        p = tmp_path / "real" / f"img{i}.png"
        p.parent.mkdir(exist_ok=True)
        Image.fromarray(g).save(p)
        paths.append(p)
    return paths


def test_adapt_runs_end_to_end_and_learns(tmp_path):
    images = _images(tmp_path)
    log = adapt(images, "tiny", budget=3, dry_run=True, workers=2, results_root=tmp_path / "results",
                twin_root=tmp_path / "twins", log=lambda *a: None)
    out = tmp_path / "results" / "adapt" / "tiny"
    for f in ("report.md", "report.html", "flakes.csv", "image_summaries.csv", "params.json", "adapt_log.json"):
        assert (out / f).exists(), f
    assert log["mode"] == "boundaries"
    assert {r["split"] for r in log["twin_scores"]} == {"dev", "holdout"}
    assert "Confidence" in (out / "report.md").read_text()
    entries = knowledge.load(tmp_path / "results" / "knowledge.jsonl")
    assert len(entries) == 1 and entries[0]["name"] == "tiny"
    # a second, similar set warm-starts from the first, when the first one succeeded (on 256 x 320 px images the
    # twin holdout of a 3-trial search is too noisy to be sure of that: mark it as a success for this test)
    entry = dict(entries[0], twin_trusted=True, twin_holdout={"worst_family_tolerance_ratio": 0.8})
    (tmp_path / "results" / "knowledge.jsonl").write_text(json.dumps(entry) + "\n")
    log2 = adapt(images[:1], "tiny2", budget=2, workers=2, results_root=tmp_path / "results",
                 twin_root=tmp_path / "twins", log=lambda *a: None)
    assert len(log2["starts"]) == 2


def test_warm_start_rescales_pixel_params():
    entry = {"mode": "boundaries", "profile": {"by_mode": {"boundaries": {"flake_width_px": 10.0}}},
             "best_params": {"min_area_px": 30, "h_px": 0.5, "boundary_sensitivity": 0.8}}
    prof = {"by_mode": {"boundaries": {"flake_width_px": 20.0}}}
    p = knowledge.warm_start(entry, prof, "boundaries")
    assert p["min_area_px"] == 120 and p["h_px"] == 1.0 and p["boundary_sensitivity"] == 0.8


def test_bootstrap_interval_contains_the_estimate():
    rng = np.random.default_rng(0)
    flakes = [{"orientation_deg": float(a), "area_px": 100, "aspect_ratio": 5.0, "used_for_orientation": True,
               "used_for_shape": True} for a in rng.normal(30, 10, 200) % 180]
    from src import axial

    b = bootstrap(flakes)
    lo, hi = b["order_parameter_ci95"]
    S = axial.resultant([f["orientation_deg"] for f in flakes])[1]  # about exp(-2 sigma^2) = 0.94 for sigma = 10 deg
    assert lo <= S <= hi and hi - lo < 0.05


def test_the_agent_can_switch_the_reading(tmp_path, monkeypatch):
    import src.adapt as A

    def plan_phase(claude, ctx, trials, reviews, best):
        return {"hypothesis": "the flakes are the dark phase", "rationale": "test", "reading": "phase",
                "flake_phase": "dark", "start_params_json": "{}", "search_space_json": "{}"}

    monkeypatch.setattr(A, "agent_plan", plan_phase)
    images = _images(tmp_path, n=1)
    log = A.adapt(images, "switch", budget=2, dry_run=True, workers=2, results_root=tmp_path / "results",
                  twin_root=tmp_path / "twins", record_knowledge=False, log=lambda *a: None)
    assert log["mode"] == "phase" and "switched by the agent" in log["mode_reason"]
    assert (tmp_path / "twins" / "switch__phase" / "twin_info.json").exists()
    assert log["chosen_params"]["flake_phase"] == "dark"


def test_failed_sets_are_remembered_but_not_used_as_a_start():
    prof = {"by_mode": {"boundaries": {"flake_width_px": 10.0, "boundary_width_px": 5.0}},
            "noise_to_contrast": 0.07, "minority_fraction": 0.3, "coherence": 0.7}
    good = {"name": "good", "mode": "boundaries", "profile": prof, "best_params": {}, "twin_trusted": True,
            "twin_holdout": {"worst_family_tolerance_ratio": 0.8}}
    bad = {"name": "bad", "mode": "boundaries", "profile": prof, "best_params": {}, "twin_trusted": True,
           "twin_holdout": {"worst_family_tolerance_ratio": 17.0}}
    # a good score on a twin that did not resemble the images is no success either
    untrusted = {"name": "untrusted", "mode": "boundaries", "profile": prof, "best_params": {}, "twin_trusted": False,
                 "twin_holdout": {"worst_family_tolerance_ratio": 0.5}}
    assert [e["name"] for _, e in knowledge.nearest([bad, good, untrusted], prof, "boundaries")] == ["good"]
    assert len(knowledge.nearest([bad, good, untrusted], prof, "boundaries", only_succeeded=False)) == 3


def test_a_second_coarse_scale_in_the_flake_phase_is_reported(tmp_path):
    from src import twin
    from src.adapt import two_scales

    g = generate(Spec("f", "t", seed=11, shape=(400, 520), lath_width_px=(5, 7), boundary_px=1.5, noise=0.02))["image"]
    fine = tmp_path / "fine.png"
    Image.fromarray(g).save(fine)
    prof = twin.set_profile([fine])
    assert two_scales([fine], prof, "boundaries") is None
    # wide bright plates (like Widmanstaetten plates next to fine colonies) over about half the area
    g2 = g.copy()
    rng = np.random.default_rng(0)
    for r in range(20, 360, 90):  # (with noise: a digitally flat rectangle would be taken for a label box)
        g2[r: r + 45, 20:500] = np.clip(215 + rng.normal(0, 6, (45, 480)), 0, 255).astype(np.uint8)
    coarse = tmp_path / "coarse.png"
    Image.fromarray(g2).save(coarse)
    prof2 = twin.set_profile([coarse])
    s = two_scales([coarse], prof2, "boundaries")
    assert s is not None and s["phase"] == "bright" and s["coarse_width_px"] >= 3 * prof2["by_mode"]["boundaries"]["flake_width_px"]


def test_the_real_image_guard_rejects_unstable_settings_but_not_fewer_fragments(tmp_path, monkeypatch):
    import src.adapt as A

    def fake(numbers):
        def measure_real(sample, params, out, workers=3):
            n = numbers[params["tag"]]["flakes"]
            return {"image_summaries": {"a": {"n_used_orientation": n}}}

        def check(method, sample, params, out, result, tol):
            return {"metrics": {"worst_consistency_ratio": numbers[params["tag"]]["rotation"]}}
        monkeypatch.setattr(A, "measure_real", measure_real)
        monkeypatch.setattr(A.consistency, "check", check)

    start, chosen = {"tag": "start"}, {"tag": "chosen"}
    # basketweave: the tuned settings were unstable under rotation -> the start settings stay
    fake({"start": {"flakes": 222, "rotation": 1.2}, "chosen": {"flakes": 61, "rotation": 3.9}})
    assert not A.real_image_guard([], start, chosen, tmp_path, {})["keep_chosen"]
    # Ti64: fewer flakes because laths are no longer cut, rotation still fine -> the tuned settings stay
    fake({"start": {"flakes": 1604, "rotation": 0.5}, "chosen": {"flakes": 654, "rotation": 0.72}})
    assert A.real_image_guard([], start, chosen, tmp_path, {})["keep_chosen"]
    # a collapse of the flake count is rejected even with a good rotation check
    fake({"start": {"flakes": 1000, "rotation": 0.5}, "chosen": {"flakes": 150, "rotation": 0.4}})
    assert not A.real_image_guard([], start, chosen, tmp_path, {})["keep_chosen"]


def test_the_agent_can_switch_to_the_regions_reading(tmp_path, monkeypatch):
    # found live on TiAl: the planner chose "regions", and a switch limited to boundaries / phase applied it as a
    # plain param change, without a regions twin and with the report naming the old reading
    import src.adapt as A

    def plan_regions(claude, ctx, trials, reviews, best):
        return {"hypothesis": "lamellae differ only by grey level", "rationale": "test", "reading": "regions",
                "flake_phase": "auto", "start_params_json": '{"mode": "regions", "h_px": 0.5}', "search_space_json": "{}"}

    monkeypatch.setattr(A, "agent_plan", plan_regions)
    images = _images(tmp_path, n=1)
    log = A.adapt(images, "toregions", budget=2, dry_run=True, workers=2, results_root=tmp_path / "results",
                  twin_root=tmp_path / "twins", record_knowledge=False, log=lambda *a: None)
    assert log["mode"] == "regions" and "switched by the agent" in log["mode_reason"]
    assert (tmp_path / "twins" / "toregions__regions" / "twin_info.json").exists()
    assert log["chosen_params"]["mode"] == "regions"


def test_adapt_writes_one_label_png_per_input_at_the_input_size(tmp_path):
    # the private test scores these with src.score_masks: 0 background, 1..N flakes, the input's size
    from src.adapt import adapt

    import shutil

    g = generate(Spec("x0", "t", seed=70, shape=(256, 320), lath_width_px=(8, 12), boundary_px=1.0))["image"]
    p0 = tmp_path / "in" / "im.0.png"  # (a dot in the name, as in the lamellar set)
    p0.parent.mkdir(exist_ok=True)
    Image.fromarray(g).save(p0)
    p1 = tmp_path / "in" / "sem.1.png"  # the scientist's SEM screenshot: flakes ~4 px, analysed enlarged
    shutil.copy(Path(__file__).resolve().parents[1] / "data" / "user" / "sem_bse.png", p1)
    paths = [p0, p1]
    for k, p in enumerate(paths):
        log = adapt([p], f"labels{k}", budget=1, workers=2, results_root=tmp_path / "results",
                    twin_root=tmp_path / "twins", record_knowledge=False, log=lambda *a: None)
        lab = tmp_path / "results" / "adapt" / f"labels{k}" / "labels" / f"{p.stem}.png"
        assert lab.exists(), lab
        arr = np.array(Image.open(lab))
        assert arr.shape == np.array(Image.open(p)).shape[:2]
        assert arr.max() >= 5 and arr.min() == 0
    assert log["upsample_factor"] > 1  # the second image was analysed enlarged, its labels are at its own size


# --- coarse cells (2026-11-18) --------------------------------------------------------------------------------------

def test_the_guard_refuses_merged_cells_but_not_fewer_laths(tmp_path, monkeypatch):
    """The rotation check passes a consistently merged segmentation: for cells, keeping half the flakes (and longer
    ones) is refused; for laths, fewer flakes are no fault by themselves (Ti64: laths kept whole)."""
    import src.adapt as A

    def fake_measure(sample, params, out_dir, workers=6):
        n, length = (100, 180.0) if params["tag"] == "start" else (50, 320.0)
        return {"image_summaries": {"a": {"n_used_orientation": n, "median_length_px": length}}}

    monkeypatch.setattr(A, "measure_real", fake_measure)
    monkeypatch.setattr(A.consistency, "check", lambda *a, **k: {"metrics": {"worst_consistency_ratio": 0.3}})
    start, chosen = {"tag": "start"}, {"tag": "chosen"}
    cells = A.real_image_guard([Path("a.png")], start, chosen, tmp_path, {}, cells=True, twin_length_px=190.0)
    laths = A.real_image_guard([Path("a.png")], start, chosen, tmp_path, {}, cells=False)
    assert not cells["keep_chosen"] and "merged" in cells["reason"]
    assert laths["keep_chosen"]


def test_adapt_takes_the_coarse_cell_twin_with_a_calibration_card(tmp_path):
    from src import calibration_card as cc
    from src.synth_cells import default_specs, generate as cells

    shape = (480, 600)
    paths = []
    for s in [x for x in default_specs(seed=5, per_family=1, holdout=0, shape=shape) if x.family != "soft"]:
        p = tmp_path / "real" / f"{s.name}.png"
        p.parent.mkdir(exist_ok=True)
        Image.fromarray(cells(s)["image"]).save(p)
        paths.append(p)
    # (the card v2 example: the generator's reference since round 3; a v1 card's contrast was the scientist's 3 x 3
    # measure read as the 5 x 5 one, and the twin fitted to it is rightly not trusted)
    card = cc.load_card(Path(__file__).resolve().parents[1] / "data" / "cards" / "coarse_cells_example_v2.json")
    card["image_shape"] = list(shape)
    (tmp_path / "card.json").write_text(json.dumps(card))
    lines = []
    log = adapt(paths, "cells", budget=2, workers=2, results_root=tmp_path / "results", twin_root=tmp_path / "twins",
                use_knowledge=False, record_knowledge=False, calibration=tmp_path / "card.json", log=lines.append)
    text = "\n".join(map(str, lines))
    assert "coarse cells: yes" in text and "fundamental spacing" in text and "min left" in text
    assert log["twin"]["family"] == "cells" and log["twin"]["card"]
    assert any(t["metrics"].get("segmentation_f1") is not None for t in log["trials"])
    out = tmp_path / "results" / "adapt" / "cells"
    assert (out / "report.md").exists() and len(list((out / "labels").glob("*.png"))) == len(paths)


def test_adapt_start_settings_carry_the_measured_width(tmp_path):
    """Round-2 private rerun: adapt passed every param explicitly but flake_width_px null, so the method lost the
    width: pores filled up to 26 px instead of ~120 (cells cut through them: ~20 splits per image), soft-region
    detection off, flake_width_px_used empty in image_summaries.csv."""
    from src import twin
    from src.adapt import resolved_start
    from src.methods import METHODS
    from src.methods.flakes import _pore_area
    from src.synth_cells import default_specs, generate as cells

    spec = next(s for s in default_specs(seed=9, per_family=1, holdout=0) if s.family == "soft")
    path = tmp_path / "s.png"
    Image.fromarray(cells(spec)["image"]).save(path)
    prof = twin.set_profile([path])
    start = resolved_start(prof, "boundaries")
    width = prof["by_mode"]["boundaries"]["flake_width_px"]
    assert start["flake_width_px"] == pytest.approx(width, rel=0.01)
    res = METHODS["flakes"]([path], start, tmp_path / "out")
    s = res["image_summaries"]["s"]
    assert s["flake_width_px_used"] == pytest.approx(width, rel=0.01)
    assert s["soft_fraction"] > 0.1                                   # soft detection runs on adapt's params
    assert _pore_area({"_width_used": s["flake_width_px_used"], "tile_px": start["tile_px"]}) > 60
    for k in ("h_px", "min_area_px", "tile_px", "boundary_width_px"):   # the explicit params are kept as given
        assert res["image_summaries"]["s"]["params_resolved"][k] == start[k]


def test_the_guard_refuses_settings_that_cut_cells(tmp_path, monkeypatch):
    """Round-2 private rerun: ~20 splits on every image passed the guard, which watched merges only. With the cells
    per readable area expected from the card (or the twin's truth), too many flakes per area is refused too."""
    import src.adapt as A

    img = tmp_path / "a.png"
    Image.fromarray(np.zeros((1000, 1000), np.uint8)).save(img)        # 1 megapixel

    def fake_measure(sample, params, out_dir, workers=6):
        n = {"start": 110, "chosen": 230}[params["tag"]]
        return {"image_summaries": {"a": {"n_used_orientation": n, "n_flakes": n, "n_pockets": 0, "soft_fraction": 0.0,
                                          "out_of_focus_fraction": 0.0, "median_length_px": 150.0}}}

    monkeypatch.setattr(A, "measure_real", fake_measure)
    monkeypatch.setattr(A.consistency, "check", lambda *a, **k: {"metrics": {"worst_consistency_ratio": 0.3}})
    g = A.real_image_guard([img], {"tag": "start"}, {"tag": "chosen"}, tmp_path, {}, cells=True,
                           twin_length_px=150.0, expected_density=100.0)
    assert not g["keep_chosen"] and "cut" in g["reason"]
    assert g["start"]["density"] == pytest.approx(110.0) and g["chosen"]["density"] == pytest.approx(230.0)
