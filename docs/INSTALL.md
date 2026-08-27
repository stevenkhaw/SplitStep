# Installing SplitStep

SplitStep is not signed by Apple, so macOS blocks it the first time. This is
one extra step, once.

1. Copy `SplitStep.dmg` to your Mac and double-click it.
2. Drag **SplitStep** into your Applications folder.
3. Open SplitStep. macOS refuses, saying it **can't verify it's free of
   malware**. This is expected — it means the app isn't registered with
   Apple, not that anything is wrong with it. Click **Done**.
4. Open **System Settings → Privacy & Security** and scroll down. There is a
   line about SplitStep being blocked, with an **Open Anyway** button. Click
   it, and confirm with Touch ID or your password.
5. Open SplitStep again. It starts.

After that it opens normally, forever.

> On macOS 14 and earlier you can skip steps 3–4 by right-clicking SplitStep
> and choosing **Open**. Apple removed that shortcut in macOS 15, so on any
> recent Mac the Privacy & Security route above is the only way through.

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
