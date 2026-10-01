"""M2 step 6 / B10: the Android app's FrameWriter golden output parses with the server's codec (the same file is the
expected output of the app's FrameWriterTest), so the phone and the server cannot drift apart silently."""

from pathlib import Path

from duoware.protocol.phone import parse_datagram

GOLDEN = Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "test" / "resources" / "frame_golden.json"


def test_android_golden_frame_parses():
    data = GOLDEN.read_bytes()
    assert data.endswith(b" " * 6 + b"}")                       # the reserved sent_ns field keeps trailing spaces
    f = parse_datagram(data)
    assert (f.cam, f.sid, f.seq) == (1, "9f2c4e1a7b3d5c60", 48213)
    assert (f.cap_ns, f.exp_ns, f.skew_ns, f.avail_ns, f.sent_ns) == (81234567890123, 3000000, 18500000,
                                                                      81234612890123, 81234629890123)
    assert (f.w, f.h, f.scan, tuple(f.searched)) == (1280, 720, "roi", (1, 5))
    assert f.bad_markers == 0
    assert [m.id for m in f.markers] == [1, 5]
    assert tuple(f.markers[1].corners[:2]) == (0.0, -1.3)      # -0.04 is written 0.0, -1.26 as -1.3
