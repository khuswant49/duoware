"""Discovery beacon (PROTOCOL.md §4.2, DECISIONS.md D5).

Sent every `udp.beacon_interval_s` to the directed broadcast address of every IPv4 interface, because Windows
sends a limited broadcast (255.255.255.255) out of one interface only. `host` in each beacon is that
interface's own address.
"""

import asyncio
import logging
import socket
from collections.abc import Callable

from duoware.protocol.phone import Beacon, encode_beacon
from duoware.settings import Settings

log = logging.getLogger(__name__)

SERVER_NAME = "DUO-WARE"      # PROTOCOL.md §4.2 example
ASSUMED_PREFIX_LEN = 24       # the subnet mask is not read; see interfaces()

InterfacesFn = Callable[[], list[tuple[str, str]]]
PortsFn = Callable[[], tuple[int, int, int]]          # (http_port, frames_port, frames_tcp_port) as actually bound


def interfaces() -> list[tuple[str, str]]:
    """Own IPv4 addresses with their directed broadcast address, e.g. ("192.168.42.129", "192.168.42.255").

    LIMITATION: the subnet mask is unknown without platform calls, so every interface is assumed to be a /24
    (true for USB tethering, phone and laptop hotspots and most home routers). On a wider or narrower subnet
    the directed broadcast is wrong and phones on that interface will not hear the beacon; they can still pair
    with a manually entered host. The M2 hardware test checks this on the real tether."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:                                                    # the address used for the default route
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))                     # TEST-NET-1: no packet is sent by connect() on UDP
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for ip in sorted(ips):
        a = ip.split(".")
        if len(a) == 4 and not ip.startswith(("127.", "169.254.", "0.")):
            out.append((ip, ".".join(a[:ASSUMED_PREFIX_LEN // 8] + ["255"])))
    return out


class BeaconSender:
    def __init__(self, settings: Settings, server_id: str, interfaces_fn: InterfacesFn, ports_fn: PortsFn) -> None:
        self._udp = settings.server.udp
        self._server_id, self._interfaces, self._ports = server_id, interfaces_fn, ports_fn
        self._failed: set[str] = set()
        self._task: asyncio.Task | None = None

    def send_once(self) -> int:
        """Sends one beacon per interface. Returns how many were sent."""
        http_port, frames_port, tcp_port = self._ports()
        sent = 0
        for ip, bcast in self._interfaces():
            payload = encode_beacon(Beacon(self._server_id, SERVER_NAME, ip, http_port, frames_port, tcp_port))
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.bind((ip, 0))
                    s.sendto(payload, (bcast, self._udp.beacon_port))
                sent += 1
            except OSError as e:
                if ip not in self._failed:                       # log once per interface, then skip quietly
                    self._failed.add(ip)
                    log.warning("beacon: cannot send on %s (%s): %s", ip, bcast, e)
        return sent

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        try:
            while True:
                self.send_once()
                await asyncio.sleep(self._udp.beacon_interval_s)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
