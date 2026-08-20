def test_ground_fixture_has_the_expected_shape(ground_features):
    frames = ground_features
    assert len(frames) == 1200
    assert frames[0].t_ms == 200_000
    assert sum(1 for f in frames if f.near is not None) == 1103
    assert sum(1 for f in frames if f.near is not None and f.far is not None) == 735
