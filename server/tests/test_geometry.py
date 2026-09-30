"""Step 6: pure geometry helpers and the simulated camera model."""

import math

import numpy as np
import pytest

from duoware.localization.geometry import (
    apply_offset, heading_from_corners, parallax_correct, side_length_mm, square_corners, to_floor, wrap_deg,
)
from duoware.sim.camera_model import PinholeCamera


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


def test_camera_projects_a_known_point():
    cam = PinholeCamera((0, 0, 1000), (0, 0), 0, (1000, 500), 90, 0.0)      # f = 500 px
    px, ok = cam.project(np.array([[0, 0, 0], [500, 0, 0], [0, 250, 0], [5000, 0, 0]]))
    assert px[0] == pytest.approx([500, 250]) and px[1] == pytest.approx([750, 250]) and px[2] == pytest.approx([500, 375])
    assert list(ok) == [True, True, True, False]


def test_camera_height_shrinks_distance_and_yaw_rotates():
    cam = PinholeCamera((0, 0, 1000), (0, 0), 0, (1000, 500), 90, 0.0)
    flat, _ = cam.project(np.array([[400, 0, 0]]))
    raised, _ = cam.project(np.array([[400, 0, 200]]))                         # closer to the lens: looks bigger
    assert raised[0, 0] - 500 == pytest.approx((flat[0, 0] - 500) * 1000 / 800)
    turned = PinholeCamera((0, 0, 1000), (0, 0), 90, (1000, 500), 90, 0.0)     # camera X axis now along floor +y
    px, _ = turned.project(np.array([[0, 200, 0]]))
    assert px[0] == pytest.approx([500 + 200 * 0.5, 250])


def test_barrel_distortion_pulls_points_towards_the_centre():
    straight = PinholeCamera((0, 0, 1000), (0, 0), 0, (1000, 500), 90, 0.0)
    barrel = PinholeCamera((0, 0, 1000), (0, 0), 0, (1000, 500), 90, -0.05)
    pincushion = PinholeCamera((0, 0, 1000), (0, 0), 0, (1000, 500), 90, 0.05)
    pt = np.array([[600, 0, 0]])
    d0, d1, d2 = (abs(c.project(pt)[0][0, 0] - 500) for c in (straight, barrel, pincushion))
    assert d1 < d0 < d2
    # first-order size of the effect: r = 600 / 1000 = 0.6, scale = 1 + k1 r^2
    assert d1 == pytest.approx(d0 * (1 - 0.05 * 0.36))


def test_tilt_moves_the_image_of_the_nadir():
    cam = PinholeCamera((0, 0, 1000), (5, 0), 0, (1000, 500), 90, 0.0)
    px, _ = cam.project(np.array([[0, 0, 0]]))
    assert px[0, 0] == pytest.approx(500) and abs(px[0, 1] - 250) == pytest.approx(500 * math.tan(math.radians(5)), rel=0.02)
