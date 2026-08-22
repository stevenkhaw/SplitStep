# BootlegVision — Colour Metadata in the Locked Clip Profile

**Date:** 2026-08-21
**Status:** Approved, ready for implementation
**Extends:** `docs/superpowers/specs/2026-08-21-clip-export-and-reels-design.md` §4.2
**Adds a fifth unpinned property to the three that section already records** —
SAR, audio presence, and now colour.

---

## 1. Problem

`make_clip` pins `-pix_fmt yuv420p` and no colour metadata, so a clip inherits
`color_range` / `color_space` / `color_primaries` / `color_transfer` from
whatever it was cut from. Nothing in the locked profile mentions them.

Measured on the real library, every artifact currently agrees by accident:

```
sessions/2026-08-18/sources/01/original.mov   hevc Main 10, hvc1, yuv420p10le
                                              tv / bt2020nc / bt2020 / arib-std-b67
sessions/2026-08-18/sources/01/proxy.mp4      h264, tv / bt2020nc / bt2020 / arib-std-b67
sessions/2026-08-18/clips/*.mp4  (all 24)     tv / bt2020nc / bt2020 / arib-std-b67
```

HLG HDR throughout, with no Dolby Vision side data — a plain 10-bit HLG HEVC
stream, which is what an iPhone records with HDR Video on. The proxy inherits
it too, so the `has_original = 0` path (cut from `proxy.mp4`) lands in the same
place as the ordinary one. There is no inconsistency in the library today.

The inconsistency is future, and it is the same shape as the two §4.2 already
had to go back and fix. The locked profile exists so that every clip stays
`-c copy` concat-compatible with every other clip, permanently — that is the
stated reason for software libx264 over hardware encoders, for the SAR
conforming, and for the synthesized silent audio track. Colour metadata is the
remaining property the profile relies on and never states, so nothing conforms
it. The first source that disagrees produces clips differing from the existing
24 in colour tags alone.

**And it breaks quietly, in the way §4.2 documents.** The concat demuxer does
not reject mismatched inputs: on ffmpeg 9.0.1 it exits 0 with empty stderr and
reads every input through the *first* clip's parameters. A mixed reel gets
silently wrong colour across part of its footage.

The `feat/reels` branch adds a pre-flight parameter comparison
(`concat.ClipParams`, `divergences()`) that already carries these four fields
and falls back to a re-encode when they diverge. That guard is complementary
and stays. It is not a fix: its fallback still feeds the concat demuxer
(`-f concat -i listing`), which imposes the first input's stream parameters, so
a mixed-colour reel becomes a slow re-encode that is still colour-wrong. The
guard converts silently-wrong into logged-and-wrong. The fix has to happen at
cut time, which is where every other conforming decision already lives.

### 1.1 What this spec explicitly does not do

**It does not add `-colorspace bt709 -color_primaries bt709 -color_trc bt709`.**
That relabels HLG pixels as SDR without converting them. The file then looks
correct while being wrong, which is worse than the present inconsistency: an
honest mismatch can be found later, a confident wrong label cannot.

---

## 2. Constraint discovered before choosing: this ffmpeg cannot tonemap

Measured on the Mac, and it eliminates two of the three candidate designs:

```
ffmpeg 9.0.1, Homebrew. Configuration carries no --enable-libzimg and no
--enable-libplacebo.

  zscale     absent  (needs libzimg)
  libplacebo absent
  tonemap    present, but it requires linear-light input that only zscale
             can produce here
  colorspace present, and its trc list contains no arib-std-b67 — it can
             take HLG neither in nor out
```

So there is no correct HDR→SDR conversion available on this machine, and none
for SDR→HDR either, since the inverse needs the same missing filters. Any
design built on tonemapping requires rebuilding ffmpeg first — on **both**
machines, because §4.2's whole argument for software libx264 is that a clip cut
on the Mac and one cut on the 4070Ti must be byte-compatible. A profile that
depends on filter availability spanning two independently-installed ffmpeg
builds is a weaker guarantee than the one it is trying to protect.

---

## 3. Decision

**Pin the profile to what the library actually contains, and refuse a source
that disagrees.** No conversion in either direction, ever, in this design.

Rejected alternatives:

- **Tonemap everything to bt709 and pin SDR.** Correct, universal output, but
  it needs the ffmpeg rebuild described in §2, it is lossy, it invalidates all
  24 existing clips at roughly 30 minutes per 24-point export (measured, §4.2),
  and it discards HDR on a delivery path where HDR works. The reviewer watches
  and shares these on Apple devices — iMessage and an iCloud Shared Album to a
  friend — and HLG is handled correctly end to end on that path.
