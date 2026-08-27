# Gate 0 — Fence-Mount Footage, Pair Mode's First Real Run

**Date:** 2026-08-27 (overnight session)
**Sources:** `sessions/2026-08-25/sources/01` (IMG_2416, 21.7 min, preset "2026-08-25
fence mount", rotation 180) and `sources/02` (IMG_2417, 18.0 min, ingested 2026-08-26
20:37, detected 20:50)
**Status:** first pass from stills + feature statistics. **No tuning was done and none
is recommended yet** — read `2026-08-20-camera-viewpoint-validation.md` before touching
any constant; everything below is consistent with its findings, not a license to re-fit.

---

## The classifier did its job

`analyze_view` on the fence-mounted source: **profile=pair, foot_separation=0.1284,
6491 of 6519 frames paired, low_confidence=False.** That is 2.6× the 0.05 boundary and
21× the ground-level measurement (0.006) from 2026-08-18. Raising the camera produced
exactly the geometry the classifier was built to detect. Route 1 of the validation
doc's conclusion ("raise the camera") is confirmed *as far as classification goes*.

Source 02, shot the next evening from a lower mount, measured **0.0499 — one part in
five hundred below the 0.05 boundary** — and classified `subject`. The classification
is defensible (the stills show a near-ground view), but a source this close to the
line will flip between profiles on re-shoots. Worth knowing: the two profiles carry
thresholds on different scales (0.45 vs 0.25), so a flip is not a small change.
`ViewGeometry` already carries `low_confidence`; it does not flag *boundary-proximity*.
If flips ever bite, that is the lever — a "near the boundary" warning in the setup/
detect log, not a threshold change.

## But the footage is not what pair mode models

Pair mode assumes two players at different depths *rallying across the net*. Stills
across the whole source (r10, r25 burst ×4, r40, r55, r65, r72) show something else:
**same-side drills** — Steven and a partner side by side on the near court hitting
toward a mostly-empty far court, with a ball hopper at the net, plus stretches of solo
serve practice. There is real, vigorous hitting (the r25 burst catches mid-forehand
frames) — but the "far player" the score believes in is usually not a participant.

Who is the far box, then? The court quad's top edge sits at y=0.32, above our far
baseline, so the band where strangers stand along the fence line is *inside* the
region. Feature statistics over all 6521 frames:

| measure | value | reading |
|---|---|---|
| both_present | **99.54%** | a "far player" exists in effectively every frame of a busy public venue |
| far.foot | med 0.385, p10 0.330 | the fence-line band — strangers on courts 13/14/15 |
| far.h med | 0.108 (vs near.h 0.330) | far boxes are small/distant figures, as expected |
| frames with audio hits | **63.7%** | the venue again — same signature the 2026-08-20 doc measured at 0.56–0.67 impacts/sec regardless of our play |

Consequences for the pair score on this footage:

- `w_both` is a constant, not a discriminator (99.54% saturation).
- `w_outside` never fires — the strangers it should penalize are inside the quad.
- `w_hits`/`w_reg` measure the venue (per the validation doc, audio cannot be
  re-fit against; that conclusion carries over to pair mode unchanged).
- What actually discriminates is `w_speed·min(near_v, far_v)` and lateral share,
  i.e. mostly our own near-court motion — plus stranger motion as `far_v`.

The observable result: **72 rallies, 68.5% coverage (893 s of 1304 s), and every
confidence between 0.56 and 0.686** — a 0.13-wide band floating entirely above the
0.45 threshold. Confidence is uninformative here in the same way the validation doc
found for subject mode: nothing scores low, because half the score is always on.

## Spot verdicts from stills

| span | verdict from stills |
|---|---|
| r1 0.3–13.7 s (conf 0.626) | ❌ pre-play milling/chat at the net. Steven had already rejected it in review — the detector's 2nd-highest-band conf on a clean false positive. |
| r25 455.7–464.1 s | ✅ real hitting (drill context, genuine swings, mid-stroke frames) |
| r40 / r55 / r65 midpoints | solo practice stances — serve practice or between-feeds; stills can't fully settle, motion presumably decided the score |
| r72 1296.5–1301.9 s | ⚠️ walking + others milling; teardown-adjacent, likely FP |
| gaps 348 s, 715 s, 1280 s | ✅ correctly not detected (chat, walking, teardown) |
| gap 100.3 s | transition/ball-pickup; nothing clearly missed |

No missed play was seen in the sampled gaps — recall looks healthy on this footage;
precision is where the noise is, consistent with the recall-biased design.

## Label-corpus state (what blocks a real verdict)

- **Source 01: zero labels.** The pair-mode verdict Gate 0 wants requires a label-mode
  pass (`L` in the review queue) over these 72 candidates. That is human work — stills
  cannot judge "was that swing a point or a feed."
- **Source 02: 16 label rows resolving to 4 spans, all boundary corrections from
  drags, no verdicts.** `labels score` at 0.25: 49 candidates, 0 decided, boundary
  MAE ≈ 4.9 s on both ends (start bias −0.9 s, end bias +4.2 s, n=4).

## What would actually move Gate 0 (in order)

1. **Steven: label-mode pass on source 01** — verdicts, not just boundaries. Also
   settles a scope question only he can answer: are same-side drills "play" the
   detector *should* keep? (For a coaching-notes workflow, probably yes — which makes
   much of the 68.5% coverage correct-by-intent rather than over-segmentation.)
2. **Cheap, principled experiment (no constants touched): redraw the fence-mount quad
   with its top edge at our far baseline**, re-run a full detect (NOT
   `--reuse-features` — the quad is baked into features), and compare counts/conf
   spread/`labels score`. If strangers leave the region, `both_present` stops
   saturating and `w_outside` regains meaning. This is using the machinery as
   designed, not tuning.
3. Only after 1+2: consider whether pair weights need anything at all. Do not re-fit
   against audio clusters (validation doc, standing rule).

## Verdict so far

- Classifier: ✅ validated on real footage in both directions (0.128 elevated / 0.0499
  ground-level), with one knife-edge case worth a future boundary-proximity warning.
- Pair scoring on *this* footage: producing usable-looking intervals (real hitting
  detected, chat/teardown mostly outside), but for partly wrong reasons — its
  strongest terms are saturated by the venue, and confidence carries no signal.
- Nothing here contradicts the 2026-08-20 findings; the audio term measuring the
  venue reproduces on a second source and a second camera height.
