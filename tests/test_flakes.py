"""The flake method against exact answers: analytic shapes, and code-generated lamellar images with known flakes."""

from pathlib import Path

import numpy as np
from scipy import ndimage as ndi
import pytest
from PIL import Image

from src import axial
from src.methods import METHODS
from src.synth_lamellar import Spec, generate

ROOT = Path(__file__).resolve().parents[1]


# --- axial statistics -------------------------------------------------------------------------------------

def test_parallel_flakes_have_order_one():
    mean, S = axial.resultant([30, 30, 30])
    assert mean == pytest.approx(30) and S == pytest.approx(1)


def test_perpendicular_pairs_have_order_zero():
    assert axial.resultant([0, 90])[1] == pytest.approx(0, abs=1e-9)
    assert axial.resultant(np.arange(0, 180, 1.0))[1] == pytest.approx(0, abs=1e-9)


def test_mean_direction_wraps_at_180():
    mean, S = axial.resultant([170, 10])
    assert axial.axial_diff(mean, 0) == pytest.approx(0, abs=1e-9)
    assert S == pytest.approx(np.cos(np.deg2rad(20)))


def test_misalignment_is_the_smallest_angle():
    assert axial.axial_diff(175, 5) == pytest.approx(10)
    assert axial.axial_diff(0, 90) == pytest.approx(90)
    s = axial.summary([20, 40], weights=[1, 1])
    assert s["mean_direction_deg"] == pytest.approx(30) and s["mean_misalignment_deg"] == pytest.approx(10)


@pytest.mark.parametrize("angle", [0, 30, 75, 120, 160])
def test_moments_orientation_of_a_drawn_bar(angle):
    rows, cols = np.mgrid[0:200, 0:200]
    t = np.deg2rad(angle)
    u = (cols - 100) * np.cos(t) - (rows - 100) * np.sin(t)   # y up: counterclockwise as displayed
    v = (cols - 100) * np.sin(t) + (rows - 100) * np.cos(t)
    bar = (np.abs(u) < 50) & (np.abs(v) < 8)
    theta, major, minor = axial.moments_orientation(*np.nonzero(bar))
    assert axial.axial_diff(theta, angle) < 1
    assert major / minor == pytest.approx(100 / 16, rel=0.08)


# --- the method on generated lamellar images ----------------------------------------------------------------

def _truth_stats(g):
    flakes = [f for f in g["flakes"] if f["area_px"] >= 30 and f["aspect_ratio"] >= 1.5]
    mean, S = axial.resultant([f["orientation_deg"] for f in flakes], [f["area_px"] for f in flakes])
    return mean, S


def _run(tmp_path, image, params=None, name="img"):
    path = tmp_path / f"{name}.png"
    Image.fromarray(image).save(path)
    out = METHODS["flakes"]([path], params or {}, tmp_path / f"out_{name}", overlay=[name])
    return out, out["image_summaries"][name]


SMALL = dict(shape=(384, 512), n_colonies=5)


def test_aligned_image_mean_direction_and_order(tmp_path):
    g = generate(Spec("a", "t", seed=1, kappa=25, mean_direction_deg=35, **SMALL))
    mean_true, S_true = _truth_stats(g)
    _, s = _run(tmp_path, g["image"])
    assert axial.axial_diff(s["mean_direction_deg"], mean_true) < 5
    assert abs(s["order_parameter"] - S_true) < 0.1, (s["order_parameter"], S_true)


def test_random_colonies_give_low_order(tmp_path):
    g = generate(Spec("r", "t", seed=4, kappa=0, n_colonies=12, shape=(384, 512)))
    _, S_true = _truth_stats(g)
    _, s = _run(tmp_path, g["image"])
    assert abs(s["order_parameter"] - S_true) < 0.15, (s["order_parameter"], S_true)


def test_aspect_ratio_is_close_to_the_truth(tmp_path):
    g = generate(Spec("a", "t", seed=2, kappa=25, mean_direction_deg=100, **SMALL))
    true_ar = np.median([f["aspect_ratio"] for f in g["flakes"] if f["area_px"] >= 30 and not f["touches_edge"]])
    _, s = _run(tmp_path, g["image"])
    assert s["median_aspect_ratio"] == pytest.approx(true_ar, rel=0.3)


