import json
from dataclasses import dataclass

_EDGE_TOL = 1e-9


@dataclass(frozen=True)
class Quad:
    """Four normalized (0-1) points describing the play region, in order."""

    points: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        points = tuple(self.points)
        if len(points) != 4:
            raise ValueError(f"Quad needs exactly 4 points, got {len(points)}")
        coerced = []
        for p in points:
            try:
                px, py = p
                coerced.append((float(px), float(py)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Quad point must be an (x, y) pair, got {p!r}") from exc
        # dataclass is frozen: bypass __setattr__ to store the validated,
        # immutable representation instead of whatever was passed in.
        object.__setattr__(self, "points", tuple(coerced))

    def _on_boundary(self, x: float, y: float) -> bool:
        """True if (x, y) lies on any edge (or vertex) of the quad.

        There is only ever one play-region quad per video source, so
        boundary points don't need to be disambiguated against a
        neighboring quad. The play region is deliberately drawn to the
        bottom of the frame, and the near player's foot point can land
        exactly on that edge (camera height clips their bounding box) -
        such points must count as inside.
        """
        pts = self.points
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            cross = (xi - xj) * (y - yj) - (yi - yj) * (x - xj)
            if abs(cross) <= _EDGE_TOL:
                within_x = min(xj, xi) - _EDGE_TOL <= x <= max(xj, xi) + _EDGE_TOL
                within_y = min(yj, yi) - _EDGE_TOL <= y <= max(yj, yi) + _EDGE_TOL
                if within_x and within_y:
                    return True
            j = i
        return False

    def contains(self, x: float, y: float) -> bool:
        """Ray-casting point-in-polygon, inclusive on every edge and vertex."""
        if self._on_boundary(x, y):
            return True
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
