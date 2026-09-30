"""Discovery beacon (PROTOCOL.md §4.2, DECISIONS.md D5).

For every active IPv4 interface (up, not loopback, not link-local) the server sends, from a socket bound to that
interface's address, one beacon to the interface's directed broadcast address (computed from its real netmask, never
assumed) and one to the limited broadcast 255.255.255.255. `host` in each beacon is the interface's own address.
"""

import asyncio
import ipaddress
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

import psutil

from duoware.protocol.phone import Beacon, encode_beacon
from duoware.settings import Settings

log = logging.getLogger(__name__)

SERVER_NAME = "DUO-WARE"      # PROTOCOL.md §4.2 example
LIMITED_BROADCAST = "255.255.255.255"
NO_DIRECTED_PREFIX = 31       # a /31 or /32 has no directed broadcast address (PROTOCOL.md §4.2)


def directed_broadcast(ip: str, netmask: str) -> str | None:
    """The directed broadcast address of `ip` on a subnet with `netmask`; None for /31, /32 and bad input."""
    try:
        net = ipaddress.IPv4Network(f"{ip}/{netmask}", strict=False)
    except ValueError:
        return None
    return None if net.prefixlen >= NO_DIRECTED_PREFIX else str(net.broadcast_address)


@dataclass(frozen=True)
class Interface:
    name: str
    ip: str
    netmask: str
    destinations: tuple[str, ...] = field(default=())     # empty = derive: directed broadcast, then the limited one

    def targets(self) -> list[str]:
        if self.destinations:
            return list(self.destinations)
        directed = directed_broadcast(self.ip, self.netmask)
        return ([directed] if directed else []) + [LIMITED_BROADCAST]


InterfacesFn = Callable[[], list[Interface]]
PortsFn = Callable[[], tuple[int, int, int]]          # (http_port, frames_port, frames_tcp_port) as actually bound


def interfaces() -> list[Interface]:
    """Active IPv4 interfaces with their real netmasks (psutil): up, not loopback, not 169.254/16."""
    out = []
    try:
        stats, addrs = psutil.net_if_stats(), psutil.net_if_addrs()
    except Exception as e:                                   # pragma: no cover - platform dependent
        log.warning("beacon: cannot list network interfaces: %s", e)
        return out
    for name, items in sorted(addrs.items()):
        if name in stats and not stats[name].isup:
            continue
        for a in items:
            if a.family != socket.AF_INET or not a.address or not a.netmask:
                continue
            ip = ipaddress.IPv4Address(a.address)
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
                continue
            out.append(Interface(name, a.address, a.netmask))
    return out


class BeaconSender:
    def __init__(self, settings: Settings, server_id: str, interfaces_fn: InterfacesFn, ports_fn: PortsFn) -> None:
        self._udp = settings.server.udp
        self._server_id, self._interfaces, self._ports = server_id, interfaces_fn, ports_fn
        self._failed: set[tuple[str, str]] = set()
        self._task: asyncio.Task | None = None

    def send_once(self) -> int:
        """Sends the beacon on every interface to each of its destinations. Returns how many datagrams were sent.
        A destination that fails is logged once and skipped from then on."""
        http_port, frames_port, tcp_port = self._ports()
        sent = 0
        for itf in self._interfaces():
            payload = encode_beacon(Beacon(self._server_id, SERVER_NAME, itf.ip, http_port, frames_port, tcp_port))
            for dest in itf.targets():
                if (itf.ip, dest) in self._failed:
                    continue
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        s.bind((itf.ip, 0))
                        s.sendto(payload, (dest, self._udp.beacon_port))
                    sent += 1
                except OSError as e:
                    self._failed.add((itf.ip, dest))
                    log.warning("beacon: cannot send on %s (%s) to %s: %s", itf.name, itf.ip, dest, e)
        return sent

    def start(self) -> None:
        for itf in self._interfaces():
            log.info("beacon: interface %s address %s netmask %s -> %s", itf.name, itf.ip, itf.netmask,
                     ", ".join(itf.targets()))
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
