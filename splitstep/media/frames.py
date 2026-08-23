from pathlib import Path

from splitstep.media.transcode import rotation_filter, run_ffmpeg

FFMPEG_TIMEOUT_S = 20.0


def extract_frame(
    src: Path,
    dst: Path,
    at_ms: int = 0,
    width: int = 1280,
    rotation_deg: int = 0,
    hwaccel: str | None = None,
    timeout: float = FFMPEG_TIMEOUT_S,
) -> None:
    """Write one frame as a JPEG, rotated `rotation_deg` clockwise.

    Rotation is applied before scaling, so `width` always means the width of
    the upright image -- the play-region quad is stored in normalized 0-1
    coordinates against the upright frame, so a squashed or sideways still
    would put every dragged corner in the wrong place. Setup previews read
    a 4K HEVC original rather than the 1080p proxy, which is why the
    decoder can be hardware-accelerated and why the timeout is generous.

    `-noautorotate` plus `-display_rotation 0` (the same pair `make_proxy`
    uses in transcode.py, and for the same reason) keep the source's own
    display matrix from reaching the scale filter or the output container:
    the only rotation that can land in the output -- pixels or metadata --
    is the one `rotation_filter` applies here. Without `-display_rotation
    0`, the OUTPUT inherits the source's display matrix, and a JPEG can
    carry rotation metadata the browser then applies on top of pixels this
    function already rotated -- a preview the browser silently re-rotates
    would disagree with the quad the user drags over it.

    A single frame extraction is a request-handler call site (unlike the
    background-job transcodes in transcode.py), so it is given a short
    timeout rather than the default unbounded one -- a hung ffmpeg here
    would otherwise tie up a request-handling thread indefinitely.
    """
    vf = ",".join(f for f in (rotation_filter(rotation_deg), f"scale={width}:-2") if f)
    dst.parent.mkdir(parents=True, exist_ok=True)

    args: list[str] = ["-noautorotate"]
    if hwaccel:
        args += ["-hwaccel", hwaccel]
    args += [
        "-display_rotation", "0",
        "-ss", f"{at_ms / 1000:.3f}",
        "-i", str(src),
        "-vf", vf,
        "-frames:v", "1",
        "-q:v", "3",
        str(dst),
    ]
    run_ffmpeg(args, timeout=timeout)
