"""Fence training, the held-out report, and the committed model.

`test_held_out_meets_the_spec_bar` is the spec's Validation 1 as a test: the
number that decides whether fence mode ships. It runs on the committed
2026-10-12 fixtures (real footage), so a change to features, tracking,
court margins or segmentation that costs a point fails here, not on
Steven's next session.
"""

import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from splitstep import resources
from splitstep.cli import main
from splitstep.db.schema import connect, migrate
from splitstep.db.sessions import add_source, find_or_create_session_for_date
from splitstep.detect import fence_train
from splitstep.detect.fence import FEATURE_NAMES, FenceModel
from splitstep.detect.truth import load_truth

FIXTURES = Path(__file__).parent / "fixtures"
NAME = "2026-10-12_source01"
FAULT = "missed first serve"


@pytest.fixture(scope="module")
def sets():
    return [fence_train.training_set(f.boxes, f.features, f.truth, f.corners)
            for f in fence_train.discover(FIXTURES)]


@pytest.fixture(scope="module")
def reports(sets):
    return fence_train.held_out(sets)


def test_discover_finds_the_committed_fixture():
    found = fence_train.discover(FIXTURES)
    assert [f.truth.stem for f in found] == [NAME]
    assert found[0].boxes.name == f"{NAME}_0-16min.jsonl"
    assert found[0].corners.name == f"{NAME}.corners.json"


def test_discover_skips_a_truth_file_without_boxes(tmp_path):
    # Spec, Validation 3: the next session's truth list is committed before
    # its boxes exist, and must not stop the others from training.
    (tmp_path / "truth").mkdir()
    (tmp_path / "truth" / "2026-11-01_source01.tsv").write_text("# covers 0:00-1:00\n")
    (tmp_path / "truth" / "2026-11-01_source01.corners.json").write_text("[]")
    assert fence_train.discover(tmp_path) == []


def test_training_set_keeps_covered_frames_and_raw_labels(sets):
    (s,) = sets
    lo, hi = s.truth.covers
    assert len(s.t_ms) == len(s.X) == len(s.y)
    assert s.t_ms.min() >= lo and s.t_ms.max() < hi
    # A first-serve fault is labelled play: the labels come from raw rows.
    fault = next(p for p in s.truth.points if p.note.startswith(FAULT))
    assert s.y[(s.t_ms >= fault.start_ms) & (s.t_ms < fault.end_ms)].all()


def test_held_out_meets_the_spec_bar(reports):
    """The spec's bar, read on the raw line.

    raw = clips before the fault fold vs every listed row; folded = the app's
    clips vs points with faults folded in -- `fence_train.legend`, which
    `splitstep fence train` prints under the same two lines.
    """
    raw, folded = reports[NAME]
    truth = load_truth(FIXTURES / "truth" / f"{NAME}.tsv")
    faults = {p.start_ms for p in truth.points if p.note.startswith(FAULT)}
    non_fault = len(truth.points) - len(faults)
    assert non_fault == 23
    missed_non_fault = [m for m in raw.missed if m not in faults]
    assert not missed_non_fault, f"non-fault rows missed held-out: {missed_non_fault}"
    assert raw.total == 38
    assert raw.caught >= 34
    assert raw.play_share >= 0.50
    # abs: clips ending five seconds early are as wrong as five late.
    assert abs(raw.median_end_err_s) <= 2.0
    # The folded side is reported, not barred, but it must be the 23 points.
    assert folded.total == 23


