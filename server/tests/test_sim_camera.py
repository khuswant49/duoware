"""Step 9: the simulated pinhole camera with radial distortion."""

import math

import numpy as np
import pytest

from duoware.sim.camera_model import PinholeCamera


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