def test_inverted_contrast_gives_the_same_direction(tmp_path):
    g = generate(Spec("a", "t", seed=3, kappa=25, mean_direction_deg=140, **SMALL))
    _, s_dark = _run(tmp_path, g["image"], name="dark")
    _, s_bright = _run(tmp_path, 255 - g["image"], name="bright")
    assert s_bright["polarity"] != s_dark["polarity"]
    assert axial.axial_diff(s_dark["mean_direction_deg"], s_bright["mean_direction_deg"]) < 3


def test_rotating_the_image_rotates_the_direction(tmp_path):
    g = generate(Spec("a", "t", seed=5, kappa=25, mean_direction_deg=20, **SMALL))
    _, s0 = _run(tmp_path, g["image"], name="r0")
    _, s90 = _run(tmp_path, np.ascontiguousarray(np.rot90(g["image"])), name="r90")
    assert axial.axial_diff(s90["mean_direction_deg"], s0["mean_direction_deg"] + 90) < 3


def test_scratches_are_found_and_do_not_cut_flakes(tmp_path):
    spec = dict(seed=7, kappa=25, mean_direction_deg=10, shape=(512, 640), n_colonies=4, gb_alpha_fraction=0)
    scratched = generate(Spec("s", "t", n_scratches=3, **spec))
    out, s = _run(tmp_path, scratched["image"], name="s")
    found = out["defect_maps"]["s"] == 1
    truth, crossing = scratched["scratch"], scratched["scratch_crossing"]
    grown = lambda m: __import__("scipy").ndimage.binary_dilation(m, iterations=2)
    precision = (found & grown(truth)).sum() / max(found.sum(), 1)
    recall = (crossing & grown(found)).sum() / crossing.sum()  # scratches along the laths are not expected
    assert precision > 0.8 and recall > 0.7, (precision, recall)


def test_no_scratches_on_a_clean_image(tmp_path):
    clean = generate(Spec("c", "t", seed=8, kappa=4, n_scratches=0, **SMALL))
    out, s = _run(tmp_path, clean["image"], name="c")
    assert s["scratch_pixel_fraction"] < 0.002


def test_unknown_params_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="sigmaa"):
        METHODS["flakes"]([], {"sigmaa": 1}, tmp_path)


def test_runs_on_a_real_ti64_lamellar_image(tmp_path):
    image = ROOT / "data" / "sample" / "Ti64" / "Ti64_21.jpg"
    out = METHODS["flakes"]([image], {}, tmp_path, overlay=["Ti64_21"])
    s = out["image_summaries"]["Ti64_21"]
    assert s["n_flakes"] > 200
    assert 0 <= s["order_parameter"] <= 1
    assert s["polarity"] == "dark_boundaries"
    assert len(out["overlays"]) == 1


def test_side_by_side_pieces_are_not_merged_but_end_to_end_pieces_are():
    from src.methods.flakes import merge_by_saddle

    # two parallel bars (laths) at 0 deg touching through a wide gap in the boundary between them,
    # and a third bar cut across its width into two end-to-end pieces
    labels = np.zeros((60, 120), np.int32)
    labels[10:20, 10:110] = 1
    labels[20:30, 10:110] = 2          # side by side with 1
    labels[40:50, 10:60] = 3
    labels[40:50, 60:110] = 4          # end to end with 3
    distance = __import__("scipy").ndimage.distance_transform_edt(labels > 0)
    merged = merge_by_saddle(labels, distance, ratio=0.7, max_offaxis_deg=30)
    assert merged[15, 50] != merged[25, 50]   # neighbouring laths stay apart
    assert merged[45, 30] == merged[45, 90]   # pieces of one lath are joined


def test_a_merge_that_would_make_a_branched_region_is_refused():
    from src.methods.flakes import merge_by_saddle

    labels = np.zeros((80, 80), np.int32)
    labels[10:20, 10:70] = 1           # a horizontal lath
    labels[20:70, 10:20] = 2           # a vertical one, touching at the corner: together an L
    distance = __import__("scipy").ndimage.distance_transform_edt(labels > 0)
    assert len(np.unique(merge_by_saddle(labels, distance, ratio=0.1, min_fill=0.0))) == 2   # merged
    assert len(np.unique(merge_by_saddle(labels, distance, ratio=0.1, min_fill=0.85))) == 3  # kept apart


