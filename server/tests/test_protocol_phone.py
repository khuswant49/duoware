"""Step 2: phone codecs (PROTOCOL.md §2-4): round trips and every row of the §2 error table."""

import json

import pytest

from duoware.protocol import ProtocolError
from duoware.protocol._read import Obj
from duoware.protocol.phone import (
    Beacon, Frame, PreviewHeader, SyncReply, SyncRequest, TcpDeframer, encode_beacon, encode_frame,
    encode_preview, encode_sync, encode_sync_r, encode_tcp, marker_capture_ns, parse_beacon, parse_datagram,
    parse_preview, parse_sync_request, parse_ws_message,
)
from duoware.protocol.phone_session import (
    CameraCaps, CameraSettings, CpuInfo, Hello, Link, PerfState, StageStats, Status, ThermalState,
    Welcome, WifiInfo, encode_error, encode_hello, encode_settings, encode_status, encode_welcome,
)


def frame_dict(**over):
    d = {"v": 1, "t": "frame", "cam": 1, "sid": "ab" * 8, "seq": 7, "cap_ns": 1000, "exp_ns": 3_000_000,
         "skew_ns": 0, "avail_ns": 2000, "sent_ns": 3000, "w": 1280, "h": 720, "scan": "full", "searched": [],
         "m": [[1, 10.0, 10.0, 50.0, 10.0, 50.0, 50.0, 10.0, 50.0]]}
    d.update(over)
    return d


def parse(d) -> Frame:
    r = parse_datagram(json.dumps(d).encode())
    assert isinstance(r, Frame)
    return r


def test_frame_round_trip_and_empty_frame():
    f = parse(frame_dict())
    again = parse_datagram(encode_frame(f))
    assert again == f
    empty = parse(frame_dict(m=[]))
    assert empty.markers == () and empty.bad_markers == 0


def test_sync_round_trips():
    req = SyncRequest("ab" * 8, 123, 5512345678901)
    assert parse_sync_request(encode_sync(req)) == req
    rep = SyncReply(1, "ab" * 8, 123, 5512345678901, 81234000000000, 81234000040000)
    assert parse_datagram(encode_sync_r(rep)) == rep


def test_beacon_round_trip():
    b = Beacon("a3f9c2d17e804b55", "DUO-WARE", "192.168.42.129", 8000, 47801, 47802)
    assert parse_beacon(encode_beacon(b)) == b
    with pytest.raises(ProtocolError) as e:
        parse_beacon(json.dumps({"v": 2, "t": "beacon"}).encode())
    assert e.value.code == "version"


@pytest.mark.parametrize("data", [
    b"not json", b"[]", b"{}", json.dumps(frame_dict(seq="x")).encode(), json.dumps(frame_dict(w=1.5)).encode(),
    json.dumps({k: v for k, v in frame_dict().items() if k != "sid"}).encode(),
    json.dumps(frame_dict(scan="half")).encode(), json.dumps(frame_dict(m="x")).encode(),
    json.dumps(frame_dict(t="other")).encode(), json.dumps(frame_dict(cam=True)).encode(), b"\xff\xfe",
])
def test_section_2_not_json_missing_field_wrong_type_is_bad(data):
    with pytest.raises(ProtocolError) as e:
        parse_datagram(data)
    assert e.value.code == "bad"


def test_section_2_version_mismatch():
    with pytest.raises(ProtocolError) as e:
        parse_datagram(json.dumps(frame_dict(v=2)).encode())
    assert e.value.code == "version"


def test_section_2_too_big():
    with pytest.raises(ProtocolError) as e:
        parse_datagram(b" " * 8193)
    assert e.value.code == "too_big"
    raw = json.dumps(frame_dict(m=[])).encode()
    assert len(raw) < 8192
    parse_datagram(raw + b" " * (8192 - len(raw)))   # exactly 8192 bytes is still fine


def test_section_2_bad_markers_are_dropped_and_counted():
    good = [1, 10.0, 10.0, 50.0, 10.0, 50.0, 50.0, 10.0, 50.0]
    cases = [
        [50] + good[1:],                              # id outside the dictionary
        [-1] + good[1:],
        good[:3] + [float("nan")] + good[4:],         # non-finite corner
        good[:3] + [1e9] + good[4:],                  # far outside [-w, 2w]
        good[:5] + [-1281.0] + good[6:],              # x below -w
        good[:2] + [1441.0] + good[3:],               # y above 2h
        good[:8],                                     # too few numbers
        "junk",
    ]
    f = parse(frame_dict(m=[good] + cases))
    assert len(f.markers) == 1 and f.bad_markers == len(cases)
    edge = [2, -1280.0, -720.0, 2560.0, 1440.0, 0.0, 0.0, 1.0, 1.0]     # exactly on the allowed bounds
    assert len(parse(frame_dict(m=[edge])).markers) == 1


def test_marker_capture_time_uses_skew_by_row():
    f = parse(frame_dict(cap_ns=1_000_000_000, exp_ns=4_000_000, skew_ns=20_000_000,
                         m=[[1, 0, 360, 10, 360, 10, 360, 0, 360]]))
    # cy = 360 of h = 720 -> half the skew
    assert marker_capture_ns(f, f.markers[0]) == 1_000_000_000 + 2_000_000 + 10_000_000


