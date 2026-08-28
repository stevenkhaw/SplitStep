import platform
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from splitstep.resources import ffmpeg_exe


@dataclass(frozen=True)
class Accel:
    hwaccel: str | None
    h264_encoder: str
    torch_device: str


def _ffmpeg_encoders() -> str:
    try:
        return subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-encoders"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


@lru_cache(maxsize=1)
def detect_accel() -> Accel:
    ffmpeg_exe()  # raises with a platform-appropriate install hint if absent

    encoders = _ffmpeg_encoders()
    torch_device = _torch_device()

    if platform.system() == "Darwin" and "h264_videotoolbox" in encoders:
        return Accel("videotoolbox", "h264_videotoolbox", torch_device)
    if "h264_nvenc" in encoders:
        return Accel("cuda", "h264_nvenc", torch_device)
    if "h264_qsv" in encoders:
        return Accel("qsv", "h264_qsv", torch_device)
    return Accel(None, "libx264", torch_device)


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
