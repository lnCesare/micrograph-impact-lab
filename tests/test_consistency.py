"""The label-free check on real images: a rotation-equivariant measurement passes, a biased one fails."""

import numpy as np
from PIL import Image

from src import consistency
from src.methods import METHODS
from src.synth_lamellar import Spec, generate

TOL = {"mean_direction_deg": 3.0, "order_parameter": 0.03, "aspect_ratio_rel": 0.05}


def test_disk_pair_turns_the_content():
    img = np.zeros((101, 121), np.uint8)
    img[48:53, 20:100] = 255  # a horizontal bar through the centre
    a, b = consistency.disk_pair(img, 30)
    assert a.shape == b.shape and a[:2, :2].max() == 0  # outside the disk is black
    rr, cc = np.nonzero(b > 128)
    slope = -np.polyfit(cc, rr, 1)[0]  # y up
    assert abs(np.degrees(np.arctan(slope)) - 30) < 3


def test_the_flake_method_turns_with_the_image(tmp_path):
    g = generate(Spec("x", "t", seed=3, kappa=25, mean_direction_deg=50, shape=(384, 384), n_colonies=4))
    path = tmp_path / "x.png"
    Image.fromarray(g["image"]).save(path)
    result = METHODS["flakes"]([path], {}, tmp_path / "out")
    out = consistency.check(METHODS["flakes"], [path], {}, tmp_path / "c", result, TOL)
    assert out["metrics"]["median_rotation_direction_diff_deg"] < 3
    assert out["metrics"]["worst_consistency_ratio"] is not None


def test_a_method_tied_to_the_image_axes_fails(tmp_path):
    g = generate(Spec("x", "t", seed=3, kappa=25, mean_direction_deg=50, shape=(384, 384), n_colonies=4))
    path = tmp_path / "x.png"
    Image.fromarray(g["image"]).save(path)

    def axis_bound(images, params, out_dir, overlay=()):  # always answers 50 deg, whatever the image
        s = {"mean_direction_deg": 50.0, "order_parameter": 0.9, "median_aspect_ratio": 4.0,
             "tensor_order_parameter": 0.9, "n_used_orientation": 10, "n_scratches": 0}
        return {"image_summaries": {p.stem: dict(s) for p in map(type(path), images)}}

    out = consistency.check(axis_bound, [path], {}, tmp_path / "c", axis_bound([path], {}, None), TOL)
    assert out["metrics"]["median_rotation_direction_diff_deg"] == 30.0
    assert out["metrics"]["worst_consistency_ratio"] > 1
