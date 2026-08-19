from pathlib import Path

from bootleg.media.transcode import run_ffmpeg

FFMPEG_TIMEOUT_S = 30.0


def extract_frame(src: Path, dst: Path, at_ms: int = 0, width: int = 1280) -> None:
    """Write one frame as a JPEG.

    Aspect ratio is preserved (`-2` derives the height) because the play-region
    quad is stored in normalized 0-1 coordinates -- a squashed still would put
    every dragged corner in the wrong place.

    A single frame extraction is a request-handler call site (unlike the
    background-job transcodes in transcode.py), so it is given a short
    timeout rather than the default unbounded one -- a hung ffmpeg here
    would otherwise tie up a request-handling thread indefinitely.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "-ss", f"{at_ms / 1000:.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "3",
        str(dst),
    ], timeout=FFMPEG_TIMEOUT_S)
