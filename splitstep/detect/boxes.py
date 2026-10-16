"""Every YOLO box, kept.

Detection used to keep only the two people `split_near_far` elected and throw
the rest away, so any later question about *which* person to follow (fence
mode's player selection) meant re-running fifteen minutes of YOLO. This file
is the lossless record: every person and ball box per frame, written once by
the full detect and read back without a GPU.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

PERSON = 0
BALL = 32  # COCO "sports ball"
# Low on purpose: the file is the corpus later selection reads, so a faint
# far-court player or a ball must survive here. The legacy path filters back
# up to LEGACY_PERSON_CONF so every non-fence source sees what it always saw.
DETECT_CONF = 0.1
# What `classes=[0]` with ultralytics' default conf gave before this file existed.
LEGACY_PERSON_CONF = 0.25


@dataclass(frozen=True)
class Detection:
    """One YOLO box, x/y normalised to the scaled frame (same as `Box`)."""

    cls: int
    conf: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def foot(self) -> float:
        return self.y2

    @property
    def top(self) -> float:
        return self.y1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def w(self) -> float:
        return self.x2 - self.x1


def write_boxes(path: Path, frames: Iterable[tuple[int, list[Detection]]]) -> None:
    # Floats rounded on write (4dp, conf 3dp) so a read/write cycle is
    # byte-stable, the same contract features.jsonl carries.
    with path.open("w") as f:
        for t, dets in frames:
            b = [[d.cls, round(d.conf, 3), round(d.x1, 4), round(d.y1, 4),
                  round(d.x2, 4), round(d.y2, 4)] for d in dets]
            f.write(json.dumps({"t": t, "b": b}, separators=(",", ":")) + "\n")


def read_boxes(path: Path) -> list[tuple[int, list[Detection]]]:
    frames: list[tuple[int, list[Detection]]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        frames.append((row["t"], [Detection(int(c), conf, x1, y1, x2, y2)
                                  for c, conf, x1, y1, x2, y2 in row["b"]]))
    return frames
