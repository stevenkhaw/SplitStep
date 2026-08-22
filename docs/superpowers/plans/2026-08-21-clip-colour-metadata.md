# Clip Colour Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pin `color_range` / `color_space` / `color_primaries` / `color_transfer` into the locked clip profile, and refuse to cut a source whose colour metadata does not match exactly.

**Architecture:** `probe()` grows four normalized colour fields on `MediaInfo`. `make_clip` gains four profile constants, passes them as explicit ffmpeg output flags, and raises `TranscodeError` before encoding when the probed source disagrees. No conversion in either direction — see the spec's §1.1 and §3 for why relabelling and tonemapping were both rejected.

**Tech Stack:** Python 3.12, pytest, ffmpeg/ffprobe 9.0.1, libx264.

**Spec:** `docs/superpowers/specs/2026-08-21-clip-colour-metadata-design.md`

## Global Constraints

- Python is the `bootleg` conda env and is **not** the shell default. Invoke by path.
- **In this worktree, run pytest as a module: `~/miniconda3/envs/bootleg/bin/python -m pytest`, never the bare `pytest` entry point.** The env editable-installs `bootleg` via a finder pinned to `/Users/stevenkhaw/Documents/GitHub/BootlegVision/bootleg` — the MAIN checkout, on `master`. The console script does not put the cwd on `sys.path`, so from here `import bootleg` silently resolves to master's code and every test in this plan passes or fails against the wrong package. `-m` prepends the cwd, which wins. CLAUDE.md documents the bare form because it is correct in the main checkout; it is wrong in a worktree.
- `~/miniconda3/envs/bootleg/bin/ruff check bootleg tests` for lint (ruff reads paths, not imports, so the bare entry point is fine).
- ruff line-length is **100**.
- `pytest` runs with `filterwarnings = ["error"]` — a new warning fails the suite.
- ffmpeg must be on PATH. YOLO is never run in tests.
- **Comments explain why, not what.** This codebase carries long rationale comments on non-obvious calls. Match that density; do not strip existing ones.
- The four pinned values are, in ffprobe's own spellings and its own field order:
  `color_range=tv`, `color_space=bt2020nc`, `color_transfer=arib-std-b67`, `color_primaries=bt2020`.
- **`feat/reels` is not merged.** Do not edit `bootleg/media/concat.py`; see Task 5's note.

## The two measured facts this plan is built on

Both were verified on ffmpeg 9.0.1 before writing this plan. Do not re-derive them; do not "simplify" the code that works around them.

**1. With a lavfi input, the `-color_*` output flags are silently partial.**

```
ffmpeg -f lavfi -i testsrc=... -c:v libx264 -pix_fmt yuv420p \
       -color_range tv -colorspace bt2020nc -color_primaries bt2020 -color_trc arib-std-b67 out.mp4
  ->  color_range=tv  color_space=bt2020nc  color_transfer=unknown  color_primaries=unknown
```

The matrix and range stick; primaries and transfer are dropped. A fixture built that way would claim to be HLG while carrying `unknown` in two of the four fields, and the tests would pass against a file `make_clip` would refuse in production. **Every synthetic source in this plan is therefore tagged with the `setparams` filter, never with the output flags.** With a real file as input the same flags do stick, which is why `make_clip` itself uses them.

**2. ffprobe spells "missing" two ways.** Its CSV writer prints the literal `unknown`; its JSON writer — which `probe()` reads — omits the key entirely. `_color_tag()` normalizes both to `None`.

---

### Task 1: `probe()` reads the four colour fields

