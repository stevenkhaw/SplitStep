# Installing SplitStep

SplitStep is not signed by Apple, so macOS blocks it the first time. This is
one extra step, once.

1. Copy `SplitStep.dmg` to your Mac and double-click it.
2. Drag **SplitStep** into your Applications folder.
3. **Right-click** SplitStep in Applications and choose **Open** — do not
   double-click it the first time.
4. macOS says it cannot verify the developer. Click **Open**.
5. If there is no Open button, go to **System Settings → Privacy & Security**,
   scroll down, and click **Open Anyway** next to SplitStep. Then repeat
   step 3.

After that first launch it opens normally, forever.

## First run

SplitStep asks where to keep your videos. Video is big — a few hours of play
fills tens of gigabytes — so if you have an external drive, plug it in and
choose a folder there. Otherwise the suggested folder in Movies is fine. The
screen shows how much space is free wherever you point it.

Then drop your first video in. Set which way up it is and drag a box around
your court, and SplitStep finds the rallies.

## If your drive is not plugged in

SplitStep says so and waits, naming the folder it is looking for. Plug the
drive in and pick it again. Nothing is lost — the library is just a folder,
and it is still on the drive.

You can keep more than one library (say, one per drive). **Settings → Change
library** switches between them. Switching never moves or deletes anything.

## Requirements

An Apple Silicon Mac (M1 or later) running macOS 12 or later. Intel Macs are
not supported.

## What it is made of

Detection by Ultralytics YOLO11 (AGPL-3.0), video by FFmpeg (GPL). Source at
github.com/stevenkhaw/SplitStep.
