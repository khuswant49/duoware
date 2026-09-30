"""Step 5 + review F7: the discovery beacon (PROTOCOL.md §4.2)."""

import asyncio
import socket

import pytest
from conftest import patched_settings

from duoware.ingest.beacon import BeaconSender, Interface, directed_broadcast, interfaces
from duoware.protocol.phone import parse_beacon


@pytest.mark.parametrize("ip,mask,expected", [
    ("192.168.42.129", "255.255.255.0", "192.168.42.255"),
    ("10.20.5.77", "255.255.254.0", "10.20.5.255"),          # /23: the assumed /24 answer would be wrong
    ("10.20.4.77", "255.255.254.0", "10.20.5.255"),
    ("10.20.5.77", "255.255.252.0", "10.20.7.255"),          # /22
    ("172.16.9.9", "255.255.0.0", "172.16.255.255"),         # /16
    ("192.168.1.5", "255.255.255.252", "192.168.1.7"),       # /30
    ("192.168.1.5", "255.255.255.254", None),                # /31
    ("192.168.1.5", "255.255.255.255", None),                # /32
    ("192.168.1.5", "garbage", None),
])
def test_directed_broadcast_uses_the_real_netmask(ip, mask, expected):
    assert directed_broadcast(ip, mask) == expected


def test_default_destinations_are_the_directed_then_the_limited_broadcast():
    assert Interface("wlan0", "10.20.5.77", "255.255.254.0").targets() == ["10.20.5.255", "255.255.255.255"]
    assert Interface("ppp0", "10.0.0.2", "255.255.255.255").targets() == ["255.255.255.255"]


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listener(host: str, port: int) -> socket.socket:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind((host, port))
    rx.settimeout(2)
    return rx


def test_beacon_goes_to_every_destination_of_an_interface(env):
    port = free_udp_port()
    a, b = listener("127.0.0.1", port), listener("127.0.0.2", port)       # stand-ins for the two broadcast addresses
    try:
        st = patched_settings(env.settings, beacon_port=port)
        itf = Interface("lo", "127.0.0.1", "255.0.0.0", destinations=("127.0.0.1", "127.0.0.2"))
        sender = BeaconSender(st, "a3f9c2d17e804b55", lambda: [itf], lambda: (8000, 47801, 47802))
        assert sender.send_once() == 2
        for rx in (a, b):
            m = parse_beacon(rx.recv(2048))
            assert (m.server_id, m.name, m.host, m.http_port, m.frames_port, m.frames_tcp_port) == \
                ("a3f9c2d17e804b55", "DUO-WARE", "127.0.0.1", 8000, 47801, 47802)
    finally:
        a.close()
        b.close()


async def test_beacon_repeats_every_interval(env):
    port = free_udp_port()
    rx = listener("127.0.0.1", port)
    rx.setblocking(False)
    try:
        st = patched_settings(env.settings, beacon_port=port, beacon_interval_s=0.05)
        itf = Interface("lo", "127.0.0.1", "255.0.0.0", destinations=("127.0.0.1",))
        sender = BeaconSender(st, "x" * 16, lambda: [itf], lambda: (1, 2, 3))
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


def test_a_failing_destination_is_logged_once_and_skipped(env, caplog):
    port = free_udp_port()
    rx = listener("127.0.0.1", port)
    itf = Interface("x", "127.0.0.1", "255.0.0.0", destinations=("203.0.113.255", "127.0.0.1"))
    sender = BeaconSender(patched_settings(env.settings, beacon_port=port), "x" * 16, lambda: [itf], lambda: (1, 2, 3))
    try:
        with caplog.at_level("WARNING"):
            first, second = sender.send_once(), sender.send_once()
        assert first in (1, 2) and second == 1                    # the good destination always works; the bad one stops
        bad = [r for r in caplog.records if "203.0.113.255" in r.message]
        assert len(bad) <= 1
        assert parse_beacon(rx.recv(2048)).host == "127.0.0.1"
    finally:
        rx.close()


def test_interfaces_come_with_their_real_netmask():
    for itf in interfaces():
        assert not itf.ip.startswith(("127.", "169.254.")) and itf.netmask.count(".") == 3 and itf.name
        assert directed_broadcast(itf.ip, itf.netmask) != itf.ip
