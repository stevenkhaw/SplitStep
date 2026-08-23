import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, fields
from pathlib import Path

from splitstep.media.probe import color_tag, ffprobe_json, probe

# Tests monkeypatch splitstep.media.concat.run_ffmpeg -- the name concat_clips
# actually calls -- so it has to live in this module's namespace, not just
# transcode's. TranscodeError rides along on the same import for tests that
# want concat_mod.TranscodeError rather than reaching back into transcode.
from splitstep.media.transcode import (
    CLIP_CRF,
    CLIP_FPS,
    ProgressFn,
    TranscodeError,  # noqa: F401 -- re-exported for tests, see comment above
    run_ffmpeg,
)

log = logging.getLogger(__name__)


class ConcatError(Exception):
    """A reel could not be concatenated."""


# Measured drift on well-formed -c copy output is a constant +21ms,
# independent of input count and of clip-duration variation (verified at
# n=3, 8, 16, 24, both with uniform 1.0s clips and with clips varied
# 1.5-4.0s). A per-input term is kept anyway: that 21ms is measured on one
# ffmpeg build (9.0.1), and a build that genuinely does drift per input
# would need it, not just a flat pad. The cap exists because the per-input
# term alone made tolerance_ms(n) >= 1500ms from n=35, which is long enough
# to hide a dropped clip.
_TOLERANCE_BASE_MS = 100
_TOLERANCE_PER_INPUT_MS = 40
# Fallback cap when tolerance_ms is called with no reel-specific durations
# (existing callers, existing tests). NOT anchored to
# splitstep/detect/segment.py's min_duration_s: that floor is the segmenter's
# own, and nothing else in the app enforces it on a span a reel can hold --
# web's MIN_RALLY_MS is 100ms, and the bounds route validates only
# end_ms > start_ms. A hand-trimmed clip well under 1.5s is a real reel
# input the segmenter's floor says nothing about, so a cap justified by it
# would let a copy that dropped exactly that clip pass. See the
# `shortest_ms` argument below for the real check.
_TOLERANCE_CAP_MS = 750


