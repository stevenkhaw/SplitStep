# Third-party components

SplitStep itself is AGPL-3.0-or-later (`LICENSE`). That was inherited rather
than chosen: the components below include copyleft ones that the shipped
`.dmg` redistributes, and a permissive licence here would have misstated what
a recipient is actually entitled to.

## Redistributed in the `.dmg`

These ship *inside* the app bundle, so distributing the `.dmg` distributes
them. They are fetched by `packaging/fetch_assets.sh` and are not committed to
this repository.

| Component | Version | Licence | Notes |
|---|---|---|---|
| FFmpeg (`ffmpeg`, `ffprobe`) | 9.0.1-tessus | **GPL-3.0-or-later** | Static macOS builds from [evermeet.cx](https://evermeet.cx/ffmpeg/), configured `--enable-gpl --enable-version3`. See the source obligation below. |
| Ultralytics (YOLO11) | 8.4.x | **AGPL-3.0** | The detector. This is the strongest copyleft in the tree and the reason SplitStep is AGPL. |
| `yolo11n.pt` | — | **AGPL-3.0** | Ultralytics' pretrained weights, under the same terms as the library. |
| PyTorch / torchvision | 2.13 / 0.28 | BSD-3-Clause, Apache-2.0 | Pulled in by Ultralytics. |
| Roboto Condensed | wght=700 instance | **OFL-1.1** | `splitstep/assets/font.ttf`, the numbered-reel overlay face. Committed rather than fetched, so the CLI and the app burn the same typeface. See `splitstep/assets/README.md` for why it is instanced and not the variable file. |
| Python runtime + the packages below | — | see table | Frozen into the sidecar by PyInstaller. |

## Python dependencies

| Package | Licence |
|---|---|
| fastapi | MIT |
| uvicorn | BSD-3-Clause |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib, CC0-1.0 components) |
| scipy | BSD-3-Clause |
| opencv-python-headless | Apache-2.0 |
| watchdog | Apache-2.0 |
| platformdirs | MIT |
| python-multipart | Apache-2.0 |
| pillow | MIT-CMU (HPND) |
| ultralytics | **AGPL-3.0** |

## Frontend and shell

Build-time only — none of these ship as source in the bundle, only the
compiled `web/dist` output and the Tauri binary.

| Component | Licence |
|---|---|
| Svelte, Vite, Tailwind CSS, TypeScript, Vitest, svelte-check, jsdom | MIT |
| Tauri v2 | MIT or Apache-2.0 |

## The FFmpeg source obligation

GPL-3.0 requires that anyone given the binary can get its corresponding
source. SplitStep ships FFmpeg binaries it did not build, so the obligation is
passed along rather than absorbed:

- The exact build is `ffmpeg version 9.0.1-tessus`, published at
  <https://evermeet.cx/ffmpeg/>, which links the source and the configure line
  for each release.
- FFmpeg's own source for that version is at <https://ffmpeg.org/download.html>
  and <https://git.ffmpeg.org/ffmpeg.git>.

**This is a link, not a written offer.** If SplitStep is ever distributed more
widely than hand-to-hand, mirror the corresponding FFmpeg source (or a durable
written offer for it) alongside the `.dmg` rather than relying on a third
party's site staying up.

## Open: this repository is private

Copyleft obligations attach to *distribution*, and handing someone the `.dmg`
is distribution. AGPL-3.0 entitles that person to SplitStep's own source, and
GPL-3.0 entitles them to FFmpeg's — but `github.com/stevenkhaw/SplitStep` is
private, so the obvious route to the first one does not exist for them. Two
ways to close it, and they are a choice rather than a bug to fix silently:

1. **Make the repository public.** The `.dmg`, `LICENSE` and this file then all
   point at something a recipient can actually reach.
2. **Keep it private and hand the source over directly** to whoever gets a
   build, along with a written offer for the FFmpeg source.

Doing neither is the only option that is not compliant. Note also that the
`.dmg` does not currently embed `LICENSE` or this file — `packaging/` was
written before either existed — so a bundle passed on by itself, without the
release page around it, carries no licence text at all.

## AGPL §13 and the loopback server

The AGPL's network clause covers users who interact with the program
"remotely through a computer network". As shipped there are none: `serve`
defaults to `--host 127.0.0.1`, and the desktop shell picks an ephemeral
loopback port and connects to it on the same machine, so the only user is the
one at the keyboard.

`--host` is a flag, though, not a constant. Binding SplitStep to a reachable
interface — or modifying it and serving that — is exactly the case §13
governs, and it obliges whoever does it to offer the corresponding source to
those users.