**Files:**
- Modify: `bootleg/media/probe.py` (the `MediaInfo` dataclass, a new `_color_tag()` helper beside `_sar()`, and `probe()`'s return statement)
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `MediaInfo.color_range`, `.color_space`, `.color_primaries`, `.color_transfer`, each `str | None`. Task 3 and Task 4 read all four.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_probe.py`. It already has its own local `sample_video` fixture built from lavfi with no colour flags — that is the untagged case, so reuse it rather than building another.

```python
@pytest.fixture
def hlg_video(tmp_path):
    """A source tagged exactly as the locked clip profile is.

    `setparams` rather than the -color_* output flags: with a lavfi input
    those flags write the matrix and the range and silently drop primaries
    and transfer, so a fixture built with them would carry `unknown` in two
    of the four fields while looking correct in the command line.
    """
    out = tmp_path / "hlg.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
         "-vf", "setparams=color_primaries=bt2020:color_trc=arib-std-b67"
                ":colorspace=bt2020nc:range=tv",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    return out


def test_probe_reads_colour_metadata(hlg_video):
    info = probe(hlg_video)
    assert info.color_range == "tv"
    assert info.color_space == "bt2020nc"
    assert info.color_transfer == "arib-std-b67"
    assert info.color_primaries == "bt2020"


def test_probe_reads_absent_colour_metadata_as_none(sample_video):
    """The case that matters more than a mislabelled source, because it is
    far commoner: a file carrying no colour metadata at all. ffprobe's CSV
    writer prints the literal "unknown" for these and its JSON writer -- the
    one probe() reads -- omits the keys entirely. Both have to arrive as
    None, because make_clip's check is a plain equality test and None is
    what makes "no tags" fail it.
    """
    info = probe(sample_video)
    assert info.color_range is None
    assert info.color_space is None
    assert info.color_transfer is None
    assert info.color_primaries is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_probe.py -q -k colour
```

Expected: FAIL, `AttributeError: 'MediaInfo' object has no attribute 'color_range'`.

- [ ] **Step 3: Add the fields, the helper, and the reads**

In `bootleg/media/probe.py`, append four fields to `MediaInfo` (keep the existing trailing-comment style):

```python
    sar: float                          # sample (pixel) aspect ratio; 1.0 for square pixels
    color_range: str | None             # ffprobe's spelling, e.g. "tv"; None when absent
    color_space: str | None             # matrix coefficients, e.g. "bt2020nc"
    color_primaries: str | None         # e.g. "bt2020"
    color_transfer: str | None          # e.g. "arib-std-b67" (HLG)
```

Add the helper immediately after `_sar()`:

```python
def _color_tag(video: dict, key: str) -> str | None:
    """One of ffprobe's colour fields, with both spellings of "missing" as None.

    ffprobe has two: its CSV writer prints the literal string "unknown" for a
    stream carrying no colour metadata, and its JSON writer -- the one this
    module reads -- omits the key outright. A third case, a value ffprobe
    does not recognise, also arrives as "unknown".

    All three mean the same thing to `make_clip`: this source's colour is not
    the locked profile's, and a clip cut from it could not be concatenated
    with the ones already cut. Collapsing them here is what lets that check
    stay a plain equality test instead of a special-case ladder -- None
    matches no pinned string, so an untagged source refuses exactly as a
    mislabelled one does.
    """
    value = video.get(key)
    if not value or value == "unknown":
        return None
    return str(value)
```

Extend `probe()`'s return statement:

```python
        sar=_sar(video),
        color_range=_color_tag(video, "color_range"),
        color_space=_color_tag(video, "color_space"),
        color_primaries=_color_tag(video, "color_primaries"),
        color_transfer=_color_tag(video, "color_transfer"),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_probe.py -q
```

Expected: PASS, whole file.

- [ ] **Step 5: Commit**

```bash
git add bootleg/media/probe.py tests/test_probe.py
git commit -m "feat(probe): read the four colour fields, both spellings of missing as None"
```

---

### Task 2: Tag every synthetic source in the suite

Pure test-fixture work, landed **before** the refusal exists so the suite is green at every commit. After this task the fixtures resemble what the library actually holds; before it, every one of them is a source `make_clip` will refuse in Task 4.

**Files:**
- Modify: `tests/conftest.py` (new `hlg_setparams` fixture; retag `sample_video`)
- Modify: `tests/test_clips.py` (retag `source_4k` and five inline lavfi builds)
- Modify: `tests/test_handler_clip.py` (retag the proxy build at ~line 81)

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixture `hlg_setparams -> str`, the bare `setparams=...` filter string with no `-vf` prefix, so a caller can either pass it alone or comma-join it onto an existing chain. Tasks 3 and 4 request it.

- [ ] **Step 1: Add the shared fixture to `tests/conftest.py`**

A fixture rather than a module-level constant: nothing in this suite imports across test modules today, and a fixture needs no assumption about `sys.path`.

```python
@pytest.fixture
def hlg_setparams() -> str:
    """The `setparams` filter that tags a synthetic source as the locked profile.

    Every source fixture in this suite needs it, because `make_clip` refuses
    a source whose colour metadata is not the profile's -- and lavfi output
    carries none at all.

    It has to be a filter, not the -color_range/-colorspace/-color_primaries
    /-color_trc output flags. Measured on ffmpeg 9.0.1: with a lavfi input
    those flags write the matrix and the range and silently DROP primaries
    and transfer, yielding a file that reports `unknown` for two of the four
    fields. A fixture built that way would look right in the command line,
    pass a careless assertion, and misrepresent what make_clip sees. With a
    real file as input the flags do stick, which is why make_clip itself
    uses them and only the fixtures need this.

    Returned bare so a caller with an existing -vf can comma-join onto it.
    """
    return "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=tv"
```

- [ ] **Step 2: Retag `sample_video` in `tests/conftest.py`**

```python
@pytest.fixture
def sample_video(tmp_path, hlg_setparams):
    """2 second 320x240 30fps clip with a 440Hz tone, tagged as the locked profile."""
    out = tmp_path / "sample.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(out)],
        check=True, capture_output=True,
    )
    return out
