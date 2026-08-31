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