- **Record the colour parameters into the library's own locked profile on
  first export.** Same refusal behaviour, but the pinned value becomes
  per-library data: a migration, a recovery path for a wrong first export, and
  two libraries free to diverge on a property this spec's parent treats as
  globally fixed. Every other profile property is a code constant. One library
  exists.

The chosen design leaves the tonemapping option reachable: if an SDR source
ever appears and ffmpeg is rebuilt with libzimg or libplacebo, the refusal
point is exactly where a conversion slots in.

---

## 4. The profile gains four constants

In `bootleg/media/transcode.py`, beside `CLIP_WIDTH` / `CLIP_HEIGHT` /
`CLIP_FPS` / `CLIP_CRF`, under the same "CHANGING ANY OF THESE BREAKS
`-c copy` AGAINST EVERY CLIP EVER CUT" banner:

```python
CLIP_COLOR_RANGE     = "tv"
CLIP_COLOR_SPACE     = "bt2020nc"
CLIP_COLOR_PRIMARIES = "bt2020"
CLIP_COLOR_TRC       = "arib-std-b67"
```

`make_clip` passes them explicitly:

```
-color_range tv -colorspace bt2020nc -color_primaries bt2020 -color_trc arib-std-b67
```

libx264 writes these into the SPS VUI. For an HLG source this changes no pixel
and no tag — the value pinned is the value the 24 existing clips already carry,
which is the whole reason none of them need re-cutting. What changes is that
the agreement stops being an accident of ffmpeg copying input properties
forward and becomes a stated property of the profile, enforced.

**The 8-bit question is pre-existing and unchanged here.** The source is
`yuv420p10le` and the profile pins `yuv420p`, so clips are already 8-bit HLG,
and have been since the first one was cut. HLG is nominally a 10-bit format;
this spec neither introduces that nor fixes it. Read the HLG pin as a
description of the existing 24 clips, not as a new claim about them.

`make_proxy` is deliberately out of scope. It has the same unpinned property,
but a proxy is a disposable intermediate that never concatenates with anything,
and the one path where a proxy reaches a clip (`has_original = 0`) is already
consistent because the proxy inherits the source's tags.

---

## 5. The match rule: strict equality, unknown included

A source is cut only if all four probed values equal the four pinned constants
exactly. Anything else raises.

**Absent tags refuse too, and that is the case that matters.** ffprobe reports
missing colour metadata two ways — the key omitted, or present as the literal
string `"unknown"` — and plenty of files carry none at all. Untagged pixels are
not HLG pixels, so writing our four tags onto them is precisely the
relabel-without-converting that §1.1 rules out. Treating unknown as "probably
fine" reintroduces the original bug wearing a checkmark.

The cost is real and accepted: a file that is genuinely bt709 but simply
unlabelled is refused with no way to assert otherwise. **There is no override
flag.** An override is a way to write a permanently wrong clip, and the clip is
the artifact that has to stay valid for years — the reviewer, not the file, is
the thing that can be corrected later.

### 5.1 What will actually trigger this

Filming is iPhone-only for now, so every source is HLG and the refusal path may
never fire. When it does fire, the likeliest cause is not a second camera: it
is **Settings → Camera → Record Video → HDR Video turned off** on the same
phone, which makes it record bt709 SDR. "Most Compatible" has the same effect
on the codec side. The error message says so, because in the one case that will
realistically occur, that toggle is the fix.

Strict equality also catches the less likely drift — an Apple change from HLG
to PQ (`smpte2084`), a re-muxed file that lost its tags — without needing to
anticipate any of them by name.

---

## 6. Where the check lives

### 6.1 `probe()` learns the four fields

`MediaInfo` gains:

```python
color_range: str | None
color_space: str | None
color_primaries: str | None
color_transfer: str | None
```

A `_color_tag()` helper sits beside the existing `_sar()` and `_pick_fps()`,
in the same spirit: both spellings of missing — key absent, and the literal
`"unknown"` — normalize to `None`. That single normalization is what makes §5's
strict equality mean what it says, since `None` matches no pinned string.

No second subprocess. `make_clip` already probes the source for SAR and audio
presence; this reads more of the document it already has.

Two spellings are in play and both are correct where they appear. ffprobe's
stream key is `color_transfer`, so that is the `MediaInfo` field name; ffmpeg's
output flag is `-color_trc`, so that is the constant's name. Comparison is
exact string equality against ffprobe's own spellings — the values in §4 are
literally what ffprobe emits for the existing sources, not normalized aliases.

