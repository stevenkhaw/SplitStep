# Smoke checklist

Most of this is now done, on a genuinely clean second Mac. What remains is
listed at the bottom.

## Verified on a second Mac, downloaded via Chrome (2026-08-27)

This is the real friend path, not a simulation of it. Do not redo:

- [x] **Gatekeeper.** Downloaded in Chrome (so a real quarantine flag),
      dragged to Applications, opened → *"can't verify it's free of
      malware"* → System Settings → Privacy & Security → Open Anyway → runs.
      That matches `docs/INSTALL.md` as now written.
- [x] Install to /Applications from the dmg.
- [x] First run with no config: the chooser appeared and the folder picker
      worked.
- [x] Selected the external drive; it found the SanDisk library and opened it.

Two wrong answers preceded this, both worth remembering:

1. The first build was **not** signed — only the signature the linker applies
   to the arm64 executable, sealing no bundle resources
   (`Sealed Resources=none`). Unquarantined macOS is lenient, so it ran
   locally and looked finished; quarantined it reported **"damaged"**, which
   has no Open button and no way through.
   `bundle.macOS.signingIdentity: "-"` fixed it.
2. A quarantine flag moving from `0081` to `0181` was read as proof the app
   had launched. It only proves *something* was approved. Check the app
   actually starts.

## Verified on Steven's own account

- [x] Opening the real library: the button read **Open** (not Create), and the
      app came up on 304 rallies.
- [x] `mode` stayed `dev` after opening — friend mode is only written when a
      library is *created*.
- [x] Quit left no sidecar, no pidfile and nothing listening.

To re-test Gatekeeper without a second machine, write the download flag onto a
copy and open that:

```bash
cp ~/Desktop/SplitStep_0.2.0_aarch64.dmg ~/Desktop/q.dmg && xattr -w com.apple.quarantine "0081;00000000;Safari;" ~/Desktop/q.dmg
```

## Score tracking, show-rejected, timeline fit (2026-09-17)

Exercised in the browser against a synthetic scratch library, not the real
library or a second Mac — the external drive was not mounted this session:

- [x] Track score: tick the checkbox, fill in the setup card, the panel
      appears beside the video
- [x] `P` marks a point and (with tracking on) opens the *Who won?* prompt;
      `A` / `B` record the winner and advance; `Esc` keeps the point and
      records no winner
- [x] `U` after `A`/`B` undoes the winner, restoring the previous
      point/winner state
- [x] Show rejected (`H`): reject a rally, reload the session, `H` shows it
      hatched in the overview band, `X` un-rejects it, `H` again hides it
- [x] Rapid/repeated `H` presses (key-repeat, or a press while the
      show-rejected refetch is still in flight) do not double-fire or
      desync the toggle from the list
- [x] Timeline mode's deck and both bands fit one screen without scrolling:
      measured 888/900 at 1440×900 and 1068/1080 at 1920×1080
- [ ] Unticking **Track score** and accepting the confirm dialog — the
      sandboxed browser used this session suppresses `confirm()`, so only
      the decline leg was exercised
- [ ] A numbered reel render with the scoreboard burned in, on real footage
      (only PNG-level overlay tests have run)
- [ ] Migration `013_score_tracking.sql` applied to the live library — the
      external drive was not mounted this session

## Re-detect call to action (2026-09-18)

Exercised in the browser against the synthetic scratch library on :8421, not
the real library — the same scratch library the 2026-09-17 rows used, with a
200-line `features.jsonl` copied in from `tests/fixtures/` so the re-segment
panel had something to score:

- [x] Assigning a region in the quad editor renders the status sentence and
      **Run detection with this region** as one filled-button card, not a
      caption-sized text link. This is the bug it exists for: the link was
      missed, the reviewer re-segmented instead, nothing changed, and
      re-segment looked broken
- [x] The re-segment panel warns *"Play region changed after the last detect
      — re-segment still uses the old one"* with its own **Run detection**
      button, driven by `preset_assigned_at` (migration 014) against the
      mtime of `features.jsonl`
- [ ] Either button actually queueing a detect, end to end — the scratch
      library's proxy is synthetic and YOLO is never run here, and the
      sandboxed browser suppresses `confirm()`, so only the render was
      exercised. The click path is covered by vitest
- [ ] Migration `014_preset_assigned_at.sql` applied to the live library —
      the external drive was not mounted this session

## Blind labelling pass (2026-09-18)

Exercised against the synthetic scratch library on :8421. The Browser pane
would not composite this session, so the clicks and keystrokes below were
dispatched into the live page rather than made with a mouse — the handlers
and the writes are real, the pointer was not:

