"""Step 6: pure geometry helpers and the simulated camera model."""

import numpy as np
import pytest

from duoware.localization.geometry import (
    apply_offset, heading_from_corners, parallax_correct, side_length_mm, square_corners, to_floor, wrap_deg,
)


def test_wrap_deg():
    assert wrap_deg(190) == -170 and wrap_deg(-190) == 170 and wrap_deg(180) == 180 and wrap_deg(-180) == 180
    assert wrap_deg(720 + 45) == 45 and wrap_deg(0) == 0


@pytest.mark.parametrize("yaw", [-90, 0, 33, 90, 179, -135])
def test_square_corners_and_heading_round_trip(yaw):
    c = square_corners(100, 200, 90, yaw)
    assert side_length_mm(c) == pytest.approx(90)
    assert wrap_deg(heading_from_corners(c) - yaw) == pytest.approx(0, abs=1e-9)
    assert c.mean(axis=0) == pytest.approx([100, 200])


def test_origin_tag_layout_matches_protocol():
    # PROTOCOL.md §0: origin tag centre at (0, 0), TOP edge towards -y: TL is up-left, TR up-right.
    c = square_corners(0, 0, 90, -90)
    assert c[0] == pytest.approx([-45, -45]) and c[1] == pytest.approx([45, -45]) and c[3] == pytest.approx([-45, 45])


def test_to_floor_applies_a_homography():
    h = np.array([[2, 0, 10], [0, 2, -5], [0, 0, 1.0]])
    assert to_floor(h, np.array([[1, 1], [3, 4]])) == pytest.approx(np.array([[12, -3], [16, 3]]))


def test_parallax_pulls_towards_the_nadir():
    nadir = np.array([100.0, 100.0])
    assert parallax_correct(np.array([700.0, 100.0]), nadir, 1.05) == pytest.approx([100 + 600 / 1.05, 100])
    assert parallax_correct(nadir, nadir, 1.2) == pytest.approx(nadir)


def test_apply_offset_forward_and_left():
    # heading 0 = facing +x; "left" of a car facing +x on a y-down map is -y.
    assert apply_offset(np.zeros(2), 0, (10, 0)) == pytest.approx([10, 0])
    assert apply_offset(np.zeros(2), 0, (0, 10)) == pytest.approx([0, -10])
    assert apply_offset(np.zeros(2), 90, (10, 0)) == pytest.approx([0, 10], abs=1e-9)      # facing +y (down the map)
    assert apply_offset(np.zeros(2), 90, (0, 10)) == pytest.approx([10, 0], abs=1e-9)
    assert apply_offset(np.array([5.0, 5.0]), -90, (10, 10)) == pytest.approx([-5, -5], abs=1e-9)