### 6.2 `make_clip` raises before it encodes

The check goes immediately after the `duration_ms <= 0` guard and before the
filter chain is built, so it fires before any temp file exists and before an
encode starts.

It raises `TranscodeError`, deliberately rather than `ValueError`:
`api/routes.py` already catches `(TranscodeError, ProbeError)` and turns it
into a client error instead of a bare 500, and the worker's `except Exception`
marks the job failed either way. The message names the file, its four actual
values, the four pinned ones, that no conversion was attempted, and §5.1's
iPhone setting.

### 6.3 No pre-flight in `plan_export`

The check lives only in `make_clip`.

A mismatched source with 24 exportable rallies produces 24 failed jobs carrying
the same message. That reads worse than it is: the check precedes the encode,
so each fails in the time of one ffprobe and the whole batch fails in seconds,
against the half hour a real 24-point export takes. Twenty-four identical
failures that each print the file's tags against the profile's is a legible
signal.

`plan_export` was the alternative — it has an `unavailable` bin and the
precedent that one broken row must not block exporting the rest — and is
rejected for this pass. That bin currently means "the source row vanished":
permanent, and free to detect. Colour mismatch is a media fact costing an
ffprobe per distinct source, and `plan_export` does no media I/O today at all,
only DB reads and `.exists()`. It also runs inside a route, where `probe()`'s
own docstring warns that a wedged ffprobe against a spun-down external drive
ties up one of Starlette's shared worker threads — so it would need a short
timeout and a new `ProbeError` fallback path. That is machinery bought to
pre-empt a failure that has not happened once.

`make_clip` is additionally the single chokepoint: CLI export, the API route,
and anything added later all pass through it. A guard there cannot be routed
around; a guard in `plan_export` can.

If an SDR source appears and 24 identical failures prove annoying in practice,
promoting the check into `plan_export` is a small follow-up with its failure
mode already understood.

---

## 7. Tests

All synthetic `lavfi` sources with explicit colour flags — the check is on
tags, so no real footage and no new files in `tests/fixtures/` are needed. This
matches how the parent spec already tests the encode profile.

1. **The pin.** `make_clip` on an HLG-tagged synthetic source yields a clip
   whose four colour values are exactly the four constants.
2. **SDR refuses.** A `bt709`-tagged source raises `TranscodeError`; the
   message carries both tag sets; `dst` does not exist and no `.part` temp is
   left in the directory — the existing cleanup path, now reached by a new
   route into it.
3. **Untagged refuses.** Same, for a source carrying no colour metadata. If a
   fully untagged libx264 output turns out not to be constructible from lavfi,
   this narrows to partially-absent tags and test 5 carries the `None` path.
4. **Drift fails the existing test.** The encode-profile test — the one the
   parent spec calls the most important in its plan — gains the four colour
   assertions, so colour drift fails the same test that already catches
   resolution, frame rate and pixel format, rather than sitting in a separate
   test someone can forget exists.
5. **Normalization.** `probe()` maps both spellings of missing metadata to
   `None`. Strict equality rests entirely on this, so it gets its own test.

---

## 8. Merge note: `feat/reels`

**Resolved 2026-08-21.** `feat/reels` has since merged to master, and master
merged into this branch cleanly — no conflict in `probe.py`, exactly as
predicted. The reconciliation it called for turned out to be a real defect
rather than a comment fix, and was made as part of this work.

`concat._parse_clip_params` read the four colour keys with raw `video.get(...)`
while `make_clip` reads them through the normalizer that folds ffprobe's
`"unknown"` into `None`. Two layers disagreeing about what "missing" means is
not cosmetic: an untagged clip whose keys are absent and one reporting
`"unknown"` compare UNEQUAL in `divergences()`, sending a reel down the full
re-encode path that `-c copy` exists to avoid. The normalizer is therefore now
public as `probe.color_tag` and shared by both — the same move master made for
`ffprobe_json`, for the same reason.

`ClipParams` keeps its own four fields rather than borrowing `MediaInfo`'s.
They answer different questions — `MediaInfo` describes a source, `ClipParams`
describes what the concat demuxer will silently assume — and they now agree on
the one thing that has to match, which is how a missing tag is spelled.

The pre-flight comparison stays regardless, as §1 says. What changed is that
for clips this app cuts, those four can no longer diverge; the comparison
remains because a clip is a file on a disk, and one cut before this pin landed,
or dropped into `clips/` from outside, still reaches concat.
