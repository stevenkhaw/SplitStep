from bootleg.detect.features import FeatureFrame, Player, read_features, write_features


def test_frame_json_round_trip():
    f = FeatureFrame(
        t_ms=41200, n=2,
        near=Player(cx=0.42, foot=0.88, h=0.31, v=1.8),
        far=Player(cx=0.55, foot=0.41, h=0.09, v=2.1),
        hits=2, hit_reg=0.81,
    )
    assert FeatureFrame.from_json_line(f.to_json_line()) == f


def test_frame_round_trip_with_missing_players():
    f = FeatureFrame(t_ms=0, n=0, near=None, far=None, hits=0, hit_reg=0.0)
    assert FeatureFrame.from_json_line(f.to_json_line()) == f


def test_write_then_read_file(tmp_path):
    frames = [
        FeatureFrame(t_ms=i * 200, n=1, near=Player(0.5, 0.9, 0.3, 0.4),
                     far=None, hits=0, hit_reg=0.0)
        for i in range(5)
    ]
    path = tmp_path / "features.jsonl"
    write_features(path, frames)
    assert read_features(path) == frames


def test_read_skips_blank_lines(tmp_path):
    path = tmp_path / "features.jsonl"
    path.write_text('{"t":0,"n":0,"hits":0,"hit_reg":0.0}\n\n')
    assert len(read_features(path)) == 1


def test_to_json_line_quantizes_high_precision_floats():
    # High-precision values do NOT round-trip exactly - they are quantized
    # to 4 decimal places by design (see to_json_line docstring). This test
    # documents the real contract instead of the "exact round-trip" claim,
    # which every other fixture (all values already <=4dp) can't catch.
    f = FeatureFrame(
        t_ms=1000, n=1,
        near=Player(cx=1 / 3, foot=0.5, h=0.2, v=1.00005),
        far=None, hits=0, hit_reg=1 / 3,
    )
    written = f.to_json_line()
    roundtripped = FeatureFrame.from_json_line(written)

    assert roundtripped.near.cx == 0.3333
    assert roundtripped.near.v == 1.0001
    assert roundtripped.hit_reg == 0.3333
    assert roundtripped != f  # quantization is lossy for high-precision input

    # But quantization is idempotent: reading a quantized line and writing
    # it again produces the exact same bytes - repeated read/write cycles
    # (e.g. re-segmentation) are stable and never drift further.
    rewritten = roundtripped.to_json_line()
    assert rewritten == written