def test_preview_round_trip_and_rejects():
    raw = encode_preview(123456789, 480, 270, b"\xff\xd8jpeg")
    head, jpeg = parse_preview(raw)
    assert head == PreviewHeader(123456789, 480, 270) and jpeg == b"\xff\xd8jpeg"
    for bad in (b"XXXX" + raw[4:], raw[:10], raw[:16], b"DWP1" + bytes(12) + b"x"):
        with pytest.raises(ProtocolError) as e:
            parse_preview(bad)
        assert e.value.code == "bad"
    with pytest.raises(ProtocolError) as e:
        parse_preview(raw + bytes(512 * 1024))
    assert e.value.code == "too_big"


def test_tcp_framing_split_at_every_byte_boundary():
    msgs = [encode_frame(parse(frame_dict(seq=i))) for i in range(3)] + [b"x"]
    stream = b"".join(encode_tcp(m) for m in msgs)
    for cut in range(len(stream) + 1):
        d = TcpDeframer()
        got = d.feed(stream[:cut]) + d.feed(stream[cut:])
        assert got == msgs
    d = TcpDeframer()
    got = []
    for i in range(len(stream)):
        got += d.feed(stream[i:i + 1])
    assert got == msgs


def test_tcp_framing_rejects_bad_lengths():
    for bad in (b"\x00\x00\x00\x00", (8193).to_bytes(4, "big") + b"x"):
        with pytest.raises(ProtocolError) as e:
            TcpDeframer().feed(bad)
        assert e.value.code == "bad"
    with pytest.raises(ProtocolError):
        encode_tcp(b"")
    with pytest.raises(ProtocolError):
        encode_tcp(b"x" * 8193)
    assert len(encode_tcp(b"x" * 8192)) == 8196


def hello() -> Hello:
    caps = CameraCaps("0", "FULL", ("MANUAL_SENSOR",), "REALTIME", (10000, 500000000), (100, 6400),
                      ((15, 30), (30, 30)), ((1280, 720, 60.0),), True, 10.0, (1402.1, 1402.1, 640.3, 361.0, 0.0),
                      (0.021, -0.043, 0.0, 0.0, 0.0), (4080, 3072))
    return Hello("dev-1", None, "K7QX-M2", "0.2.0", "realme RMX3393", "13", 33,
                 Link("wireless", "wlan0", "192.168.1.23", WifiInfo(5.0, -51, 866)),
                 CpuInfo(8, (2000000,) * 8), caps)


def test_hello_round_trip_with_nulls():
    h = hello()
    assert parse_ws_message(encode_hello(h)) == h
    nullish = Hello("d", "tok", None, "1", "m", "13", 33, Link("wired_adb", None, None, None), CpuInfo(4, (1, 2, 3, 4)),
                    CameraCaps("0", "LEGACY", (), "UNKNOWN", None, None, (), (), False, None, None, None, None))
    assert parse_ws_message(encode_hello(nullish)) == nullish


def test_status_round_trip_and_unmeasured_values():
    s = Status(1, "tracking", "boottime", hello().link, 59.7, 60.0, (1280, 720),
               {"pipeline": StageStats(28.0, 34.1), "detect_full": None, "detect_roi": StageStats(None, None),
                "send": None, "cap_to_sent": None},
               {"full_every": 10, "rois_per_frame": 2.0, "lost_rescans": 3}, 3, 0, 3000000, 800, "locked", None, None,
               ThermalState(0, None, 0, None), PerfState(True, True, None, False, None, True), None, None, 7)
    back = parse_ws_message(encode_status(s))
    assert back == s and back.cpu_app_pct is None and back.thermal.headroom is None


def test_ws_message_errors():
    for text in ("nope", "[]", json.dumps({"t": "hello"}), json.dumps({"v": 1, "t": "what"})):
        with pytest.raises(ProtocolError) as e:
            parse_ws_message(text)
        assert e.value.code == "bad"
    with pytest.raises(ProtocolError) as e:
        parse_ws_message(json.dumps({"v": 2, "t": "hello"}))
    assert e.value.code == "version"
    with pytest.raises(ProtocolError):
        parse_ws_message(json.dumps({"v": 1, "t": "hello", "device_id": "d"}))


def test_settings_and_welcome_encode():
    raw = {"resolution": [1280, 720], "fps": 0, "exposure_ns": 3000000, "iso": 800, "focus": "locked",
           "awb": "locked", "tracking": {"track_ids": [1, 5], "full_scan_every": 10, "roi_margin": 1.5,
                                         "roi_min_px": 64, "threads": 0, "demote_after_scans": 3,
                                         "corner_refine": "subpix", "aruco3": False},       # M2 keys (§4.4)
           "thermal": {"forecast_s": 10, "headroom_down": 0.85, "headroom_up": 0.65, "up_after_s": 60,
                       "status_down": 2, "fps_steps": [1.0, 0.75, 0.5], "resolution_steps": [[960, 540]]},
           "preview": {"fps": 3, "width": 480, "quality": 60}}
    s = CameraSettings.parse(Obj(raw))
    assert CameraSettings.parse(Obj(json.loads(encode_settings(s)))) == s
    w = Welcome(1, "ab" * 8, None, 47801, 47802, "a3f9c2d17e804b55", s)
    assert Welcome.parse(Obj(json.loads(encode_welcome(w)))) == w
    assert json.loads(encode_error("bad_pair_code", "x"))["code"] == "bad_pair_code"
