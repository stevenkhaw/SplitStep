import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Quad:
    """Four normalized (0-1) points describing the play region, in order."""

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if len(self.points) != 4:
            raise ValueError(f"Quad needs exactly 4 points, got {len(self.points)}")

    def contains(self, x: float, y: float) -> bool:
        """Ray-casting point-in-polygon."""
        inside = False
        pts = self.points
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y):
                x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < x_cross:
                    inside = not inside
            j = i
        return inside

    def to_json(self) -> str:
        return json.dumps([list(p) for p in self.points])

    @classmethod
    def from_json(cls, raw: str) -> "Quad":
        return cls(tuple((float(a), float(b)) for a, b in json.loads(raw)))
