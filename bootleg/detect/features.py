import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Player:
    cx: float     # bbox centre x, normalized
    foot: float   # bbox bottom y, normalized
    h: float      # bbox height, normalized — the distance proxy
    v: float      # speed in body-lengths per second


@dataclass(frozen=True)
class FeatureFrame:
    t_ms: int
    n: int                  # persons detected inside the play region
    near: Player | None
    far: Player | None
    hits: int               # audio impacts in the trailing 1 s
    hit_reg: float          # 0-1 regularity of the last 4 inter-hit intervals

    def to_json_line(self) -> str:
        d: dict = {"t": self.t_ms, "n": self.n, "hits": self.hits,
                   "hit_reg": round(self.hit_reg, 4)}
        if self.near is not None:
            d["near"] = {k: round(v, 4) for k, v in asdict(self.near).items()}
        if self.far is not None:
            d["far"] = {k: round(v, 4) for k, v in asdict(self.far).items()}
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> "FeatureFrame":
        d = json.loads(line)
        return cls(
            t_ms=int(d["t"]),
            n=int(d["n"]),
            near=Player(**d["near"]) if "near" in d else None,
            far=Player(**d["far"]) if "far" in d else None,
            hits=int(d.get("hits", 0)),
            hit_reg=float(d.get("hit_reg", 0.0)),
        )


def write_features(path: Path, frames: Iterable[FeatureFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for frame in frames:
            fh.write(frame.to_json_line() + "\n")


def read_features(path: Path) -> list[FeatureFrame]:
    with path.open() as fh:
        return [FeatureFrame.from_json_line(ln) for ln in fh if ln.strip()]
