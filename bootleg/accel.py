import platform
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Accel:
    hwaccel: str | None
    h264_encoder: str
    torch_device: str


def _ffmpeg_encoders() -> str:
    try:
        return subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


@lru_cache(maxsize=1)
def detect_accel() -> Accel:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg not found on PATH. Install it: brew install ffmpeg")

    encoders = _ffmpeg_encoders()

    if platform.system() == "Darwin" and "h264_videotoolbox" in encoders:
        return Accel("videotoolbox", "h264_videotoolbox", _torch_device())
    if "h264_nvenc" in encoders and _has_cuda():
        return Accel("cuda", "h264_nvenc", "cuda")
    if "h264_qsv" in encoders:
        return Accel("qsv", "h264_qsv", "cpu")
    return Accel(None, "libx264", _torch_device())


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001 -- any driver/import failure means "no cuda"
        return False


def _torch_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:  # noqa: BLE001, S110 -- fall back to cpu on any detection failure
        pass
    return "cpu"