- [x] `GET /api/sources/{id}/label-sample` and `splitstep labels sample`
      return the same windows for the same seed
- [x] `#/audit/<source_id>` renders a window, advances on a verdict, and
      shows `k / n · j judged`
- [x] A verdict lands in `rally_labels` with `rally_id` NULL
- [x] `U` retracts: an append-only retraction row, the counter drops, and the
      pass returns to the window it was on
- [x] Re-labelling appends rather than overwriting — five rows for three
      windows after one undo and one correction
- [x] The all-judged card offers **Draw another sample**
- [x] `splitstep labels score` prints `sampled recall (blind windows)` beside
      the old figure
- [ ] A pass over real footage. The scratch library's proxy is synthetic and
      its features are a fixture slice, so every figure above is arithmetic,
      not a judgement about tennis
- [ ] Playback keys (`Space`, `R`) — VideoDeck has no `<video>` under jsdom
      and the pane would not composite, so neither was exercised

## Still open

## First run

- [x] The chooser appears, not the app
- [x] **Choose folder…** opens a real macOS picker
- [ ] The suggested path is `~/Movies/SplitStep` and free space is shown
- [ ] Pick a *brand new* folder and press **Create** — every run so far has
      opened an existing library, so the create path has only been proven by
      invoking the bundled server directly, never through this button
- [ ] Window becomes the app within ~20 seconds
- [ ] Settings shows friend mode ON (Advanced unticked) — the shell writes
      `mode: friend` on a created library

## The actual work

- [ ] Drop a video; the wizard appears
- [ ] Set rotation and drag the court quad; Start Detection
- [ ] **A notification fires when detection finishes** (this is the one piece
      that has never run end to end — the poller reads `/api/jobs` for a
      `detect` job leaving the queue)
- [ ] Review a rally: star, reject, undo
- [ ] Export a clip; watch it in the Clips panel at full quality
- [ ] Build a reel; render it plain
- [ ] Render it numbered, and check the burned counter is **Bold** — the
      bundled font is Roboto Condensed instanced to wght=700, replacing the
      Arial Bold the 2026-08-26 verification used. If it looks light, the
      instancing did not take

## The failure paths

- [ ] Quit from the menu; `pgrep splitstep-server` finds nothing
- [ ] Relaunch: it goes **straight into the app**, no chooser
- [ ] Force Quit the app; `pgrep splitstep-server` finds an orphan
- [ ] Relaunch; exactly one server runs (the reaper worked)
- [ ] Eject the drive, relaunch: "Your library is not connected", naming the
      path, with the picker available
- [ ] Plug the drive back in, pick it, and land back in your sessions
- [ ] Settings → Change library → the chooser lists both libraries.
      This FAILED on a real second Mac with "Command back_to_chooser not
      allowed by ACL": Tauri denies app commands from the sidecar's
      http://127.0.0.1 origin unless a capability grants it, and every test
      that stayed on the launcher passed because the launcher is a local
      origin. Fixed by src-tauri/capabilities/default.json — re-verify.
- [ ] Switch to the other one and back; nothing was moved or lost
- [x] The pipe wedge, against the installed .dmg (2026-09-10): 1500 requests
      through a launched app, `~/Library/Logs/SplitStep/server.log` at 94,631
      bytes -- past the 64KB a pipe holds -- and still answering in 3ms. The
      same run against the previous build stops at ~1050
- [x] Quit straight after that: **1.21s**, no orphan sidecar, pidfile cleaned,
      and the log ends in uvicorn's own graceful shutdown, so SIGTERM landed
      and the SIGKILL fallback was never needed. The three `.hang` reports
      this replaced were 14s, 18s and 101s on exactly this path
- [ ] Still unexercised by hand: ten real minutes in a reel -- preview seeks
      and Alt+Arrow reorders rather than a loop of /api/config. Same mechanism,
      but nobody has sat through it

## Known-unknown

Detection quality on a friend's footage is Gate 0's business, not this
phase's. If the cuts are wrong here, that is not the app being broken.

## The redesign (2026-08-30)

- [ ] The court is visible behind Library and quiets on a session page
- [ ] Reels is reachable from a session page, not only from Library
- [ ] At full ultrawide width the cards go two-up and the review video is
      centred at 1920, not stretched
- [ ] Navigating to a session shows the split-ball mark where "Loading…"
      used to be, and it animates while the page's data loads
- [ ] A running detect job still shows its own indicator in the jobs badge
      — that is a different component from the mark, and neither one
      drives the other
- [ ] Every interactive element shows a visible focus ring with the mouse
      untouched
- [ ] The Dock icon is the split ball, and is legible in a Finder list view
