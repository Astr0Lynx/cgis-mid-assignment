import pytest

from app.geometry import clip_convex, signed_area

SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4)]


def area(poly):
    return abs(signed_area(poly))


def test_overlap_of_two_squares_is_their_common_square():
    out = clip_convex(SQUARE, [(2, 2), (6, 2), (6, 6), (2, 6)])
    assert area(out) == pytest.approx(4.0) and {(round(x), round(y)) for x, y in out} == {(2, 2), (4, 2), (4, 4), (2, 4)}


def test_disjoint_polygons_give_nothing():
    assert clip_convex(SQUARE, [(10, 10), (12, 10), (12, 12), (10, 12)]) == []


def test_contained_polygon_is_returned_whole_whatever_the_order_or_orientation():
    inner = [(1, 1), (2, 1), (2, 2), (1, 2)]
    for sub, clp in ((inner, SQUARE), (inner[::-1], SQUARE), (inner, SQUARE[::-1]), (inner + [inner[0]], SQUARE + [SQUARE[0]])):
        assert area(clip_convex(sub, clp)) == pytest.approx(1.0)


def test_a_tilted_quadrilateral_is_cut_by_a_rectangle():
    tilted = [(0, 0), (10, 2), (10, 12), (0, 10)]  # a slanted parallelogram, like a satellite scene
    cut = clip_convex(tilted, [(2, 0), (8, 0), (8, 20), (2, 20)])
    assert 0 < area(cut) < area(tilted) and all(2 - 1e-9 <= x <= 8 + 1e-9 for x, _ in cut)


def test_degenerate_input_is_safe():
    assert clip_convex([(0, 0), (1, 1)], SQUARE) == [] and clip_convex(SQUARE, []) == []
