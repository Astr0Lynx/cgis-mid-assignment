"""Tiny polygon helpers (no shapely needed): clipping one convex polygon by another."""

Point = tuple[float, float]


def signed_area(poly: list[Point]) -> float:
    """Positive for counter-clockwise rings."""
    return 0.5 * sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]))


def _open(ring: list[Point]) -> list[Point]:
    """A ring without the repeated closing point."""
    return ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else list(ring)


def clip_convex(subject: list[Point], clip: list[Point]) -> list[Point]:
    """Intersection of two CONVEX polygons (Sutherland-Hodgman). Returns an open ring, empty if they do not overlap.

    Both rings may be open or closed and in either orientation.
    """
    subject, clip = _open(subject), _open(clip)
    if len(subject) < 3 or len(clip) < 3:
        return []
    if signed_area(clip) < 0:
        clip = clip[::-1]  # the algorithm keeps the left side of each edge: make the clip ring counter-clockwise

    def inside(p: Point, a: Point, b: Point) -> bool:
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= 0

    def cross(p: Point, q: Point, a: Point, b: Point) -> Point:
        d1x, d1y, d2x, d2y = q[0] - p[0], q[1] - p[1], b[0] - a[0], b[1] - a[1]
        t = ((a[0] - p[0]) * d2y - (a[1] - p[1]) * d2x) / (d1x * d2y - d1y * d2x)
        return (p[0] + t * d1x, p[1] + t * d1y)

    output = subject
    for a, b in zip(clip, clip[1:] + clip[:1]):
        if not output:
            return []
        source, output = output, []
        for p, q in zip(source, source[1:] + source[:1]):
            if inside(q, a, b):
                if not inside(p, a, b):
                    output.append(cross(p, q, a, b))
                output.append(q)
            elif inside(p, a, b):
                output.append(cross(p, q, a, b))
    return output if len(output) >= 3 and abs(signed_area(output)) > 1e-12 else []
