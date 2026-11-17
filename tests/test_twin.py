"""The synthetic twin: its fit stays lath-like, and an enlarged twin keeps consistent truth."""

import numpy as np
import pandas as pd
from PIL import Image

from src import twin
from src.evaluate import flake_truth
from src.synth_lamellar import Spec, generate, write_dataset


def test_an_enlarged_twin_keeps_its_truth_consistent(tmp_path):
    specs = [Spec("a_clean_0", "a_clean", seed=1, shape=(120, 160), lath_width_px=(4, 6), boundary_px=1.5),
             Spec("a_clean_1", "a_clean", seed=2, shape=(120, 160), lath_width_px=(4, 6), boundary_px=1.5)]
    write_dataset(tmp_path, specs)
    before = pd.read_csv(tmp_path / "truth" / "flakes.csv")
    truth_before = flake_truth({}, ["a_clean_0"], tmp_path)["a_clean_0"]["stats"]
    from src.synth_lamellar import upsample_dataset

    upsample_dataset(tmp_path, 3)
    assert Image.open(tmp_path / "images" / "a_clean_0.png").size == (480, 360)
    ids = np.array(Image.open(tmp_path / "truth" / "flake_ids" / "a_clean_0.png"))
    assert ids.shape == (360, 480)
    after = pd.read_csv(tmp_path / "truth" / "flakes.csv")
    assert np.allclose(after.area_px, before.area_px * 9) and np.allclose(after.major_px, before.major_px * 3, rtol=1e-4)
    assert np.allclose(after.aspect_ratio, before.aspect_ratio)
    truth_after = flake_truth({}, ["a_clean_0"], tmp_path)["a_clean_0"]["stats"]
    # (the truth's minimum flake area is in pixels of the enlarged image, so a few tiny flakes join: S moves slightly)
    assert abs(truth_after["order_parameter"] - truth_before["order_parameter"]) < 0.01


def test_the_twin_fit_stays_lath_like_for_wide_flakes_with_thick_boundaries():
    # the case that ran away (JOURNAL 2026-11-17): flakes ~18 px with ~12 px boundaries, low noise
    g = generate(Spec("r", "t", seed=7, shape=(600, 800), lath_width_px=(18, 30), lath_length_px=(80, 300),
                      boundary_px=12, noise=0.01))["image"] / 255.0
    from src.methods import profile as prof_mod

    p = prof_mod.profile(g)
    target = {**p, "by_mode": p["by_mode"]}
    spec, hist = twin.fit_spec(target, "boundaries", None, 0, iters=4)
    w_mid = np.mean(spec.lath_width_px)
    assert 0.5 * 24 <= w_mid <= 2.0 * 24  # near the true width, not run away
    assert spec.lath_length_px[0] >= 2.0 * w_mid  # laths, not blobs
    assert min(h["mismatch"] for h in hist) < np.log(1.35)


def test_the_structure_fit_matches_the_aspect_ratio_the_method_measures(tmp_path):
    # "real" images with short laths; a twin spec that starts with long ones must be brought to the same measured AR
    real = []
    for k in range(2):
        g = generate(Spec(f"r{k}", "t", seed=20 + k, shape=(400, 520), kappa=(0.0, 25.0)[k], lath_width_px=(8, 12),
                          lath_length_px=(20, 45), boundary_px=2.5, noise=0.03))["image"]
        real.append(tmp_path / f"r{k}.png")
        Image.fromarray(g).save(real[-1])
    params = {"mode": "boundaries"}
    target = twin.method_structure(real, params)
    start = Spec("preview", "preview", seed=0, shape=(400, 520), lath_width_px=(8, 12), lath_length_px=(60, 200),
                 boundary_px=2.5, noise=0.03)
    spec, hist = twin.fit_structure(start, target, params, iters=3)
    assert hist[0]["mismatch"] > np.log(1.3)  # the long laths measure clearly longer
    assert min(h["mismatch"] for h in hist) < np.log(1.25)
    assert spec.lath_length_px[1] < 200