def tolerance_ms(n_inputs: int, shortest_ms: int | None = None) -> int:
    """How far the concatenated duration may sit from the sum of its inputs.

    Deliberately generous, and capped: the failure this catches is a reel
    quietly missing its last four points -- tens of seconds -- not a
    rounding remainder, so a tight bound would only buy false re-encodes on
    well-formed output. Being wrong the other way is cheap: the fallback
    costs a re-encode, and it is logged.

    `shortest_ms`, when given, is THIS reel's own shortest input duration --
    concat_clips has already probed every input by the time it calls this,
    so passing it in costs nothing further. The cap is halved against it: a
    copy that silently dropped the shortest clip outright must land outside
    the window whatever that clip's own duration is, not merely outside a
    module constant borrowed from a different validator that this app does
    not otherwise enforce. Omitted, the cap falls back to `_TOLERANCE_CAP_MS`
    for callers with no reel in hand (this module's own tests, mainly).
    """
    cap = _TOLERANCE_CAP_MS
    if shortest_ms is not None:
        cap = min(cap, shortest_ms // 2)
    return min(
        _TOLERANCE_BASE_MS + _TOLERANCE_PER_INPUT_MS * max(0, n_inputs),
        cap,
    )


@dataclass(frozen=True)
class ClipParams:
    """The stream parameters `-c copy` silently assumes every input shares.

    This exists because ffmpeg does NOT check them. The design was written
    assuming the concat demuxer refuses streams whose codec parameters
    differ; measured on ffmpeg 9.0.1 it does not. It exits 0 with empty
    stderr and reads every input through the FIRST clip's parameters, so a
    mismatched sample aspect plays that clip at the wrong shape for its
    whole duration, a clip tagged with a different colour matrix decodes
    through the first clip's matrix instead for its whole duration, and an
    input carrying no audio stream contributes no audio -- the reel's track
    stops early while the picture runs on.

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
    # Absent tag preserved as None rather than guessed at -- deliberately
    # UNLIKE sample_aspect_ratio's "1:1" default below. An untagged clip
    # therefore diverges from a bt709-tagged one and takes the re-encode
    # path even though it might really match. That is the intended trade:
    # a spurious re-encode is a slower CORRECT reel, a missed mismatch is a
    # fast WRONG one, and the wrongness stays invisible until someone
    # watches the reel. The real library's clips are HLG HDR
    # (color_range=tv, color_space=bt2020nc, color_primaries=bt2020,
    # color_transfer=arib-std-b67), and make_clip now pins exactly those and
    # refuses a source carrying anything else -- so for clips this app cut
    # these four can no longer diverge. They stay compared because a clip is
    # a file on a disk: one cut before the pin landed, or dropped into
    # clips/ from outside, still reaches concat.
    #
    # Read through probe.color_tag, NOT video.get, so "unknown" and an absent
    # key both arrive as None. Raw reads would make two identically untagged
    # clips compare unequal and cost a needless re-encode.
    color_range: str | None
    color_space: str | None
    color_primaries: str | None
    color_transfer: str | None
    audio_codec: str | None
    audio_sample_rate: str | None
    audio_channels: int | None


def _parse_clip_params(data: dict, path: Path) -> ClipParams:
    """Build a `ClipParams` from an ffprobe document already in hand.

    Split out of `clip_params` so `_probe_clip` can parse both the
    comparison fields and the duration out of one ffprobe call instead of
    two -- see `_probe_clip`'s docstring for the measurement.
    """
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
        # Measured: ffprobe reports "1:1" explicitly for square-pixel clips,
        # both before and after make_clip pinned SAR -- it omits the field
        # only for UNDEFINED SAR (setsar=0/1). So this default conflates
        # "undefined" with "square", which is benign: every player already
        # treats undefined SAR as square, so the concat is correct either
        # way, and diverging over an absent tag would send an untouched
        # library down the re-encode path for nothing.
        sample_aspect_ratio=video.get("sample_aspect_ratio", "1:1"),
        pix_fmt=video.get("pix_fmt", ""),
        # r_frame_rate as ffprobe's raw string ("30/1"). Kept exact rather
        # than floated: mismatched rates break -c copy, and 30/1 against
        # 30000/1001 is precisely the difference a float comparison at any
        # tolerance would blur away.
        frame_rate=video.get("r_frame_rate", ""),
        # None means "ffprobe didn't tag it" and stays None -- see the field
        # comment on ClipParams for why guessing here would be the wrong
        # direction to be wrong.
        color_range=color_tag(video, "color_range"),
        color_space=color_tag(video, "color_space"),
        color_primaries=color_tag(video, "color_primaries"),
        color_transfer=color_tag(video, "color_transfer"),
        audio_codec=None if audio is None else audio.get("codec_name"),
        audio_sample_rate=None if audio is None else audio.get("sample_rate"),
        audio_channels=None if audio is None else int(audio["channels"]),
    )


def clip_params(path: Path) -> ClipParams:
    """Read the parameters `-c copy` cares about out of one clip."""
    return _parse_clip_params(ffprobe_json(path), path)


def _duration_ms(data: dict, path: Path) -> int:
    """Duration in ms from an ffprobe document already in hand.

    Mirrors probe.py's own MediaInfo.duration_ms derivation (format
    duration, falling back to the video stream's) so this stays identical
    to what a second `probe()` call on the same file would have produced --
    `_probe_clip` calls this instead of `probe()` precisely to avoid that
    second call.
    """
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    duration_s = float(fmt.get("duration") or (video or {}).get("duration") or 0.0)
    if duration_s <= 0:
        raise ConcatError(f"Could not determine duration for {path}")
    return round(duration_s * 1000)


def _probe_clip(path: Path) -> tuple[ClipParams, int]:
    """One ffprobe call, parsed for both the pre-flight check and the duration sum.

    concat_clips used to probe every clip twice -- once via `probe()` for
    `expected_ms`, once via `clip_params()` for the divergence check --
    reading the same `-show_format -show_streams` document both times.
    Measured at n=24: 610ms + 606ms of a 1.29s total, next to an ~80ms
    `-c copy` itself. Harmless on an SSD, but this library lives on an
    external drive, where it is doubled spin-up wait rather than free
    latency.
    """
    data = ffprobe_json(path)
    return _parse_clip_params(data, path), _duration_ms(data, path)


def divergences(paths: list[Path], params: list[ClipParams] | None = None) -> list[str]:
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

    `params`, when given, must already be `clip_params(p)` for each `p` in
    `paths`, in the same order. `concat_clips` passes its own
    already-probed results through here so this does not re-probe every
    clip a second time (see `_probe_clip`); every other caller -- and every
    existing test -- leaves it None and gets the original one-probe-per-path
    behaviour.
    """
    if len(paths) < 2:
        return []
    parsed = params if params is not None else [clip_params(p) for p in paths]
    first = parsed[0]
    reported: list[str] = []
    for path, current in zip(paths[1:], parsed[1:]):
        if current == first:
            continue
        for field in fields(ClipParams):
            mine = getattr(current, field.name)
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
    path on an external drive. Measured: this handles a literal quote and a
    literal backslash. It does NOT handle a newline in a directory name --
    that breaks the line-oriented concat listing and ffmpeg refuses the
    whole file (TranscodeError, exit 183). That is the acceptable failure
    direction: loud, not silent.
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
       SUCCEED and yield a wrong-shaped, wrong-matrix, or audio-truncated
       reel with a perfectly correct duration.
    2. AFTER: the output's duration is measured against the sum of the
       inputs, whichever branch produced it. Both `-c copy` AND the
       re-encode fallback run through the same concat demuxer, which can
       drop a later input outright -- and the re-encode runs it on inputs
       the pre-flight has already declared abnormal, so it is not obviously
       safer. A reel that still comes out wrong after the fallback raises
       `ConcatError` rather than reaching `mark_rendered`: a reel recorded
       as "rendered" has to actually contain what it claims to.

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
    # One ffprobe pass per input, reused for both the duration sum below and
    # the divergence check further down -- see _probe_clip.
    probed = [_probe_clip(p) for p in paths]
    expected_ms = sum(duration for _, duration in probed)
    # This reel's own shortest input, not the segmenter's floor -- see
    # tolerance_ms's docstring for why anchoring to segment.py's
    # min_duration_s would miss a hand-trimmed clip shorter than it.
    tolerance = tolerance_ms(len(paths), min(duration for _, duration in probed))

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

        differences = divergences(paths, [params for params, _ in probed])
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
            if abs(actual_ms - expected_ms) > tolerance:
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
            # The fallback runs through the SAME concat demuxer as -c copy,
            # on inputs the pre-flight has already flagged as abnormal --
            # _reencode_args' own docstring concedes the demuxer imposes the
            # first input's frame on the rest either way. Nothing measured
            # this branch's own output before, so a fallback that ALSO
            # dropped a later input still reached mark_rendered and the UI
            # read "rendered" for a reel silently missing its last points.
            actual_ms = probe(tmp).duration_ms
            if abs(actual_ms - expected_ms) > tolerance:
                raise ConcatError(
                    f"re-encoded {dst.name} came out {actual_ms}ms from {len(paths)} "
                    f"clip(s) totalling {expected_ms}ms -- refusing to mark it rendered"
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
