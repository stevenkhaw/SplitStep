import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

from bootleg.media.probe import ffprobe_json, probe
from bootleg.media.transcode import (
    CLIP_CRF,
    CLIP_FPS,
    ProgressFn,
    TranscodeError,
    run_ffmpeg,
)

log = logging.getLogger(__name__)


class ConcatError(Exception):
    """A reel could not be concatenated."""


# A concat that succeeds still shifts the total by a frame or so per input:
# each clip's duration is a whole number of frames at 30 fps, and the muxer
# rounds edit lists and the final sample's duration independently. 40ms is a
# little over one frame at the locked rate.
_TOLERANCE_BASE_MS = 100
_TOLERANCE_PER_INPUT_MS = 40


def tolerance_ms(n_inputs: int) -> int:
    """How far the concatenated duration may sit from the sum of its inputs.

    Deliberately generous. The failure this catches is a reel quietly
    missing its last four points -- tens of seconds -- not a rounding
    remainder, so a tight bound would only buy false re-encodes on
    well-formed output. Being wrong the other way is cheap: the fallback
    costs a re-encode, and it is logged.
    """
    return _TOLERANCE_BASE_MS + _TOLERANCE_PER_INPUT_MS * max(0, n_inputs)


@dataclass(frozen=True)
class ClipParams:
    """The stream parameters `-c copy` silently assumes every input shares.

    This exists because ffmpeg does NOT check them. The design was written
    assuming the concat demuxer refuses streams whose codec parameters
    differ; measured on ffmpeg 9.0.1 it does not. It exits 0 with empty
    stderr and reads every input through the FIRST clip's parameters, so a
    mismatched sample aspect plays that clip at the wrong shape for its
    whole duration, and an input carrying no audio stream contributes no
    audio -- the reel's track stops early while the picture runs on.

    Neither of those changes the output's DURATION, so neither is visible to
    the duration check in concat_clips. Comparing the inputs up front is the
    only thing that can see them: it is the loud refusal ffmpeg declines to
    give.
    """

    codec: str
    profile: str
    width: int
    height: int
    sample_aspect_ratio: str
    pix_fmt: str
    frame_rate: str
    audio_codec: str | None
    audio_sample_rate: str | None
    audio_channels: int | None


def clip_params(path: Path) -> ClipParams:
    """Read the parameters `-c copy` cares about out of one clip."""
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ConcatError(f"No video stream in {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return ClipParams(
        codec=video.get("codec_name", ""),
        profile=video.get("profile", ""),
        width=int(video["width"]),
        height=int(video["height"]),
        # ffprobe omits the field entirely for square pixels. Defaulting to
        # "1:1" rather than leaving it None is what makes a clip cut before
        # make_clip pinned SAR compare EQUAL to one cut after -- they really
        # are the same shape, and diverging over an absent tag would send
        # every pre-existing library down the re-encode path for nothing.
        sample_aspect_ratio=video.get("sample_aspect_ratio", "1:1"),
        pix_fmt=video.get("pix_fmt", ""),
        # r_frame_rate as ffprobe's raw string ("30/1"). Kept exact rather
        # than floated: mismatched rates break -c copy, and 30/1 against
        # 30000/1001 is precisely the difference a float comparison at any
        # tolerance would blur away.
        frame_rate=video.get("r_frame_rate", ""),
        audio_codec=None if audio is None else audio.get("codec_name"),
        audio_sample_rate=None if audio is None else audio.get("sample_rate"),
        audio_channels=None if audio is None else int(audio["channels"]),
    )


def divergences(paths: list[Path]) -> list[str]:
    """Every way a later clip's parameters differ from the first clip's.

    Compared against the FIRST clip rather than against the locked profile's
    constants on purpose: the first clip is what ffmpeg will actually read
    every other input through, so it is the thing they have to match. A
    library cut uniformly at some other profile still concatenates
    correctly, and refusing it would be a rule about our constants instead
    of about the output.

    Returns human-readable strings rather than a bool because the caller
    logs them: "b.mp4: sample_aspect_ratio is '2:1', expected '1:1'" is
    something a human can act on, and "inputs differ" is not.
    """
    if len(paths) < 2:
        return []
    first = clip_params(paths[0])
    reported: list[str] = []
    for path in paths[1:]:
        params = clip_params(path)
        if params == first:
            continue
        for field in fields(ClipParams):
            mine = getattr(params, field.name)
            theirs = getattr(first, field.name)
            if mine != theirs:
                reported.append(
                    f"{path.name}: {field.name} is {mine!r}, expected {theirs!r}"
                )
    return reported


