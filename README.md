# BootlegVision

Local tennis rally cutter. Ingests phone footage, segments it into rallies,
serves them over a REST API.

## Setup

```bash
conda create -n bootleg python=3.12 -y
conda activate bootleg
pip install -e ".[dev]"
brew install ffmpeg
```

## Library

The library is a self-contained folder, normally on an external drive:

```
/Volumes/BootlegVision/
  library.db
  _inbox/            drop videos here
  sessions/<date>/sources/NN/{original,proxy.mp4,thumbs.jpg,features.jsonl}
  reels/
```

Create it once with `bootleg init` — the app never creates it implicitly, so
a missing or unmounted drive (or a leftover empty mountpoint from an unclean
eject) is an error instead of a silent second library on internal storage.

```bash
bootleg --library /Volumes/BootlegVision init
```

`init` refuses if a library already exists at that path.

## Use

```bash
bootleg --library /Volumes/BootlegVision doctor      # check hardware + paths
bootleg --library /Volumes/BootlegVision serve       # http://127.0.0.1:8420
```

Drop a video in `_inbox/`. It is picked up within 5 seconds once the file
stops growing, transcoded to a 1080p proxy, and detected automatically.

## Tuning segmentation

Detection caches features to `features.jsonl`, so re-segmenting costs
milliseconds and needs no GPU:

```bash
bootleg --library /Volumes/BootlegVision segment <source_id> --threshold 0.35 --dry-run
```

## Tests

```bash
pytest -v
```

Tests never run YOLO. Detector output is fixtured or mocked throughout.
