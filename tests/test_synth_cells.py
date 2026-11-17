"""The calibrated coarse-cell twin: its images, measured by src.calibration_card with their own truth masks, must
fall inside the ranges of the private images' card (data/cards/coarse_cells_example.json)."""

from pathlib import Path

import json

import numpy as np
import pytest

from src import calibration_card as cc
from src.synth_cells import CellSpec, default_specs, generate, write_dataset

ROOT = Path(__file__).resolve().parents[1]
CARD = cc.load_card(ROOT / "data" / "cards" / "coarse_cells_example.json")


def inside(value, rng, slack=0.15):
    lo, hi = rng
    pad = slack * (hi - lo) + 1e-9
    return lo - pad <= value <= hi + pad


@pytest.fixture(scope="module")
def sharp():
    specs = [s for s in default_specs(seed=7, per_family=2, holdout=0) if s.family != "soft"][:3]
    gens = [generate(s) for s in specs]
    card = cc.make_card([(s.name, g["image"] / 255.0, g["mask"]) for s, g in zip(specs, gens)])
    return specs, gens, card


@pytest.fixture(scope="module")
def soft():
    spec = next(s for s in default_specs(seed=8, per_family=1, holdout=0) if s.family == "soft")
    return spec, generate(spec)


@pytest.mark.parametrize("key", ["interior_level", "cell_width_px", "cell_length_px", "cell_aspect_ratio",
                                 "order_parameter", "n_cells", "contrast_p25", "contrast_median", "contrast_p75",
                                 "invisible_fraction_along", "invisible_fraction_across", "noise",
                                 "dots_per_megapixel", "pocket_fraction", "illumination_range"])
def test_sharp_twin_images_match_the_card(sharp, key):
    """The median over the twin images lies in the card's range; every image lies within 60 % of the range's width
    of it (12 twin images spread more than the card's 8 on the cell-population medians: AR, counts)."""
    _, _, card = sharp
    vals = [cc._flat(row)[key] for row in card["per_image"]]
    assert inside(float(np.median(vals)), CARD["summary"][key], slack=0.1), f"{key}: median {np.median(vals):.4g} outside {CARD['summary'][key]}"
    for row, v in zip(card["per_image"], vals):
        assert inside(v, CARD["summary"][key], slack=0.6), f"{row['image']}: {key} {v:.4g} outside {CARD['summary'][key]}"


def test_the_field_is_filled_with_cells_of_varied_width(sharp):
    _, gens, _ = sharp
    for g in gens:
        m = g["mask"]
        assert np.isin(m, [0, 1, 2, 3]).all()
        assert (m == 1).mean() > 0.8                     # no matrix: cells, thin boundaries, small pockets
        fl = [f for f in g["flakes"] if not f["touches_edge"]]
        minor = np.array([f["minor_px"] for f in fl])
        assert np.percentile(minor, 90) / np.percentile(minor, 10) >= 1.5   # thin slivers to wide lenses


def test_soft_images_have_an_unreadable_region_and_fainter_boundaries(sharp, soft):
    _, _, card = sharp
    spec, g = soft
    s = cc.image_stats(g["image"] / 255.0, g["mask"])
    assert inside(s["ignore_fraction"], CARD["soft"]["ignore_fraction"], slack=0.1)
    assert s["boundary_contrast"]["median"] < min(r["boundary_contrast"]["median"] for r in card["per_image"])
    assert inside(s["boundary_contrast"]["median"], CARD["soft"]["contrast_median"], slack=0.5)
    assert not (g["flake_ids"][g["ignore"]] > 0).any()


def test_scratches_hairline_at_45_and_a_thick_one_at_20_deg():
    g = generate(CellSpec("s", "scratched", seed=4, n_hairline=3, thick_probability=1.0, thick_angle_deg=20.0))
    lines = g["scratch_lines"]
    thick = [s for s in lines if s["thick"]]
    assert thick and all(5 <= s["width_px"] <= 10 and 100 <= s["length_px"] <= 420 for s in thick)
    assert all(abs(s["angle_to_cells_deg"] - 20) < 6 for s in thick)
    hair = [s for s in lines if not s["thick"] and not s.get("debris")]
    assert len(hair) == 3 and all(s["length_px"] >= 250 for s in hair)
    assert g["scratch"].any()


def test_dataset_has_three_families_dev_holdout_and_consistent_truth(tmp_path):
    specs = default_specs(seed=1, per_family=2, holdout=1, shape=(400, 520))
    write_dataset(tmp_path, specs, holdout=1, per_family=2)
    split = json.loads((tmp_path / "split.json").read_text())
    assert {s.family for s in specs} == {"clean", "scratched", "soft"}
    assert len(split["dev"]) == 3 and len(split["holdout"]) == 3
    assert (tmp_path / "truth" / "masks").exists() and (tmp_path / "truth" / "flakes.csv").exists()
