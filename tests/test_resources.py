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


def test_bundled_font_wins(frozen):
    (frozen / "font.ttf").write_bytes(b"")
    assert resources.overlay_font() == str(frozen / "font.ttf")


def test_env_font_is_the_escape_hatch_for_a_mac_without_the_system_paths(
    monkeypatch, tmp_path
):
    font = tmp_path / "custom.ttf"
    font.write_bytes(b"")
    monkeypatch.setenv("SPLITSTEP_FONT", str(font))
    assert resources.overlay_font() == str(font)


def test_system_font_resolves_on_this_mac(monkeypatch):
    # No bundle, no env override -- this Mac's own Arial Bold/Arial/
    # Helvetica.ttc must still resolve, since that fallback is the whole
    # point of the list.
    monkeypatch.delenv("SPLITSTEP_FONT", raising=False)
    found = resources.overlay_font()
    assert Path(found).is_file()
