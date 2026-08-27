# Clean-account smoke checklist

The `.dmg` was built and its internals verified by machine. What was **not**
verified, and cannot be from this account, is the part a friend actually
experiences: Gatekeeper, a first run with no config, and a real drive.

Do this in a **second macOS user account** — the poor man's clean machine.
System Settings → Users & Groups → Add User. Log into it. Nothing in
`~/Library/Application Support/splitstep` exists there, which is the point.

## Already verified on Steven's own account (2026-08-27)

Done, do not redo:

- [x] Gatekeeper. Tested by writing a quarantine flag onto a copy of the dmg
      rather than needing a second account — the flag moved from `0081`
      (downloaded) to `0181` (user-approved), so the dialog appeared and was
      cleared through the documented path.
- [x] Installed to /Applications, replacing a previous copy.
- [x] Opened the real library on the SanDisk drive: chooser appeared, Choose
      folder worked, the button read **Open** (not Create), and the app came
      up on 304 rallies.
- [x] `mode` stayed `dev` after opening — friend mode is only written when a
      library is created, which is the intended behaviour.
- [x] Quit left no sidecar, no pidfile and nothing listening.

Still open below.

## Install

- [ ] Copy `SplitStep.dmg` across (AirDrop to yourself, or a USB stick)
- [ ] Double-click it; drag SplitStep to Applications
- [ ] **Right-click → Open.** Confirm the Gatekeeper dialog appears and that
      `docs/INSTALL.md` describes what you actually see — the wording of that
      dialog changes between macOS versions, and the doc is written from
      memory, not from this machine
- [ ] If Open Anyway was needed instead, note it and correct INSTALL.md

## First run

- [ ] The chooser appears, not the app
- [ ] It reads "Where should your videos live?" and explains the drive reasoning
- [ ] The suggested path is `~/Movies/SplitStep` and free space is shown
- [ ] **Choose folder…** opens a real macOS picker
- [ ] Pick a folder on the external drive; Create
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
- [ ] Settings → Change library → the chooser lists both libraries
- [ ] Switch to the other one and back; nothing was moved or lost

## Known-unknown

Detection quality on a friend's footage is Gate 0's business, not this
phase's. If the cuts are wrong here, that is not the app being broken.