def _escape(path: Path) -> str:
    """A path as the concat demuxer's `file '...'` directive spells it.

    The directive ends at the first unescaped quote, and its escape is the
    shell's: close, backslash-quote, reopen. Clip names are always
    NN-START-END.mp4 and carry none, but the library root is a user-chosen
    path on an external drive and may contain anything at all.
    """
    return str(path).replace("'", "'\\''")


def _reencode_args(base: list[str], tmp: Path) -> list[str]:
    """The fallback encode, at the locked profile's own settings.

    scale and pad are absent because every input already carries the locked
    frame -- or, when they do not, because the mismatch is exactly what sent
    us here and the first input's frame is what the demuxer imposes anyway.
    The RESULT is still a file that could itself be concatenated.
    """
    return [
        *base,
        "-r", str(CLIP_FPS),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", str(CLIP_CRF),
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(tmp),
    ]


def concat_clips(
    paths: list[Path], dst: Path, on_progress: ProgressFn | None = None
) -> str:
    """Concatenate clips sharing the locked profile into `dst`.

    Returns "copy" if `-c copy` produced a correct file, "reencode" if the
    inputs could not safely be copied or if the copy came out wrong.

    `-c copy` is the whole reason clips are cut at one locked profile: it
    remuxes without touching a pixel, so a twenty-minute reel takes about a
    second. Two guards stand around it, because ffmpeg provides neither:

    1. BEFORE: the inputs are compared to each other (see ClipParams). A
       divergence skips the copy entirely -- on ffmpeg 9.0.1 the copy would
       SUCCEED and yield a wrong-shaped or audio-truncated reel with a
       perfectly correct duration.
    2. AFTER: the output's duration is measured against the sum of the
       inputs, because a copy can also drop later inputs outright. That one
       IS visible in the duration, and this is the check that sees it.

    `on_progress` is threaded to the re-encode only. The copy is effectively
    instantaneous; the fallback is minutes on a real reel, and it is the one
    worth a badge.
    """
    if not paths:
        raise ConcatError("a reel needs at least one clip to concatenate")
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise ConcatError(f"{len(missing)} clip(s) missing, first: {missing[0]}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    expected_ms = sum(probe(p).duration_ms for p in paths)

    # Both temps are dot-prefixed siblings of dst: same directory, hence same
    # filesystem, so the os.replace() below is atomic and can never straddle
    # a mount -- and a plain listing of reels/ does not turn them up. Same
    # discipline make_clip uses for a clip in flight.
    stamp = uuid.uuid4().hex
    listing = dst.with_name(f".{dst.stem}.{stamp}.concat.txt")
    tmp = dst.with_name(f".{dst.stem}.{stamp}.part{dst.suffix}")

    try:
        listing.write_text("".join(f"file '{_escape(p)}'\n" for p in paths))
        # -safe 0 because the listing carries absolute paths, which the
        # demuxer refuses by default.
        base = ["-f", "concat", "-safe", "0", "-i", str(listing)]

        differences = divergences(paths)
        if differences:
            for difference in differences:
                log.warning("reel input mismatch -- %s", difference)
            log.warning(
                "%d input mismatch(es) for %s; re-encoding rather than copying",
                len(differences), dst.name,
            )
            mode = "reencode"
        else:
            run_ffmpeg([*base, "-c", "copy", "-movflags", "+faststart", str(tmp)])
            actual_ms = probe(tmp).duration_ms
            mode = "copy"
            if abs(actual_ms - expected_ms) > tolerance_ms(len(paths)):
                log.warning(
                    "concat -c copy produced %dms from %d clips totalling %dms; "
                    "re-encoding %s",
                    actual_ms, len(paths), expected_ms, dst.name,
                )
                mode = "reencode"

        if mode == "reencode":
            run_ffmpeg(
                _reencode_args(base, tmp),
                on_progress=on_progress, total_ms=expected_ms,
            )

        os.replace(tmp, dst)
        return mode
    except BaseException:
        # Best-effort only, and explicitly suppressed: a permissions failure
        # cleaning up must never replace the TranscodeError already
        # propagating with an unrelated error about a temp file.
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    finally:
        with contextlib.suppress(OSError):
            listing.unlink(missing_ok=True)


# run_ffmpeg and TranscodeError are re-exported deliberately: tests
# monkeypatch `concat.run_ffmpeg`, which must be the name this module
# actually calls rather than the one in transcode.
__all__ = [
    "ClipParams",
    "ConcatError",
    "TranscodeError",
    "clip_params",
    "concat_clips",
    "divergences",
    "run_ffmpeg",
    "tolerance_ms",
]
