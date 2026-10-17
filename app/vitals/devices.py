"""Known LAN devices (nicknames, first/last seen, new-device alerts) and Wake-on-LAN."""
from __future__ import annotations

import ipaddress
import re
import socket
import sys
import time
from typing import Any, Dict, List, Optional

from . import config
from .events import events
from .util import host_net, load_json, run, save_json

PATH = config.DATA / "devices.json"
_MAC = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")


def norm_mac(mac: str) -> str:
    mac = (mac or "").strip()
    if not _MAC.match(mac):
        raise ValueError("That isn't a valid MAC address")
    return mac.replace("-", ":").upper()


class Devices:
    def __init__(self) -> None:
        d = load_json(PATH, {})
        self.known: Dict[str, Dict[str, Any]] = d.get("known", {})
        self.baseline_done: bool = d.get("baseline", False)

    def save(self) -> None:
        save_json(PATH, {"known": self.known, "baseline": self.baseline_done})

    def merge(self, devices: List[Dict[str, Any]], now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Update the registry from a scan; returns the devices that are new."""
        now = now or time.time()
        new = []
        for d in devices:
            if not d.get("mac"):
                continue
            mac = d["mac"].upper()
            k = self.known.get(mac)
            if k is None:
                k = self.known[mac] = {"firstSeen": now, "nickname": ""}
                if self.baseline_done and not d.get("self"):
                    new.append(d)
            k.update(lastSeen=now, ip=d.get("ip"), vendor=d.get("vendor") or k.get("vendor"),
                     hostname=d.get("name") or k.get("hostname"))
            d["nickname"] = k.get("nickname", "")
            d["firstSeen"] = k["firstSeen"]
            d["new"] = d in new
        # the first scan just learns what's normal for this network
        self.baseline_done = True
        for d in new:
            label = d.get("name") or d.get("vendor") or "Unknown device"
            events.add("new_device", f"New device joined your network: {label} ({d['ip']})",
                       mac=d["mac"], ip=d["ip"])
        self.save()
        return new

    def rename(self, mac: str, nickname: str) -> Dict[str, Any]:
        mac = norm_mac(mac)
        k = self.known.setdefault(mac, {"firstSeen": time.time()})
        k["nickname"] = nickname.strip()[:40]
        self.save()
        return k

    def annotate(self, devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for d in devices:
            k = self.known.get((d.get("mac") or "").upper())
            if k:
                d["nickname"] = k.get("nickname", "")
                d["firstSeen"] = k.get("firstSeen")
        return devices


def magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + bytes.fromhex(norm_mac(mac).replace(":", "")) * 16


async def wake(mac: str, subnet: Optional[str]) -> str:
    """Send a Wake-on-LAN packet from the host's network (broadcasts don't leave Docker's bridge)."""
    norm_mac(mac)
    targets = ["255.255.255.255"]
    if subnet:
        try:
            targets.insert(0, str(ipaddress.ip_network(subnet, strict=False).broadcast_address))
        except ValueError:
            pass
    cmd = host_net([sys.executable, "-m", "vitals.wol", mac] + targets)
    rc, out, err = await run(cmd, timeout=10)
    if rc != 0:
        raise RuntimeError((err or out or "Couldn't send the packet").strip()[-200:])
    return f"Wake-up packet sent to {norm_mac(mac)}"


def send_now(mac: str, targets: List[str]) -> None:
    pkt = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for t in targets:
            for port in (9, 7):
                s.sendto(pkt, (t, port))


devices = Devices()
