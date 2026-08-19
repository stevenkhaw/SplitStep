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
