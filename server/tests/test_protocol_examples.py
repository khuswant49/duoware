"""A7: the JSON examples in PROTOCOL.md parse with the server's codecs/models. The examples are read from
PROTOCOL.md itself (never copied), so a change to the document or to the code that breaks the match fails here."""

import json
import re
from pathlib import Path

from duoware.protocol._read import Obj
from duoware.protocol.dashboard import EventRecord, HelloMsg, Layout, StateMsg
from duoware.protocol.phone import (
    Bench, CameraSettings, Frame, Hello, Status, SyncReply, parse_beacon, parse_datagram, parse_sync_request,
    parse_ws_message,
)

PROTOCOL = Path(__file__).resolve().parents[2] / "PROTOCOL.md"


def blocks_by_heading() -> list[tuple[str, str]]:
    """(heading line, json block text) for every ```json fenced block in PROTOCOL.md."""
    heading, out, cur = "", [], None
    for line in PROTOCOL.read_text(encoding="utf-8").splitlines():
        if cur is None:
            if line.startswith("```json"):
                cur = []
            elif line.startswith("#"):
                heading = line
        elif line.startswith("```"):
            out.append((heading, "\n".join(cur)))
            cur = None
        else:
            cur.append(line)
    return out


def objects(text: str) -> list[dict]:
    dec, i, res = json.JSONDecoder(), 0, []
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        obj, i = dec.raw_decode(text, i)
        res.append(obj)
    return res


def examples(prefix: str) -> list[dict]:
    """Every parseable object under the heading that starts with `prefix` (blocks with `...`/`…` are skipped)."""
    res = []
    for heading, text in blocks_by_heading():
        if heading.lstrip("# ").startswith(prefix) and "..." not in text and "…" not in text:
            res += objects(text)
    return res


def test_there_are_examples_to_check():
    assert len(blocks_by_heading()) >= 10
    for prefix in ("2.", "3.2", "4.2", "4.3", "4.4", "4.5", "4.7", "6.1", "6.2", "7.5", "8."):
        assert examples(prefix), f"no example found under §{prefix}"


def test_section_2_frame():
    for ex in examples("2."):
        f = parse_datagram(json.dumps(ex).encode())
        assert isinstance(f, Frame) and f.bad_markers == 0 and len(f.markers) == 2
        assert f.scan == "roi" and f.searched == (1, 5)


def test_section_3_2_sync_pair():
    objs = examples("3.2")
    assert [o["t"] for o in objs] == ["sync", "sync_r"]
    req = parse_sync_request(json.dumps(objs[0]).encode())
    rep = parse_datagram(json.dumps(objs[1]).encode())
    assert isinstance(rep, SyncReply) and rep.n == req.n and rep.t1 == req.t1


def test_section_4_2_beacon():
    (ex,) = examples("4.2")
    assert parse_beacon(json.dumps(ex).encode()).frames_tcp_port == 47802


def test_section_4_3_hello():
    (ex,) = examples("4.3")
    h = parse_ws_message(json.dumps(ex))
    assert isinstance(h, Hello) and h.link.wifi.band_ghz == 5 and h.camera.yuv_sizes[1] == (1280, 720, 60.0)


def test_section_4_4_settings():
    (ex,) = examples("4.4")
    s = CameraSettings.parse(Obj(ex))
    assert s.tracking.track_ids == (1, 5) and s.thermal.resolution_steps == ((960, 540),)


def test_section_4_5_status():
    (ex,) = examples("4.5")
    s = parse_ws_message(json.dumps(ex))
    assert isinstance(s, Status) and s.app_mode == "tracking" and s.stages_ms["cap_to_sent"].p95 == 41.0


def test_section_4_7_bench():
    (ex,) = examples("4.7")
    b = parse_ws_message(json.dumps(ex))
    assert isinstance(b, Bench) and b.results[0].data["pipeline"] == "native_roi"


def test_section_6_dashboard_messages():
    (hello,) = examples("6.1")
    assert HelloMsg.model_validate(hello).state_hz == 20
    (state,) = examples("6.2")
    m = StateMsg.model_validate(state)
    assert m.cars[0].name == "DUO-A" and m.cameras[0].calib.status == "OK"


def test_section_7_5_layout():
    (ex,) = examples("7.5")
    lay = Layout.model_validate(ex)
    assert lay.version == 3 and lay.warnings[0].code == "edge_angle"


def test_section_8_event_record():
    (ex,) = examples("8.")
    assert EventRecord.model_validate(ex).type == "registry"


def test_placeholder_blocks_are_the_ones_excluded():
    skipped = [h for h, t in blocks_by_heading() if "..." in t or "…" in t]
    assert any("4.3" in h for h in skipped) and any("6.3" in h for h in skipped)
    assert not re.search(r"^#+ (2\.|3\.2|4\.[2457]|6\.[12]|7\.5|8\.)", "\n".join(skipped), re.M)