```

- [ ] **Step 3: Retag `source_4k` in `tests/test_clips.py`, with a self-check**

The self-check follows the file's existing convention (`assert ... "fixture is not anamorphic"`) and exists because of the measured flag-dropping trap: a fixture that silently loses two fields must fail here, loudly, rather than in whatever test happens to use it.

```python
@pytest.fixture
def source_4k(tmp_path, hlg_setparams):
    """6 seconds of 4K30 with a tone, so a 2-second cut has room either side."""
    out = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=3840x2160:rate=30:duration=6",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(out)],
        check=True, capture_output=True,
    )
    assert _stream_field(out, "v:0", "color_range,color_space,color_transfer,color_primaries") == (
        "tv,bt2020nc,arib-std-b67,bt2020"
    ), "fixture did not come out tagged as the locked profile"
    return out
```

`_stream_field` is defined at line ~282 of the same file, below this fixture. Python resolves it at call time, so the forward reference is fine.

- [ ] **Step 4: Retag the five inline lavfi builds in `tests/test_clips.py`**

Each needs `hlg_setparams` added to its test signature. Two already have a `-vf`; comma-join onto it rather than adding a second `-vf`, which would silently override the first.

`test_make_clip_conforms_a_quarter_turn_to_the_locked_frame` — add `hlg_setparams` to the signature, then:

```python
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=2160x3840:rate=30:duration=3",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
```

`test_make_clip_upscales_and_pads_a_1080p_source` — same shape:

```python
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
```

`test_make_clip_pins_square_pixels` and `test_make_clip_conforms_an_anamorphic_source_without_stretching_it` — both already pass `-vf setsar=2/1`:

```python
         "-vf", f"setsar=2/1,{hlg_setparams}",
```

`test_make_clip_synthesizes_silence_for_a_source_with_no_audio`:

```python
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
```

`test_clips_from_mismatched_sources_concat_with_c_copy` builds two more sources (`anamorphic`, `silent`). Tag both — its whole point is that sources agreeing on nothing else still produce concatenatable clips, and after Task 4 colour is the one axis on which they must agree:

```python
         "-vf", f"setsar=2/1,{hlg_setparams}",   # the anamorphic one
```
```python
         "-vf", hlg_setparams,                   # the silent one
