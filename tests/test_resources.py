import sys
from pathlib import Path

import pytest

from splitstep import resources


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    """Simulate a PyInstaller bundle: sys._MEIPASS points at tmp."""
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    return tmp_path


def test_bundled_ffmpeg_wins_over_path(frozen):
    (frozen / "ffmpeg").write_bytes(b"")
    assert resources.ffmpeg_exe() == str(frozen / "ffmpeg")


def test_unfrozen_falls_back_to_path_lookup():
    # The dev machine has a real ffmpeg; the resolved value must be absolute.
    assert Path(resources.ffmpeg_exe()).is_absolute()


def test_missing_ffmpeg_names_a_platform_install_hint(monkeypatch):
    monkeypatch.setattr(resources.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError) as exc:
        resources.ffmpeg_exe()
    assert "ffmpeg" in str(exc.value)


def test_bundled_weights_win(frozen):
    (frozen / "yolo11n.pt").write_bytes(b"")
    assert resources.yolo_weights() == str(frozen / "yolo11n.pt")


def test_unfrozen_weights_keep_the_ultralytics_default():
    assert resources.yolo_weights() == "yolo11n.pt"


def test_bundled_spa_wins(frozen):
    (frozen / "web_dist").mkdir()
    (frozen / "web_dist" / "index.html").write_text("x")
    assert resources.spa_dist() == frozen / "web_dist"


def test_unfrozen_spa_is_the_source_tree():
    assert resources.spa_dist().parts[-2:] == ("web", "dist")


def test_the_packaged_font_is_what_ships(monkeypatch):
    # The point of committing it: a dev checkout and the frozen app resolve
    # the SAME file, so one reel cannot burn in two typefaces depending on
    # which one rendered it.
    monkeypatch.delenv("SPLITSTEP_FONT", raising=False)
    found = Path(resources.overlay_font())
    assert found.is_file()
    assert found.parts[-2:] == ("assets", "font.ttf")


def test_the_packaged_font_beats_a_bundle_root_copy(frozen, monkeypatch):
    # An older bundle put font.ttf at the bundle root. Package data wins, so
    # a stale root copy cannot reintroduce the split.
    monkeypatch.delenv("SPLITSTEP_FONT", raising=False)
    (frozen / "font.ttf").write_bytes(b"")
    assert Path(resources.overlay_font()).parts[-2:] == ("assets", "font.ttf")


def test_a_bundle_root_font_still_resolves_without_package_data(
    frozen, monkeypatch
):
    # Belt and braces for an install whose package data went missing.
    monkeypatch.delenv("SPLITSTEP_FONT", raising=False)
    (frozen / "font.ttf").write_bytes(b"")
    monkeypatch.setattr(
        resources, "__file__", str(frozen / "nowhere" / "resources.py")
    )
    assert resources.overlay_font() == str(frozen / "font.ttf")


def test_env_font_overrides_even_the_packaged_one(monkeypatch, tmp_path):
    font = tmp_path / "custom.ttf"
    font.write_bytes(b"")
    monkeypatch.setenv("SPLITSTEP_FONT", str(font))
    assert resources.overlay_font() == str(font)


def test_the_packaged_font_is_a_static_bold(monkeypatch):
    # media/numbered.py calls ImageFont.truetype without selecting a
    # variation, so a variable file would render its default instance --
    # Regular -- and silently lighten a burn that was checked frame by frame.
    # The weight class and the absence of fvar are what govern that; the name
    # records do not, which is why this asserts on the former.
    from fontTools.ttLib import TTFont

    monkeypatch.delenv("SPLITSTEP_FONT", raising=False)
    face = TTFont(resources.overlay_font())
    assert face["OS/2"].usWeightClass == 700
    assert "fvar" not in face, "still variable: would render Regular"
