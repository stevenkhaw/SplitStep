import subprocess
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from splitstep.accel import detect_accel
from splitstep.detect.features import FeatureFrame, Player
from splitstep.detect.geometry import Quad
from splitstep.media.probe import probe


@dataclass(frozen=True)
class Box:
    """Person bounding box, normalized to the frame."""

    cx: float
    cy: float
    w: float
    h: float

    @property
    def foot(self) -> float:
        return self.cy + self.h / 2


def _in_region(box: Box, quad: Quad) -> bool:
    return quad.contains(box.cx, box.foot)


def split_near_far(
    boxes: Sequence[Box], quad: Quad
) -> tuple[Box | None, Box | None]:
    """Largest in-region box is the near player, next largest is the far one.

    Apparent height is the distance proxy: at a baseline camera the near
    player's box is 2-4x the far player's. This replaces a tracker, which is
    unreliable at 5 fps sampling.
    """
    inside = sorted((b for b in boxes if _in_region(b, quad)),
                    key=lambda b: b.h, reverse=True)
    near = inside[0] if inside else None
    far = inside[1] if len(inside) > 1 else None
    return near, far


def _to_player(box: Box | None, prev: Box | None, step_ms: int) -> Player | None:
    if box is None:
        return None
    if prev is None or box.h <= 0:
        v = 0.0
    else:
        dist = ((box.cx - prev.cx) ** 2 + (box.foot - prev.foot) ** 2) ** 0.5
        v = (dist / box.h) / (step_ms / 1000)
    return Player(cx=round(box.cx, 4), foot=round(box.foot, 4),
                  h=round(box.h, 4), v=round(v, 4))


def build_features(
    boxes_per_frame: Sequence[Sequence[Box]],
    quad: Quad,
    audio_grid: Sequence[tuple[int, float]],
    step_ms: int,
) -> list[FeatureFrame]:
    frames: list[FeatureFrame] = []
    prev_near: Box | None = None
    prev_far: Box | None = None

    for i, boxes in enumerate(boxes_per_frame):
        near, far = split_near_far(boxes, quad)
        hits, reg = audio_grid[i] if i < len(audio_grid) else (0, 0.0)
        frames.append(FeatureFrame(
            t_ms=i * step_ms,
            n=sum(1 for b in boxes if _in_region(b, quad)),
            near=_to_player(near, prev_near, step_ms),
            far=_to_player(far, prev_far, step_ms),
            hits=hits,
            hit_reg=reg,
        ))
        prev_near, prev_far = near, far

    return frames


def _scaled_dims(width: int, height: int, target_w: int = 960) -> tuple[int, int]:
    """Mirror ffmpeg's `scale={target_w}:-2`: width fixed, height keeps the
    source aspect ratio and is rounded down to the nearest even number (`-2`
    requires a multiple of 2).

    A fixed `960:540` (forced 16:9) squashes any non-16:9 source -- notably
    vertical phone footage -- which degrades the apparent-height distance
    proxy the near/far split depends on. Verified byte-for-byte against
    ffmpeg 9.0.1's actual `scale=960:-2` output across several source aspect
    ratios, including 1080x1920 vertical video.
    """
    h = round(height * target_w / width)
    return target_w, h - (h % 2)


def _drain(pipe, sink: list[bytes]) -> None:
    sink.extend(iter(lambda: pipe.read(4096), b""))


def _run_frames(cmd: Sequence[str], frame_bytes: int) -> Iterator[bytes]:
    """Run `cmd`, yielding one `frame_bytes`-sized chunk of stdout per frame.

    stderr is drained on a background thread instead of being read after the
    loop. ffmpeg's stderr pipe fills at roughly 64 KB; a producer that is
    chatty on stderr -- or one hitting decode errors, exactly the case worth
    diagnosing -- blocks on that write once the pipe is full, while this
    generator is blocked reading stdout. Left undrained until after the
    loop, those two blocks deadlock each other with no timeout.
    """
    stderr_chunks: list[bytes] = []
    with subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes
    ) as proc:
        reader = threading.Thread(
            target=_drain, args=(proc.stderr, stderr_chunks), daemon=True
        )
        reader.start()
        try:
            while True:
                raw = proc.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                yield raw
        finally:
            reader.join(timeout=5.0)
        if proc.wait() != 0:
            stderr = b"".join(stderr_chunks).decode(errors="replace")
            raise RuntimeError(f"frame decode failed: {stderr.strip()}")


def iter_person_boxes(
    proxy: Path,
    *,
    sample_fps: int = 5,
    imgsz: int = 960,
    model_name: str = "yolo11n.pt",
) -> Iterator[list[Box]]:
    """Decode the proxy at sample_fps and yield person boxes per frame.

    YOLO inference itself is not unit tested here -- it is mocked everywhere
    else, and model inference in CI is slow and non-deterministic across
    devices. Frame decoding (`_run_frames`) is exercised directly in
    tests/test_vision.py without touching YOLO.
    """
    from ultralytics import YOLO

    accel = detect_accel()
    info = probe(proxy)
    width, height = _scaled_dims(info.width, info.height)

    cmd = ["ffmpeg", "-v", "error"]
    if accel.hwaccel:
        cmd += ["-hwaccel", accel.hwaccel]
    cmd += ["-i", str(proxy), "-vf", f"fps={sample_fps},scale={width}:-2",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]

    model = YOLO(model_name)
    frame_bytes = width * height * 3

    for raw in _run_frames(cmd, frame_bytes):
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
        result = model.predict(
            frame, imgsz=imgsz, classes=[0], device=accel.torch_device,
            verbose=False,
        )[0]

        boxes: list[Box] = []
        for x1, y1, x2, y2 in result.boxes.xyxy.tolist():
            boxes.append(Box(
                cx=((x1 + x2) / 2) / width,
                cy=((y1 + y2) / 2) / height,
                w=(x2 - x1) / width,
                h=(y2 - y1) / height,
            ))
        yield boxes