```

- [ ] **Step 5: Retag the proxy build in `tests/test_handler_clip.py`**

In `test_handle_clip_cuts_from_the_proxy_when_the_original_is_reclaimed`, add `hlg_setparams` to the signature and:

```python
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-vf", hlg_setparams,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(source.dir / "proxy.mp4")],
```

- [ ] **Step 6: Run the affected files**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_clips.py tests/test_handler_clip.py tests/test_export.py tests/test_orphans.py -q
```

Expected: PASS. Nothing has changed behaviourally yet — this step is confirming the retagged fixtures still encode and still exercise what they did before.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_clips.py tests/test_handler_clip.py
git commit -m "test(clips): tag every synthetic source as the locked colour profile"
```

---

### Task 3: Pin the four constants and write them explicitly

**Files:**
- Modify: `bootleg/media/transcode.py` (the locked-profile constant block, and `make_clip`'s `run_ffmpeg` argument list)
- Test: `tests/test_clips.py`

**Interfaces:**
- Consumes: `hlg_setparams` (Task 2).
- Produces: `CLIP_COLOR_RANGE`, `CLIP_COLOR_SPACE`, `CLIP_COLOR_PRIMARIES`, `CLIP_COLOR_TRC` — all `str`, importable from `bootleg.media.transcode`. Task 4 compares against them.

- [ ] **Step 1: Write the failing test**

Extend the existing literal-guard test in `tests/test_clips.py`. That test is the file's deliberate speed bump — the one place the profile is written as literals instead of re-derived from the module under test — so the colour values belong in it rather than in a new test that can be forgotten.

Add to the imports:

```python
from bootleg.media.transcode import (
    CLIP_COLOR_PRIMARIES,
    CLIP_COLOR_RANGE,
    CLIP_COLOR_SPACE,
    CLIP_COLOR_TRC,
    CLIP_CRF,
    CLIP_FPS,
    CLIP_HEIGHT,
    CLIP_WIDTH,
    TranscodeError,
    make_clip,
)
```

Extend the test body and its docstring's closing paragraph:

```python
    assert (CLIP_WIDTH, CLIP_HEIGHT, CLIP_FPS, CLIP_CRF) == (3840, 2160, 30, 20)
    # Colour is the fifth property -- see the colour-metadata spec. The 24
    # clips already on the drive carry exactly these, inherited by accident
    # from a 10-bit HLG iPhone source; pinning them is what makes the
    # agreement deliberate. Changing any of them breaks -c copy against
    # every one of those clips.
    assert (CLIP_COLOR_RANGE, CLIP_COLOR_SPACE, CLIP_COLOR_TRC, CLIP_COLOR_PRIMARIES) == (
        "tv", "bt2020nc", "arib-std-b67", "bt2020"
    )
```

And add the regression test for the encoded output:

```python
def test_make_clip_writes_the_locked_colour_metadata(source_4k, tmp_path):
    """Colour is the fifth property `-c copy` assumes every clip shares, after
    the frame, the rate, the sample aspect and the audio track.

    This assertion holds today even with the flags removed, because ffmpeg
    copies an input's colour properties forward and every source that gets
    this far is already tagged as the profile (make_clip refuses the ones
    that are not). That is exactly why it is worth pinning AND asserting:
    the agreement is currently an accident of ffmpeg's defaults, and this
    test is what notices if a future version stops propagating them.
    """
    dst = tmp_path / "clip.mp4"
    make_clip(source_4k, dst, start_ms=1000, end_ms=3000)
    assert _stream_field(
        dst, "v:0", "color_range,color_space,color_transfer,color_primaries"
    ) == f"{CLIP_COLOR_RANGE},{CLIP_COLOR_SPACE},{CLIP_COLOR_TRC},{CLIP_COLOR_PRIMARIES}"
```

**Deviation from spec §7.4, deliberate.** The spec put the four colour
assertions inside `test_make_clip_hits_the_locked_profile`. They go in two
places here instead: the literal-guard test above, and this one. The
literal-guard test is the stronger of the two -- it compares against
written-out literals, so it fails when someone edits the constants, which is
the change that actually breaks concat. `test_make_clip_hits_the_locked_profile`
reads its expectations from the module under test and would move silently with
them. The spec's intent -- that colour drift fails alongside frame and rate
drift rather than in a test someone forgets -- is met by both.

- [ ] **Step 2: Run to verify it fails**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_clips.py -q -k "colour or always_been"
```

