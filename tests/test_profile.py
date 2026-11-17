"""The profiler: modes, widths, colour, region of interest; and scale adaptation of the flakes method."""

import numpy as np
import pytest
from PIL import Image

from src.methods import METHODS
from src.methods.profile import profile, region_of_interest, scaled_params, to_gray, REFERENCE_FLAKE_WIDTH_PX
from src.synth_lamellar import Spec, generate
from src import axial


def test_a_boundary_network_is_recognised_and_its_widths_measured():
    g = generate(Spec("x", "t", seed=1, kappa=25, shape=(384, 512), n_colonies=4, lath_width_px=(9, 11),
                      boundary_px=2.0, gap_fraction=0.0))["image"] / 255.0
    p = profile(g)
    assert p["mode"] == "boundaries" and p["flake_phase"] == "bright"
    assert 7 <= p["by_mode"]["boundaries"]["flake_width_px"] <= 13


def test_islands_are_recognised():
    rows, cols = np.mgrid[0:300, 0:400]
    rng = np.random.default_rng(0)
    img = np.full((300, 400), 0.8)
    for _ in range(40):
        r0, c0 = rng.uniform(0, 300), rng.uniform(0, 400)
        img[(np.abs(rows - r0) < 4) & (np.abs(cols - c0) < 25)] = 0.25   # dark laths in a bright matrix
    p = profile(img + rng.normal(0, 0.01, img.shape))
    assert p["mode"] in ("phase", "ambiguous") and p["by_mode"]["phase"]["flake_phase"] == "dark"
    assert 5 <= p["by_mode"]["phase"]["flake_width_px"] <= 11


def test_colour_contrast_is_kept_when_luminance_hides_it():
    rgb = np.zeros((50, 60, 3))
    rgb[:, :30] = (0.6, 0.3, 0.6)     # purple
    rgb[:, 30:] = (0.35, 0.45, 0.65)  # blue of about the same luminance
    g = to_gray((rgb * 255).astype(np.uint8))
    assert abs(g[:, :30].mean() - g[:, 30:].mean()) > 0.5


def test_white_padding_is_left_out_of_the_region_of_interest():
    g = generate(Spec("x", "t", seed=2, kappa=4, shape=(300, 400), n_colonies=3))["image"] / 255.0
    g[200:, 250:] = 1.0  # a white padding block, as in figure panels
    roi = region_of_interest(g)
    assert roi[250, 330] == False and roi[100, 100] == True  # noqa: E712


def test_at_equal_fractions_the_islands_are_the_flakes_not_the_connected_matrix():
    # dark separate islands covering a little more than half the area, in a connected bright matrix:
    # the minority (bright) is the matrix, and the phase reading must take the dark islands
    rng = np.random.default_rng(3)
    g = np.full((340, 476), 0.8)
    for r in range(0, 340, 17):
        for c in range(0, 476, 34):
            g[r + 3: r + 14, c + 3: c + 31] = 0.25
    g = np.clip(g + rng.normal(0, 0.02, g.shape), 0, 1)
    p = profile(g)
    assert 0.5 < 1 - p["minority_fraction"] < 0.58
    assert p["by_mode"]["phase"]["flake_phase"] == "dark"
    assert p["majority_connectivity"] < p["minority_connectivity"]


def test_saturated_flakes_stay_in_the_region_of_interest_but_padding_does_not():
    # laths clipped at white (digitally flat, like padding) inside the specimen must be kept;
    # a white frame around the panel is padding
    g = generate(Spec("sat", "t", seed=5, shape=(400, 520), lath_width_px=(18, 30), noise=0.01))["image"] / 255.0
    g = np.clip(g * 1.2, 0, 1)  # the brightest laths saturate
    assert (g >= 1.0).mean() > 0.1
    assert region_of_interest(g).mean() > 0.9
    framed = np.ones((480, 600))
    framed[40:440, 40:560] = g
    roi = region_of_interest(framed)
    assert roi[:30].mean() == 0 and roi[:, :30].mean() == 0 and roi[60:420, 60:540].mean() > 0.9


def test_scaled_params_follow_the_flake_width_beyond_the_dead_zone_and_keep_given_ones():
    finer = scaled_params(0.7 * REFERENCE_FLAKE_WIDTH_PX, None, set(), {})
    assert finer["min_area_px"] == 30 and finer["bridge_gaps_px"] == 9         # defaults kept down to half the width
    out = scaled_params(2 * REFERENCE_FLAKE_WIDTH_PX, None, {"h_px"}, {"h_px": 0.7})
    assert out["min_area_px"] == 120 and out["bridge_gaps_px"] == 18 and "h_px" not in out  # coarser: proportional


@pytest.mark.parametrize("zoom", [0.33, 0.5, 2.0, 3.0])
def test_auto_scale_makes_the_result_independent_of_magnification(tmp_path, zoom):
    g = generate(Spec("x", "t", seed=5, kappa=25, mean_direction_deg=30, shape=(512, 640), n_colonies=4,
                      lath_width_px=(8, 14), lath_length_px=(50, 180)))["image"]
    base = tmp_path / "base.png"
    Image.fromarray(g).save(base)
    z = Image.fromarray(g).resize((int(640 * zoom), int(512 * zoom)), Image.BICUBIC)
    zp = tmp_path / "zoom.png"
    z.save(zp)
    s0 = METHODS["flakes"]([base], {}, tmp_path / "a")["image_summaries"]["base"]
    s1 = METHODS["flakes"]([zp], {}, tmp_path / "b")["image_summaries"]["zoom"]
    assert axial.axial_diff(s0["mean_direction_deg"], s1["mean_direction_deg"]) < 3
    assert abs(s0["order_parameter"] - s1["order_parameter"]) < 0.06
    if zoom >= 0.5:  # at 0.33x laths are ~3 px wide and their boundaries under the resolution: shape is lost
        assert s1["median_aspect_ratio"] == pytest.approx(s0["median_aspect_ratio"], rel=0.25)


def test_an_sem_data_bar_is_left_out_of_the_region_of_interest():
    # a strip of grey boxes with text-like marks under a full-width border line, as on SEM micrographs
    g = generate(Spec("bar", "t", seed=8, shape=(400, 520), noise=0.02))["image"] / 255.0
    g[352:, :] = 0.25
    g[352:354, :] = 0.95  # the bar's top border
    rng = np.random.default_rng(1)
    for c in range(10, 470, 60):  # "text"
        g[365:390, c: c + 40] = np.where(rng.uniform(size=(25, 40)) < 0.3, 0.95, 0.25)
    roi = region_of_interest(g)
    assert roi[360:].mean() == 0
    assert roi[50:300, 50:470].mean() > 0.9
    # and nothing is cut from an image without a bar
    plain = generate(Spec("plain", "t", seed=8, shape=(400, 520), noise=0.02))["image"] / 255.0
    assert region_of_interest(plain)[330:390, 50:470].mean() > 0.9