def _arc(shape=(200, 260), radius=300, width=10, span_deg=40, centre=(330, 130)):
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    r = np.hypot(rows - centre[0], cols - centre[1])
    ang = np.degrees(np.arctan2(cols - centre[1], centre[0] - rows))
    return (np.abs(r - radius) < width / 2) & (np.abs(ang) < span_deg / 2)


def test_a_bent_lath_is_one_flake_but_a_branched_region_is_not():
    from src.methods.flakes import is_branched

    bent = _arc()                                   # a long lath bent along an arc: low solidity, no branches
    from skimage.measure import regionprops, label
    assert regionprops(label(bent))[0].solidity < 0.75
    assert not is_branched(bent)
    y = np.zeros((200, 200), bool)                  # a Y: three arms of lath width
    y[100:190, 95:105] = True
    for k in range(80):
        y[100 - k, 100 - k:110 - k] = True
        y[100 - k, 90 + k:100 + k] = True
    assert is_branched(y)
    straight = np.zeros((60, 200), bool)
    straight[25:35, 10:190] = True
    assert not is_branched(straight)


def test_a_long_band_across_its_neighbours_is_taken_for_grain_boundary_alpha():
    from src.methods.flakes import DEFAULTS, measure_flakes

    labels = np.zeros((300, 400), np.int32)
    k = 1
    for r0 in range(5, 295, 12):                 # short horizontal laths, 10 px wide, 40 px long
        for c0 in range(5 + (r0 % 24), 395 - 40, 46):
            labels[r0:r0 + 10, c0:c0 + 40] = k
            k += 1
    rows, cols = np.mgrid[0:300, 0:400]
    band = np.abs((rows - 150) - 0.6 * (cols - 200)) < 6   # a long band at about 31 deg, across the laths
    labels[band] = k
    long_lath = (rows >= 278) & (rows < 288) & (cols >= 10) & (cols < 390) & ~band  # a long lath along them
    labels[long_lath] = k + 1
    flakes = measure_flakes(labels, np.ones(labels.shape, bool), np.zeros(labels.shape, bool), DEFAULTS)
    assert flakes[k - 1]["gb_alpha_like"] and not flakes[k - 1]["used_for_orientation"]
    assert not flakes[k]["gb_alpha_like"] and flakes[k]["used_for_orientation"]


def test_pasted_strong_scratches_are_found_along_their_length(tmp_path):
    """Strong polishing scratches from the paster (the real-texture test's model) on a lamellar image."""
    from scipy import ndimage as ndi
    from src.scratch_paste import paste

    g = generate(Spec("x", "t", seed=21, kappa=4, shape=(512, 640), n_colonies=5, n_scratches=0))["image"] / 255.0
    img, code, _ = paste(g, np.random.default_rng(7), "strong")
    path = tmp_path / "s.png"
    Image.fromarray((img * 255).round().astype(np.uint8)).save(path)
    found = METHODS["flakes"]([path], {}, tmp_path / "out")["defect_maps"]["s"] == 1
    crossing = code == 255
    recall = (crossing & ndi.binary_dilation(found, iterations=2)).sum() / crossing.sum()
    precision = (found & ndi.binary_dilation(code > 0, iterations=2)).sum() / max(found.sum(), 1)
    assert recall > 0.3 and precision > 0.9, (recall, precision)  # old settings (gap 6, fraction 0.5): 0.19, 1.00



def test_the_regions_reading_separates_laths_told_apart_only_by_grey_level(tmp_path):
    # BSE-like lamellae: touching laths at three grey levels, no boundary lines between them
    from src import evaluate
    from src.synth_lamellar import write_dataset

    root = tmp_path / "ds"
    specs = [Spec(f"strong_clean_{k}", "strong_clean", seed=60 + k, shape=(400, 560), kappa=25, lath_width_px=(8, 14),
                  boundary_px=1.0, style="regions", n_levels=4, noise=0.02, n_pits=5) for k in range(2)]
    write_dataset(root, specs)
    names = [s.name for s in specs]
    cfg = {"flakes": {"tolerances": {"mean_direction_deg": 5.0, "order_parameter": 0.05, "misalignment_deg": 5.0,
                                     "aspect_ratio_rel": 0.15, "flake_orientation_deg": 5.0}}}
    recall = {}
    for mode in ("regions", "boundaries"):
        res = METHODS["flakes"]([root / "images" / f"{n}.png" for n in names], {"mode": mode}, tmp_path / mode)
        m = evaluate.score_flakes(cfg, names, res, tmp_path / mode / "labels", truth_root=root)["metrics"]
        recall[mode] = m["flake_recall"]
        if mode == "regions":
            assert m["median_direction_error_deg"] < 5 and m["median_order_abs_error"] < 0.1
    assert recall["regions"] >= 0.5 and recall["regions"] > recall["boundaries"] + 0.2, recall