Expected: FAIL at collection, `ImportError: cannot import name 'CLIP_COLOR_PRIMARIES' from 'bootleg.media.transcode'`.

- [ ] **Step 3: Add the constants**

In `bootleg/media/transcode.py`, inside the existing locked-profile block, after `CLIP_CRF = 20`:

```python
CLIP_CRF = 20

# Colour is the fifth property of a source the profile relied on and never
# stated -- after the frame, the rate, the sample aspect and the audio track.
# `-pix_fmt yuv420p` was pinned and the colour tags were not, so every clip
# inherited them from whatever it was cut from. It happens to agree today:
# all 24 clips on the drive, and the proxies, are tv/bt2020nc/arib-std-b67
# /bt2020, inherited from a 10-bit HLG iPhone source. That agreement was an
# accident, and the first source that disagreed would have broken it
# silently -- the concat demuxer reads every input through the FIRST clip's
# parameters, so a mixed reel gets wrong colour on part of its footage with
# no error anywhere.
#
# Pinned to what the library already contains rather than converted to
# anything: this ffmpeg has neither libzimg nor libplacebo, so no correct
# tonemap exists here in either direction, and writing bt709 tags onto HLG
# pixels would make the file look right while being wrong. A source that
# does not match is refused instead -- see _require_locked_color.
CLIP_COLOR_RANGE = "tv"
CLIP_COLOR_SPACE = "bt2020nc"
CLIP_COLOR_PRIMARIES = "bt2020"
CLIP_COLOR_TRC = "arib-std-b67"
```

- [ ] **Step 4: Write the flags in `make_clip`**

In `make_clip`'s `run_ffmpeg([...])` list, directly after `"-pix_fmt", "yuv420p",`:

```python
            "-pix_fmt", "yuv420p",
            # Written explicitly rather than left to ffmpeg's copy-forward of
            # the input's properties, which is all that has ever put them
            # there. These are output flags rather than a `setparams` in the
            # filter chain because the input is always a real file here, and
            # measured on ffmpeg 9.0.1 the flags stick for a file input --
            # they do NOT for a lavfi one, where primaries and transfer are
            # silently dropped, which is why the test fixtures tag with
            # setparams instead.
            "-color_range", CLIP_COLOR_RANGE,
            "-colorspace", CLIP_COLOR_SPACE,
            "-color_primaries", CLIP_COLOR_PRIMARIES,
            "-color_trc", CLIP_COLOR_TRC,
```

- [ ] **Step 5: Run to verify it passes**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_clips.py -q
```

Expected: PASS, whole file.

- [ ] **Step 6: Commit**

```bash
git add bootleg/media/transcode.py tests/test_clips.py
git commit -m "feat(clips): pin the locked profile's colour metadata explicitly"
```

---

### Task 4: Refuse a source whose colour is not the profile's

**Files:**
- Modify: `bootleg/media/transcode.py` (new `_require_locked_color()`, called from `make_clip`)
- Test: `tests/test_clips.py`

**Interfaces:**
- Consumes: `MediaInfo.color_*` (Task 1), the four constants (Task 3), `hlg_setparams` (Task 2).
- Produces: `make_clip` raises `TranscodeError` on a colour mismatch, before any temp file is created.

- [ ] **Step 1: Write the failing tests**

```python
def test_make_clip_refuses_an_sdr_source(tmp_path, hlg_setparams):
    """The whole point of the pin. An SDR source cut into this library would
    produce a clip differing from the 24 already on the drive in colour tags
    alone -- and the concat demuxer does not refuse that, it reads every
    input through the first clip's parameters and renders part of the reel
    with the wrong colour, silently.

    Refused rather than converted: this ffmpeg has no libzimg and no
    libplacebo, so there is no correct tonemap available in either
    direction, and tagging bt709 pixels as HLG (or the reverse) makes the
    file look correct while being wrong. A refusal is instant and
    recoverable; a mislabelled clip is permanent and costs a re-cut.
    """
    src = tmp_path / "sdr.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    assert _stream_field(src, "v:0", "color_space") == "bt709", "fixture is not SDR"

    dst = tmp_path / "clip.mp4"
    with pytest.raises(TranscodeError, match="colour"):
        make_clip(src, dst, start_ms=0, end_ms=2000)
    assert not dst.exists()
    # The check must precede the encode, so nothing was ever written: no
    # finished clip, and no in-flight .part sibling either.
    assert [p for p in tmp_path.iterdir() if ".part." in p.name] == []