def test_held_out_never_predicts_a_block_with_a_model_that_saw_it(sets, monkeypatch):
    # Record what each fit was trained on: no fit used for a block may hold
    # a single row of that block.
    seen: list[set[int]] = []
    real_fit = fence_train.fit

    def spy(train, **kw):
        seen.append({int(t) for s in train for t in s.t_ms})
        return real_fit(train, **kw)

    monkeypatch.setattr(fence_train, "fit", spy)
    fence_train.held_out_probs(sets)
    (s,) = sets
    blocks = sorted(set((s.t_ms // 240_000).tolist()))
    assert len(seen) == len(blocks) == 4
    for k, trained in zip(blocks, seen, strict=True):
        block = {int(t) for t in s.t_ms[s.t_ms // 240_000 == k]}
        assert not trained & block
        assert trained | block == {int(t) for t in s.t_ms}


def test_committed_model_matches_fixture_features():
    model = FenceModel.load(resources.fence_model())
    assert model.features == FEATURE_NAMES
    assert NAME in model.trained_on
    assert model.threshold == fence_train.FITTED_THRESHOLD


def test_committed_model_is_what_training_writes(sets):
    # Training is deterministic, so the committed file is reproducible from
    # `splitstep fence train`; a hand-edited or stale model fails here.
    # Vectors to a relative 1e-4 rather than exactly: the file is rounded to
    # six figures, and a different BLAS may land a last digit the other way.
    fitted = fence_train.fit(sets)
    committed = FenceModel.load(resources.fence_model())
    for field in ("mean", "std", "weights"):
        assert np.allclose(getattr(fitted, field), getattr(committed, field),
                           rtol=1e-4, atol=1e-6), field
    assert fitted.bias == pytest.approx(committed.bias, rel=1e-4, abs=1e-6)
    assert replace(fitted, mean=(), std=(), weights=(), bias=0.0, features=()) == \
        replace(committed, mean=(), std=(), weights=(), bias=0.0, features=())


def test_fit_separates_play_in_sample(sets):
    (s,) = sets
    model = fence_train.fit(sets)
    p = model.predict(s.X)
    assert p[s.y == 1].mean() > p[s.y == 0].mean() + 0.2


def test_fit_refuses_no_sets():
    with pytest.raises(ValueError, match="at least one truth file"):
        fence_train.fit([])


def test_cli_fence_train_prints_held_out_and_writes_the_model(tmp_path, capsys):
    out = tmp_path / "model.json"
    assert main(["fence", "train", "--out", str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"{NAME} raw: points caught ")
    assert lines[0].endswith("(held-out)")
    assert lines[1].startswith(f"{NAME} folded: points caught ")
    model = FenceModel.load(out)
    assert lines[2] == fence_train.legend(model.threshold)
    assert model.trained_on == (NAME,)


@pytest.fixture
def fence_source(library):
    conn = connect(library.db_path)
    migrate(conn)
    session_id = find_or_create_session_for_date(conn, "2026-10-12")
    source_id, idx = add_source(
        conn, session_id, recorded_at="2026-10-12T10:00:00Z", duration_ms=960_000,
        width=3840, height=2160, fps=30.0, original_name=f"{uuid.uuid4().hex}.MOV",
    )
    corners = (FIXTURES / "truth" / f"{NAME}.corners.json").read_text()
    conn.execute("UPDATE sources SET court_corners = ? WHERE id = ?", (corners, source_id))
    conn.commit()
    src = library.source_dir(session_id, idx)
    src.mkdir(parents=True, exist_ok=True)
    (src / "boxes.jsonl").write_bytes(
        (FIXTURES / "boxes" / f"{NAME}_0-16min.jsonl").read_bytes())
    (src / "features.jsonl").write_bytes(
        (FIXTURES / "features" / f"{NAME}_0-16min.jsonl").read_bytes())
    return library, conn, source_id


def test_cli_fence_score_labels_its_own_training_file_in_sample(fence_source, capsys):
    library, _, source_id = fence_source
    assert main(["--library", str(library.root), "fence", "score", source_id]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("clips at threshold 0.25")
    assert out[1].startswith(f"{NAME} raw: points caught ")
    assert out[1].endswith("(in-sample)")
    assert out[2].startswith(f"{NAME} folded: ")


def test_cli_fence_score_without_corners_says_so(fence_source, capsys):
    library, conn, source_id = fence_source
    conn.execute("UPDATE sources SET court_corners = NULL WHERE id = ?", (source_id,))
    conn.commit()
    assert main(["--library", str(library.root), "fence", "score", source_id]) == 1
    assert "no court corners" in capsys.readouterr().err


def test_cli_fence_score_against_an_unseen_truth_is_not_in_sample(fence_source, tmp_path,
                                                                  capsys):
    library, _, source_id = fence_source
    other = tmp_path / "2026-11-01_source01.tsv"
    lines = (FIXTURES / "truth" / f"{NAME}.tsv").read_text().splitlines()
    other.write_text("\n".join(lines[:6]) + "\n")
    assert main(["--library", str(library.root), "fence", "score", source_id,
                 "--truth", str(other)]) == 0
    out = capsys.readouterr().out
    assert "2026-11-01_source01 raw: points caught " in out
    assert "in-sample" not in out


def test_legend_names_both_lines_and_the_models_threshold():
    line = fence_train.legend(0.31)
    assert "raw = clips before the fault fold" in line
    assert "folded = the app's clips" in line
    assert "threshold 0.31" in line
