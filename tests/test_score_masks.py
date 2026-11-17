"""src.score_masks: flake labels scored against hand-corrected masks (0 boundary, 1 flake, 2 not a flake, 3 ignore)."""

import json

import numpy as np
from PIL import Image
from skimage.measure import label

from src import score_masks as sm


def _mask():
    """6 x 4 bricks of 20 x 60 px (long axis along x) between 2 px boundaries, a pocket, an ignore band."""
    m = np.zeros((96, 384), np.uint8)
    for r in range(4):
        for c in range(6):
            m[2 + r * 23: 2 + r * 23 + 20, 2 + c * 63: 2 + c * 63 + 60] = 1
    m[25:45, 128:188] = 2   # one brick is a pocket ("not a flake")
    m[:, 314:] = 3          # the last column of bricks is out of focus
    return m


def _perfect(m):
    return label(m == 1, connectivity=1)


def test_perfect_labels_score_one_and_zero_errors():
    m = _mask()
    r = sm.score_image(_perfect(m), m)
    assert r["n_true"] == 4 * 5 - 1 and r["tp"] == r["n_true"] and r["fp"] == 0
    assert r["precision"] == r["recall"] == r["f1"] == 1.0
    assert r["merges"] == r["splits"] == r["pocket_fp"] == 0
    assert abs(r["direction_error_deg"]) < 1e-6 and abs(r["order_error"]) < 1e-9 and abs(r["aspect_ratio_rel_error"]) < 1e-9


def test_a_merge_a_split_and_a_pocket_are_counted():
    m = _mask()
    lab = _perfect(m)
    a, b = lab[10, 30], lab[10, 90]        # two bricks side by side in the first row
    lab[2:22, 62:65] = a                    # bridge the boundary between them
    lab[lab == b] = a                       # one region over two bricks: a merge
    c = lab[60, 30]
    lab[(lab == c) & (np.arange(384)[None, :] >= 32)] = lab.max() + 1  # one brick cut in two: a split
    lab[25:45, 128:188] = lab.max() + 1     # a region on the pocket
    lab[10:30, 330:380] = lab.max() + 1     # a region in the ignore band: not scored
    r = sm.score_image(lab, m)
    assert r["merges"] == 1 and r["splits"] == 1 and r["pocket_fp"] == 1
    assert r["tp"] == r["n_true"] - 3      # the two merged bricks and the split one are missed
    assert r["fp"] == 1 + 2 + 1            # the merged region, the two halves, the pocket region
    assert r["n_pred"] == r["tp"] + r["fp"]


def test_the_cli_pairs_files_by_name_and_writes_json(tmp_path, capsys):
    m = _mask()
    (tmp_path / "labels").mkdir()
    (tmp_path / "masks").mkdir()
    Image.fromarray(_perfect(m).astype(np.uint16)).save(tmp_path / "labels" / "img_01.png")
    Image.fromarray(m).save(tmp_path / "masks" / "img_01_mask.png")
    out = tmp_path / "score.json"
    sm.main(["--labels", str(tmp_path / "labels"), "--masks", str(tmp_path / "masks"), "--json", str(out)])
    res = json.loads(out.read_text())
    assert res["summary"]["f1"] == 1.0 and res["summary"]["n_images"] == 1
    assert "img_01" in res["images"]
    printed = capsys.readouterr().out
    assert "F1" in printed and "img_01" in printed


def test_labels_at_another_size_are_resized_to_the_mask():
    m = _mask()
    lab = _perfect(m)
    big = np.kron(lab, np.ones((2, 2), lab.dtype))
    r = sm.score_image(big, m)
    assert r["f1"] == 1.0 and r["resized"]