def test_a_drawing_keeps_the_full_image_name(tmp_path):
    # "L08__10.3390_x.png" used to be drawn as "L08__10.jpg": names with dots collided and lost their pairing
    g = generate(Spec("x", "t", seed=3, shape=(200, 260)))["image"]
    a, b = tmp_path / "L08__10.3390_a.png", tmp_path / "L08__10.3390_b.png"
    Image.fromarray(g).save(a)
    Image.fromarray(g).save(b)
    res = METHODS["flakes"]([a, b], {}, tmp_path / "out", overlay=[a.stem, b.stem])
    assert sorted(Path(o).name for o in res["overlays"]) == ["L08__10.3390_a.jpg", "L08__10.3390_b.jpg"]


def test_junction_pockets_are_not_counted_as_flakes(tmp_path):
    # failure 4 of the private coarse-cell test: textured pockets at triple junctions were measured as flakes
    from src import score_masks
    from src.synth_cells_v1 import CellSpec, generate as cells

    g = cells(CellSpec("p", "clean", seed=31, pocket_fraction=0.3))
    path = tmp_path / "p.png"
    Image.fromarray(g["image"]).save(path)
    score = {}
    for flag in (False, True):
        res = METHODS["flakes"]([path], {"detect_pockets": flag}, tmp_path / str(flag))
        lab = np.array(Image.open(tmp_path / str(flag) / "labels" / "p.png"))
        score[flag] = score_masks.score_image(lab, g["mask"])
    assert score[False]["pocket_fp"] >= 5
    assert score[True]["pocket_fp"] <= 0.3 * score[False]["pocket_fp"]
    assert score[True]["tp"] >= 0.97 * score[False]["tp"]  # real cells are not taken for pockets
    assert res["image_summaries"]["p"]["n_pockets"] >= 0.5 * score[False]["pocket_fp"]


def test_fine_lamellae_are_never_taken_for_pockets(tmp_path):
    g = generate(Spec("f", "t", seed=5, shape=(400, 520), lath_width_px=(5, 9), boundary_px=1.5, noise=0.04))["image"]
    path = tmp_path / "f.png"
    Image.fromarray(g).save(path)
    res = METHODS["flakes"]([path], {}, tmp_path / "out")
    assert res["image_summaries"]["f"]["n_pockets"] == 0


def test_pores_inside_coarse_cells_do_not_cut_them(tmp_path):
    # failure 3 of the private coarse-cell test: over-splitting; on the twin most splits ran from a pore to the
    # boundary (the pore was a hole in the interior, and the watershed cut through it)
    from src import score_masks
    from src.synth_cells_v1 import CellSpec, generate as cells

    g = cells(CellSpec("d", "clean", seed=41, dots_per_cell=4, pocket_fraction=0.05))
    path = tmp_path / "d.png"
    Image.fromarray(g["image"]).save(path)
    METHODS["flakes"]([path], {}, tmp_path / "out")
    s = score_masks.score_image(np.array(Image.open(tmp_path / "out" / "labels" / "d.png")), g["mask"])
    assert s["splits"] <= 0.07 * s["n_true"]  # (before the pore filling: 0.15)
    assert s["aspect_ratio_rel_error"] > -0.08


