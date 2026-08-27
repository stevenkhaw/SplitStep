from splitstep.media.numbered import drawtext_filters


def test_counter_and_note_are_two_drawtext_filters():
    vf = drawtext_filters("3/20", "match point", "/tmp/f.ttf")
    assert vf.count("drawtext=") == 2
    assert "text='3/20'" in vf and "text='match point'" in vf
    assert vf.count("expansion=none") == 2


def test_no_note_means_one_filter():
    assert drawtext_filters("3/20", "", "/tmp/f.ttf").count("drawtext=") == 1


def test_note_text_is_escaped_for_the_filtergraph():
    vf = drawtext_filters("1/2", "it's 50%: a,b\\c", "/tmp/f.ttf")
    # The five characters that terminate or re-interpret a drawtext value.
    assert "\\'" in vf and "\\:" in vf and "\\," in vf and "\\\\" in vf
