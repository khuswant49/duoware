"""Step 5: the discovery beacon (PROTOCOL.md §4.2)."""

import asyncio
import socket

from conftest import patched_settings

from duoware.ingest.beacon import BeaconSender, interfaces
from duoware.protocol.phone import parse_beacon


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_beacon_reaches_a_listener_and_decodes(env):
    port = free_udp_port()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", port))
    rx.settimeout(2)
    try:
        st = patched_settings(env.settings, beacon_port=port)
        sender = BeaconSender(st, "a3f9c2d17e804b55", lambda: [("127.0.0.1", "127.0.0.1")], lambda: (8000, 47801, 47802))
        assert sender.send_once() == 1
        b = parse_beacon(rx.recv(2048))
        assert (b.server_id, b.name, b.host, b.http_port, b.frames_port, b.frames_tcp_port) == \
            ("a3f9c2d17e804b55", "DUO-WARE", "127.0.0.1", 8000, 47801, 47802)
    finally:
        rx.close()


async def test_beacon_repeats_every_interval(env):
    port = free_udp_port()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", port))
    rx.setblocking(False)
    try:
        st = patched_settings(env.settings, beacon_port=port, beacon_interval_s=0.05)
        sender = BeaconSender(st, "x" * 16, lambda: [("127.0.0.1", "127.0.0.1")], lambda: (1, 2, 3))
        sender.start()
        await asyncio.sleep(0.3)
        await sender.stop()
        n = 0
        while True:
            try:
                rx.recv(2048)
                n += 1
            except BlockingIOError:
                break
        assert n >= 3
    finally:
        rx.close()


def test_a_failing_interface_is_skipped_and_logged_once(env, caplog):
    sender = BeaconSender(env.settings, "x" * 16, lambda: [("203.0.113.77", "203.0.113.255"), ("127.0.0.1", "127.0.0.1")],
                          lambda: (1, 2, 3))
    with caplog.at_level("WARNING"):
        assert sender.send_once() == 1
        assert sender.send_once() == 1
    assert sum("cannot send on 203.0.113.77" in r.message for r in caplog.records) == 1


def test_interfaces_returns_directed_broadcasts():
    for ip, bcast in interfaces():
        assert not ip.startswith("127.") and bcast == ".".join(ip.split(".")[:3] + ["255"])