def test_make_clip_refuses_an_untagged_source(tmp_path):
    """The likelier case in practice than a mislabelled one: a file carrying
    no colour metadata at all -- a screen recording, a re-mux, anything from
    a camera that does not tag. Untagged pixels are not HLG pixels, so
    writing the profile's tags onto them would be precisely the
    relabel-without-converting this design exists to avoid.

    Deliberately no hlg_setparams here: lavfi output carries no colour
    metadata unless something puts it there.
    """
    src = tmp_path / "untagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    with pytest.raises(TranscodeError, match="colour"):
        make_clip(src, dst, start_ms=0, end_ms=2000)
    assert not dst.exists()


def test_make_clip_refusal_names_both_sets_of_tags(tmp_path):
    """The message is the whole remediation path: there is no override flag,
    so it has to say what it saw, what it wanted, that it did not convert,
    and -- for the one cause that will realistically occur -- where the
    setting is."""
    src = tmp_path / "untagged.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=30:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True,
    )
    with pytest.raises(TranscodeError) as exc:
        make_clip(src, tmp_path / "clip.mp4", start_ms=0, end_ms=2000)
    message = str(exc.value)
    assert "untagged.mp4" in message
    assert "unset" in message                 # how a None field is rendered
    assert "arib-std-b67" in message          # what the profile wanted
    assert "No conversion was attempted" in message
    assert "HDR Video" in message             # the iPhone setting
```

- [ ] **Step 2: Run to verify they fail**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_clips.py -q -k "refuse or refusal"
```

Expected: FAIL — `DID NOT RAISE <class 'TranscodeError'>` on all three, because `make_clip` currently cuts these happily.

- [ ] **Step 3: Add the check**

In `bootleg/media/transcode.py`, add above `make_clip`:

```python
def _require_locked_color(info: MediaInfo, src: Path) -> None:
    """Refuse a source whose colour metadata is not the locked profile's.

    Strict equality on all four fields, and `None` -- an untagged source --
    fails it exactly as a bt709 one does. That is deliberate: untagged pixels
    are not HLG pixels, so applying the profile's tags to them would be a
    relabel without a conversion, which produces a file that looks correct
    while being wrong. Harder to find later than an honest mismatch.

    Refused rather than converted because this ffmpeg cannot convert:
    measured on 9.0.1 with neither libzimg nor libplacebo, `zscale` is absent
    so no linear-light stage exists to feed `tonemap`, and the built-in
    `colorspace` filter's transfer list contains no arib-std-b67 at all --
    it takes HLG neither in nor out. Both directions are blocked, not just
    the one.

    There is deliberately no override. An override is a way to write a
    permanently wrong clip, and the clip is the artifact that has to stay
    concat-compatible for years; the reviewer is the part that can be
    corrected later.
    """
    actual = (info.color_range, info.color_space, info.color_transfer, info.color_primaries)
    wanted = (CLIP_COLOR_RANGE, CLIP_COLOR_SPACE, CLIP_COLOR_TRC, CLIP_COLOR_PRIMARIES)
    if actual == wanted:
        return

    shown = tuple(field or "unset" for field in actual)
    raise TranscodeError(
        f"{src.name} does not carry the locked profile's colour metadata, so a clip "
        f"cut from it could not be concatenated with the ones already cut.\n"
        f"  source:  range={shown[0]} space={shown[1]} transfer={shown[2]} primaries={shown[3]}\n"
        f"  profile: range={wanted[0]} space={wanted[1]} transfer={wanted[2]} "
        f"primaries={wanted[3]}\n"
        f"No conversion was attempted: relabelling one as the other makes the file look "
        f"correct while being wrong, and this ffmpeg has no working tonemap in either "
        f"direction. If this came from the usual iPhone, check Settings > Camera > "
        f"Record Video -- HDR Video turned off records bt709 SDR."
    )
```