def test_pieces_with_a_darker_shared_border_are_not_merged():
    # failure 3: coarse cells stacked end to end have faint, broken boundaries; the watershed cuts the leaked region
    # at the neck, and the pieces must stay apart when their shared border is darker than both (a boundary is
    # there), but merge back when it is not (a cut inside one flake)
    from src.methods.flakes import merge_by_saddle

    labels = np.zeros((60, 200), int)
    labels[10:50, 10:100] = 1
    labels[10:50, 100:190] = 2           # two pieces end to end, touching at column 100
    distance = ndi.distance_transform_edt(labels > 0)
    img = np.full(labels.shape, 0.8)
    plain = merge_by_saddle(labels, distance, 0.5, 30.0, min_fill=0.9, evidence=(img, 0.05))
    assert len(np.unique(plain[plain > 0])) == 1   # no darker border: one flake
    img[10:50, 98:102] = 0.65                       # a faint line along the shared border
    kept = merge_by_saddle(labels, distance, 0.5, 30.0, min_fill=0.9, evidence=(img, 0.05))
    assert len(np.unique(kept[kept > 0])) == 2
    assert len(np.unique(merge_by_saddle(labels, distance, 0.5, 30.0, min_fill=0.9)[labels > 0])) == 1  # (no evidence: merged)


def test_hairline_and_thick_scratches_across_coarse_cells_are_found(tmp_path):
    # failure 5 of the private coarse-cell test: 2 scratches found in 8 images, not the thick one
    from src.synth_cells_v1 import CellSpec, generate as cells

    hits, thick_hit = [], False
    for seed in (51, 52):
        g = cells(CellSpec("s", "scratched", seed=seed, n_hairline=3, thick_probability=1.0, pocket_fraction=0.1))
        path = tmp_path / f"s{seed}.png"
        Image.fromarray(g["image"]).save(path)
        res = METHODS["flakes"]([path], {}, tmp_path / str(seed))
        found = res["defect_maps"][f"s{seed}"] == 1
        crossing = g["scratch_crossing"]
        near = ndi.binary_dilation(found, iterations=2)
        hits.append(float(near[crossing].mean()))
        # the thick one: its pixels are the darkest scratch pixels of the truth
        lines = res["defects"][f"s{seed}"]
        thick_hit |= any(it.get("kind") == "dark" and it["length_px"] > 200 for it in lines)
        # no false scratch along the cell boundaries
        false = found & ~ndi.binary_dilation(g["scratch"], iterations=3)
        assert false.sum() <= 0.2 * max(found.sum(), 1)
    assert np.mean(hits) >= 0.4
    assert thick_hit


def test_no_scratch_is_invented_on_clean_coarse_cells(tmp_path):
    from src.synth_cells_v1 import CellSpec, generate as cells

    for seed in (53, 54):
        g = cells(CellSpec("c", "clean", seed=seed))
        path = tmp_path / f"c{seed}.png"
        Image.fromarray(g["image"]).save(path)
        res = METHODS["flakes"]([path], {}, tmp_path / str(seed))
        assert (res["defect_maps"][f"c{seed}"] == 1).sum() == 0


# --- calibrated coarse-cell twin (2026-11-18) -----------------------------------------------------------------------

def _analyse_cells(img):
    from src.methods.flakes import DEFAULTS, analyse

    return analyse(img, dict(DEFAULTS, _given=[]))


@pytest.mark.xfail(reason="fix 7 (thick scratches at any angle >= 12 deg) not implemented yet: frozen 2026-11-17 17:30", strict=False)
def test_a_thick_dark_scratch_at_20_deg_to_the_cells_is_found():
    """The private images have a thick dark scratch ~20 deg off the cells, inside the 25 deg exclusion of the coarse
    rules: thick scratches are told from boundaries by their width, darkness and length instead (crossing >= 12 deg)."""
    from src.synth_cells import CellSpec, generate as cells

    found = 0
    for seed in (51, 52, 53):
        g = cells(CellSpec("t", "scratched", seed=seed, thick_probability=1.0, thick_angle_deg=20.0, n_hairline=0))
        r = _analyse_cells(g["image"] / 255.0)
        thick = g["scratch"]
        if (ndi.binary_dilation(r["scratch"], iterations=2) & thick).sum() >= 0.4 * thick.sum():
            found += 1
    assert found >= 2


def test_no_scratch_is_invented_on_clean_calibrated_cells():
    from src.synth_cells import CellSpec, generate as cells

    for seed in (61, 62):
        g = cells(CellSpec("c", "clean", seed=seed))
        assert _analyse_cells(g["image"] / 255.0)["scratch"].mean() < 0.002
