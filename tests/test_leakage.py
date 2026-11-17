"""Methods measure, the evaluator scores: no file in src/methods/ may touch the ground truth or the split."""

from pathlib import Path

import pytest

METHODS_DIR = Path(__file__).resolve().parents[1] / "src" / "methods"
FORBIDDEN = ["ground_truth", "labels_", "masks", "artifact_mask", "holdout", "splits", "evaluate",
             "truth/", "Ti64_scratched", "lamellar_synth", "scratch_paste"]


def leaks(text: str) -> list[str]:
    return [token for token in FORBIDDEN if token in text]


def test_the_scanner_flags_a_planted_leak():
    assert leaks("from src.evaluate import truth") == ["evaluate"]
    assert leaks("np.array(Image.open(root / 'labels_grain' / name))") == ["labels_"]


@pytest.mark.parametrize("path", sorted(METHODS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_methods_do_not_touch_ground_truth(path):
    assert leaks(path.read_text()) == [], f"{path.name} mentions ground truth or the split"


def test_only_the_evaluator_reads_the_defect_masks():
    src = METHODS_DIR.parent
    readers = sorted(str(p.relative_to(src)) for p in src.rglob("*.py") if "artifact_mask" in p.read_text())
    assert readers in ([], ["evaluate.py"])


def test_only_the_evaluator_and_the_paster_know_where_the_pasted_scratches_are():
    src = METHODS_DIR.parent
    readers = sorted(str(p.relative_to(src)) for p in src.rglob("*.py")
                     if '"truth"' in p.read_text() and '"scratch"' in p.read_text())
    # (the generators write the truth of their own images: synth_lamellar, and synth_cells for the coarse cells)
    assert set(readers) <= {"evaluate.py", "scratch_paste.py", "synth_lamellar.py", "synth_cells.py"}, readers