Change the existing import at the top of `transcode.py` from
`from bootleg.media.probe import probe` to
`from bootleg.media.probe import MediaInfo, probe`.

Call it in `make_clip`, directly after the existing `info = probe(src)`:

```python
    info = probe(src)

    # Before anything is written, and before the minutes of encoding: colour
    # is the one profile property that cannot be conformed here, only
    # checked. See _require_locked_color.
    _require_locked_color(info, src)
```

- [ ] **Step 4: Run to verify they pass**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest tests/test_clips.py -q
```

Expected: PASS, whole file — including the existing tests, which Task 2 already tagged.

- [ ] **Step 5: Commit**

```bash
git add bootleg/media/transcode.py tests/test_clips.py
git commit -m "feat(clips): refuse a source whose colour is not the locked profile's"
```

---

### Task 5: Full suite, lint, and the convention note

**Files:**
- Modify: `CLAUDE.md` (the "Conventions that matter" list)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing further depends on this.

- [ ] **Step 1: Run the whole suite**

```bash
~/miniconda3/envs/bootleg/bin/python -m pytest -q
```

Expected: PASS. The count was 471 before this plan; it should be 471 plus the six tests added here (two in Task 1, one in Task 3, three in Task 4). If anything outside `tests/test_clips.py`, `tests/test_probe.py`, `tests/test_handler_clip.py` fails, it is a source fixture Task 2 missed — find it with `grep -rn "lavfi" tests/` and tag it the same way rather than loosening the check.

- [ ] **Step 2: Lint**

```bash
~/miniconda3/envs/bootleg/bin/ruff check bootleg tests
```

Expected: `All checks passed!`. Line-length is 100; the long f-string in `_require_locked_color` is already split to fit.

- [ ] **Step 3: Add the convention note to `CLAUDE.md`**

In "Conventions that matter", after the `rotation_filter()` bullet:

```markdown
- **The clip profile pins colour, and `make_clip` refuses a source that
  disagrees.** `tv / bt2020nc / arib-std-b67 / bt2020` — HLG, what an iPhone
  records and what all existing clips carry. Strict equality, untagged
  included, and no override: this ffmpeg has neither libzimg nor libplacebo,
  so there is no correct tonemap in either direction and a relabel would make
  a file look right while being wrong. Synthetic test sources must be tagged
  with the `hlg_setparams` fixture — with a lavfi input the `-color_*` output
  flags silently drop primaries and transfer.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note the clip profile's colour pin and the lavfi tagging trap"
```

---

## Not in this plan

- **`make_proxy` is untouched.** It has the same unpinned property, but a proxy never concatenates with anything, and the one path where a proxy reaches a clip (`has_original = 0`) is already consistent because the proxy inherits the source's tags.
- **No pre-flight in `plan_export`.** Spec §6.3 — it would put an ffprobe per source into a request handler to pre-empt a failure that has not happened once, and `make_clip` is the chokepoint every caller passes through anyway.
- **`bootleg/media/concat.py` is not edited.** It exists only on the unmerged `feat/reels` branch, where `ClipParams` already carries these four fields and a comment says `MediaInfo` "deliberately does not carry" stream-level parameters. That sentence is false once this lands. Whoever merges resolves it — spec §8. Textually the conflict is nil: `feat/reels` touched `probe()`'s body, not the `MediaInfo` dataclass and not its return statement.
- **The 24 existing clips are not re-cut.** The pinned value is what they already carry.
