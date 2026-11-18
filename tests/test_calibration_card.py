"""The calibration card: image statistics from images + masks, so a twin can be fitted to private images without
the images leaving their owner (only these numbers cross over)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage as ndi

from src import calibration_card as cc

ROOT = Path(__file__).resolve().parents[1]
W, L, B = 60, 180, 3          # cell width, length, boundary width (px)
LEVEL = 0.97                  # cell interior


def bricks(noise=0.005, seed=0, long_contrast=0.15, trans_contrast=0.10, gap=True):
    """Vertical bricks 60 x 180 px; boundaries along the cells at 15 % contrast with 20 % gaps, across at 10 %;
    10 dark dots, one 40 x 40 pocket, the right 100 px "ignore"."""
    rng = np.random.default_rng(seed)
    h, w = 600, 800
    img = np.full((h, w), LEVEL)
    mask = np.ones((h, w), np.uint8)
    cols = np.arange(w)
    vline = (cols % (W + B)) >= W                       # boundaries along the cells (vertical)
    img[:, vline] = LEVEL * (1 - long_contrast)
    mask[:, vline] = 0
    gaps = np.zeros((h, w), bool)
    if gap:
        for r0 in range(0, h, 150):                      # 30 of every 150 rows: a gap (20 %)
            gaps[r0:r0 + 30, :] = True
        img[gaps & vline[None, :]] = LEVEL
    for c0 in range(0, w, W + B):                        # boundaries across (staggered per column)
        off = (c0 // (W + B)) % 2 * (L // 2)
        for r in range(off, h, L + B):
            img[r:r + B, c0:c0 + W] = LEVEL * (1 - trans_contrast)
            mask[r:r + B, c0:c0 + W] = 0
    dots = [(45, 30 + k * (W + B)) for k in range(10)]   # one dot inside each of 10 cells, off the boundaries
    yy, xx = np.mgrid[0:h, 0:w]
    for (y, x) in dots:
        d = (yy - y) ** 2 + (xx - x) ** 2 <= 9
        img[d] = 0.5
    mask[300:340, 200:240] = 2
    img[300:340, 200:240] = 0.6
    mask[:, 700:] = 3
    img = img + rng.normal(0, noise, img.shape)
    return np.clip(img, 0, 1), mask


@pytest.fixture(scope="module")
def card_of_bricks():
    img, mask = bricks()
    return cc.image_stats(img, mask)


def test_cells_are_measured_by_their_second_moments(card_of_bricks):
    s = card_of_bricks
    assert s["interior_level"] == pytest.approx(LEVEL, abs=0.005)
    assert s["cell_width_px"] == pytest.approx(W * 4 / np.sqrt(12), rel=0.05)   # minor axis of a 60 px wide brick
    assert s["cell_aspect_ratio"] == pytest.approx(L / W, rel=0.08)
    assert s["order_parameter"] > 0.98 and abs(s["mean_direction_deg"] - 90) < 1


def test_boundary_contrast_and_invisible_fractions(card_of_bricks):
    s = card_of_bricks
    assert 0.12 <= s["boundary_contrast"]["median"] <= 0.19
    # along the cells: 20 % of the rows are gaps; across: none
    assert 0.12 <= s["invisible_fraction_along"] <= 0.26
    assert s["invisible_fraction_across"] <= 0.05
    assert 0.03 <= s["boundary_fraction"] <= 0.08


def test_noise_dots_pockets_ignore_illumination(card_of_bricks):
    s = card_of_bricks
    assert s["noise"] == pytest.approx(0.005 / LEVEL, rel=0.25)
    assert s["dots_per_megapixel"] == pytest.approx(10 / 0.48, rel=0.2)
    assert s["pocket_fraction"] == pytest.approx(1600 / 480000, rel=0.05)
    assert s["ignore_fraction"] == pytest.approx(0.125, rel=0.02)
    assert s["illumination_range"] < 0.01


def test_sharpness_is_relative_to_the_sharpest_image():
    img, mask = bricks()
    soft = ndi.gaussian_filter(img, 3)
    card = cc.make_card([("sharp", img, mask), ("soft", soft, mask)])
    sharp_s, soft_s = (r["sharpness_median"] for r in card["per_image"])
    assert sharp_s == pytest.approx(1.0)
    assert soft_s < 0.5
    assert card["per_image"][1]["boundary_contrast"]["median"] < card["per_image"][0]["boundary_contrast"]["median"]


def test_cli_pairs_images_and_masks_and_writes_the_card(tmp_path):
    img, mask = bricks()
    (tmp_path / "im").mkdir()
    (tmp_path / "mk").mkdir()
    Image.fromarray((img * 255).astype(np.uint8)).convert("RGB").save(tmp_path / "im" / "a.jpg", quality=95)
    Image.fromarray(mask).save(tmp_path / "mk" / "a_mask.png")
    cc.main(["--images", str(tmp_path / "im"), "--masks", str(tmp_path / "mk"), "--out", str(tmp_path / "card.json")])
    card = json.loads((tmp_path / "card.json").read_text())
    assert card["n_images"] == 1 and card["per_image"][0]["image"] == "a"
    for key in cc.SUMMARY_KEYS:
        lo, hi = card["summary"][key]
        assert lo <= hi


def test_the_shipped_example_card_is_complete():
    card = cc.load_card(ROOT / "data" / "cards" / "coarse_cells_example.json")
    for key in cc.SUMMARY_KEYS_V1:
        lo, hi = card["summary"][key]
        assert lo <= hi
    assert card["summary"]["cell_width_px"] == [51, 67]
    assert card["soft"]["ignore_fraction"] == [0.34, 0.55]


def test_the_shipped_example_card_v2_is_complete_and_marks_its_assumptions():
    card = cc.load_card(ROOT / "data" / "cards" / "coarse_cells_example_v2.json")
    assert card["card_version"] == 2
    for key in cc.SUMMARY_KEYS:
        lo, hi = card["summary"][key]
        assert lo <= hi
    assert card["summary"]["invisible_fraction_local_along"] == [0.13, 0.21]
    assert card["summary"]["invisible_fraction_along"][1] <= 0.03          # the 5x5 measure on the real images
    assert {"gap_run_p50_px", "gap_run_p90_px", "gap_share_gt5", "gap_share_gt10", "gap_share_gt20"} <= set(card["assumed"])


# --- card v2 (round 3): contrast with a 3x3 window and a local interior level, and gap runs along the boundaries ---

def beaded(gap=5, dark=10, long_gap_rows=(305, 320), noise=0.0, gradient=0.0, seed=0):
    """Vertical bricks whose boundaries along the cells are beaded: dark segments (`dark` rows, contrast 0.25) and
    short gaps (`gap` rows, at cell level), plus one long gap on every line; boundaries across the cells continuous
    (contrast 0.15). Optional left-to-right illumination gradient (the level drops by `gradient` across the image)."""
    rng = np.random.default_rng(seed)
    h, w = 600, 800
    mask = np.ones((h, w), np.uint8)
    cols = np.arange(w)
    vline = (cols % (W + B)) >= W
    mask[:, vline] = 0
    dark_vline = np.zeros(h, bool)
    for r0 in range(0, h, gap + dark):
        dark_vline[r0:r0 + dark] = True
    dark_vline[long_gap_rows[0]:long_gap_rows[1]] = False
    rel = np.zeros((h, w))                                      # relative darkness of each pixel
    rel[np.ix_(dark_vline, vline)] = 0.25
    for c0 in range(0, w, W + B):
        off = (c0 // (W + B)) % 2 * (L // 2)
        for r in range(off, h, L + B):
            mask[r:r + B, c0:c0 + W] = 0
            rel[r:r + B, c0:c0 + W] = 0.15
    level = LEVEL * (1 - gradient * cols / w)[None, :] * np.ones((h, 1))
    img = level * (1 - rel) + rng.normal(0, noise, (h, w))
    return np.clip(img, 0, 1), mask


def test_local_contrast_sees_short_gaps_that_the_5x5_window_jumps():
    img, mask = beaded(gap=5, dark=10)
    s = cc.image_stats(img, mask)
    # along the cells: a 5-row gap is invisible over its 3 middle rows with a 3x3 window, its middle row with 5x5
    assert 0.15 <= s["invisible_fraction_local_along"] <= 0.27
    assert s["invisible_fraction_along"] <= 0.12
    assert s["invisible_fraction_local_across"] <= 0.05
    assert s["contrast_local"]["median"] == pytest.approx(0.25, abs=0.03)


def test_gap_runs_along_the_boundary_skeleton():
    img, mask = beaded(gap=5, dark=10, long_gap_rows=(305, 320))
    s = cc.image_stats(img, mask)
    assert 2 <= s["gap_run_p50_px"] <= 4                         # the short gaps: 5 rows, 3 invisible with 3x3
    assert s["gap_share_gt10"] > 0.005                           # the long gaps (13 invisible rows) ...
    assert s["gap_share_gt20"] == 0.0                            # ... are not longer than 20 px
    assert s["gap_share_gt5"] == pytest.approx(s["gap_share_gt10"], abs=1e-9)   # nothing between 5 and 10 px


def test_local_contrast_follows_uneven_illumination():
    img, mask = beaded(gap=5, dark=10, gradient=0.2)
    s = cc.image_stats(img, mask)
    assert s["contrast_local"]["median"] == pytest.approx(0.25, abs=0.03)   # relative to the cells nearby


def test_version_1_cards_still_load():
    card = cc.load_card(ROOT / "data" / "cards" / "coarse_cells_example.json")
    assert card["card_version"] == 1
    assert "invisible_fraction_local_along" not in card["summary"]
