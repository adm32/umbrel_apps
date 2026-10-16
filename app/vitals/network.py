"""Host network interfaces, data usage accounting, latency monitor and speed test."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import socket
import statistics
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import aiohttp
import psutil

from . import config, settings
from .util import Job, host_net, load_json, read_int, read_text, run, save_json

VIRTUAL_PREFIXES = ("lo", "docker", "br-", "veth", "virbr", "vnet", "tap", "dummy", "cni", "flannel", "kube")
VPN_PREFIXES = ("tailscale", "wg", "tun", "zt", "nordlynx", "utun")


# ---------------------------------------------------------------- counters

def read_counters() -> Dict[str, Tuple[int, int, int, int]]:
    """iface -> (rx_bytes, tx_bytes, rx_packets, tx_packets) for the host namespace."""
    path = config.host_proc_net("dev")
    raw = read_text(path) if config.IS_LINUX else None
    out: Dict[str, Tuple[int, int, int, int]] = {}
    if raw:
        for line in raw.splitlines()[2:]:
            if ":" not in line:
                continue
            name, rest = line.split(":", 1)
            f = rest.split()
            if len(f) >= 10:
                out[name.strip()] = (int(f[0]), int(f[8]), int(f[1]), int(f[9]))
        return out
    for name, c in psutil.net_io_counters(pernic=True).items():
        out[name] = (c.bytes_recv, c.bytes_sent, c.packets_recv, c.packets_sent)
    return out


def iface_kind(name: str) -> str:
    if name.startswith(VPN_PREFIXES):
        return "vpn"
    if name.startswith(VIRTUAL_PREFIXES):
        return "virtual"
    base = config.host_sys(f"class/net/{name}")
    if os.path.isdir(os.path.join(base, "wireless")) or name.startswith(("wl", "wlan")):
        return "wifi"
    if os.path.exists(os.path.join(base, "device")) or name.startswith(("en", "eth")):
        return "ethernet"
    if not config.IS_LINUX and name.startswith("en"):
        return "ethernet"
    return "virtual"


def is_physical(name: str) -> bool:
    return iface_kind(name) in ("ethernet", "wifi")


# ---------------------------------------------------------------- interface details

def default_route() -> Tuple[Optional[str], Optional[str]]:
    raw = read_text(config.host_proc_net("route")) or ""
    for line in raw.splitlines()[1:]:
        f = line.split()
        if len(f) >= 3 and f[1] == "00000000" and f[2] != "00000000":
            gw = socket.inet_ntoa(int(f[2], 16).to_bytes(4, "little"))
            return f[0], gw
    return None, None


def dns_servers() -> List[str]:
    raw = read_text(config.host_path("/etc/resolv.conf")) or ""
    servers = re.findall(r"^nameserver\s+(\S+)", raw, re.M)
    if not servers or all(s.startswith("127.") for s in servers):
        alt = read_text(config.host_path("/run/systemd/resolve/resolv.conf")) or ""
        servers = re.findall(r"^nameserver\s+(\S+)", alt, re.M) or servers
    return servers


def wifi_stats() -> Dict[str, Dict[str, Any]]:
    raw = read_text(config.host_proc_net("wireless")) or ""
    out = {}
    for line in raw.splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        f = rest.split()
        try:
            out[name.strip()] = {"quality": float(f[1].rstrip(".")), "signal": float(f[2].rstrip("."))}
        except (IndexError, ValueError):
            pass
    return out


async def _iw_link(name: str) -> Dict[str, Any]:
    rc, out, _ = await run(host_net(["iw", "dev", name, "link"]), timeout=5)
    if rc != 0:
        return {}
    info: Dict[str, Any] = {}
    for key, pat in (("ssid", r"SSID:\s*(.+)"), ("freq", r"freq:\s*([\d.]+)"),
                     ("signal", r"signal:\s*(-?\d+)"), ("txRate", r"tx bitrate:\s*([\d.]+\s*\S+)"),
                     ("rxRate", r"rx bitrate:\s*([\d.]+\s*\S+)")):
        m = re.search(pat, out)
        if m:
            info[key] = m.group(1).strip()
    return info


async def interfaces() -> List[Dict[str, Any]]:
    addrs: Dict[str, Dict[str, Any]] = {}
    rc, out, _ = await run(host_net(["ip", "-j", "addr", "show"]), timeout=5)
    if rc == 0 and out.strip().startswith("["):
        for i in json.loads(out):
            addrs[i["ifname"]] = {
                "mac": i.get("address"),
                "mtu": i.get("mtu"),
                "state": (i.get("operstate") or "").lower(),
                "ipv4": [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", []) if a.get("family") == "inet"],
                "ipv6": [f"{a['local']}/{a['prefixlen']}" for a in i.get("addr_info", [])
                         if a.get("family") == "inet6"],
            }
    else:
        stats = psutil.net_if_stats()
        for name, lst in psutil.net_if_addrs().items():
            st = stats.get(name)
            addrs[name] = {
                "mac": next((a.address for a in lst if a.family == psutil.AF_LINK), None),
                "mtu": st.mtu if st else None,
                "state": "up" if st and st.isup else "down",
                "ipv4": [f"{a.address}/{_prefix(a.netmask)}" for a in lst if a.family == socket.AF_INET],
                "ipv6": [a.address.split("%")[0] for a in lst if a.family == socket.AF_INET6],
            }
    default_if, gateway = default_route()
    wifi = wifi_stats()
    out_list = []
    for name, a in addrs.items():
        kind = iface_kind(name)
        if kind == "virtual" and name != default_if:
            continue
        base = config.host_sys(f"class/net/{name}")
        speed = read_int(os.path.join(base, "speed"))
        item = {
            "name": name,
            "kind": kind,
            "default": name == default_if,
            "speedMbps": speed if speed and speed > 0 else None,
            "duplex": read_text(os.path.join(base, "duplex")),
            **a,
        }
        if kind == "wifi":
            item["wifi"] = {**wifi.get(name, {}), **(await _iw_link(name))}
        out_list.append(item)
    out_list.sort(key=lambda i: (not i["default"], i["kind"] != "ethernet", i["name"]))
    return out_list


def _prefix(mask: Optional[str]) -> int:
    try:
        return sum(bin(int(x)).count("1") for x in (mask or "").split("."))
    except ValueError:
        return 0


def local_subnet() -> Optional[str]:
    """CIDR of the LAN the default interface sits on, e.g. 192.168.1.0/24."""
    import ipaddress

    iface, _ = default_route()
    raw = read_text(config.host_proc_net("route")) or ""
    for line in raw.splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[0] == iface and f[1] != "00000000" and f[2] == "00000000":
            net = socket.inet_ntoa(int(f[1], 16).to_bytes(4, "little"))
            mask = socket.inet_ntoa(int(f[7], 16).to_bytes(4, "little"))
            n = ipaddress.ip_network(f"{net}/{mask}", strict=False)
            if n.prefixlen >= 16 and not n.is_loopback:
                return str(n)
    for i, lst in psutil.net_if_addrs().items():
        for a in lst:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "172.17.")):
                return str(ipaddress.ip_network(f"{a.address}/{a.netmask}", strict=False))
    return None


# ---------------------------------------------------------------- public info

_public: Dict[str, Any] = {"at": 0, "data": None}


async def public_info(session: aiohttp.ClientSession, force: bool = False) -> Optional[Dict[str, Any]]:
    if not force and time.time() - _public["at"] < 900 and _public["data"]:
        return _public["data"]
    try:
        async with session.get("https://speed.cloudflare.com/meta", timeout=aiohttp.ClientTimeout(total=6)) as r:
            m = await r.json(content_type=None)
        data = {
            "ip": m.get("clientIp"),
            "isp": m.get("asOrganization"),
            "asn": m.get("asn"),
            "city": m.get("city"),
            "region": m.get("region"),
            "country": m.get("country"),
            "colo": m.get("colo"),
        }
        _public.update(at=time.time(), data=data)
    except Exception:
        if not _public["data"]:
            return None
    return _public["data"]


# ---------------------------------------------------------------- usage accounting

class UsageTracker:
    """Counts bytes on physical interfaces into hourly and daily buckets."""

    PATH = config.DATA / "netusage.json"

    def __init__(self) -> None:
        d = load_json(self.PATH, {})
        self.days: Dict[str, List[int]] = d.get("days", {})
        self.hours: Dict[str, List[int]] = d.get("hours", {})
        self.last: Dict[str, Tuple[int, int]] = {}
        self.dirty = False

    def add(self, counters: Dict[str, Tuple[int, int, int, int]]) -> None:
        now = dt.datetime.now()
        day = now.strftime("%Y-%m-%d")
        hour = now.strftime("%Y-%m-%dT%H")
        rx_total = tx_total = 0
        for name, c in counters.items():
            if not is_physical(name):
                continue
            prev = self.last.get(name)
            self.last[name] = (c[0], c[1])
            if prev is None:
                continue
            drx = c[0] - prev[0] if c[0] >= prev[0] else c[0]
            dtx = c[1] - prev[1] if c[1] >= prev[1] else c[1]
            rx_total += drx
            tx_total += dtx
        if rx_total or tx_total:
            d = self.days.setdefault(day, [0, 0])
            d[0] += rx_total
            d[1] += tx_total
            h = self.hours.setdefault(hour, [0, 0])
            h[0] += rx_total
            h[1] += tx_total
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        cutoff_day = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        cutoff_hour = (dt.datetime.now() - dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H")
        self.days = {k: v for k, v in self.days.items() if k >= cutoff_day}
        self.hours = {k: v for k, v in self.hours.items() if k >= cutoff_hour}
        save_json(self.PATH, {"days": self.days, "hours": self.hours})
        self.dirty = False

    def summary(self) -> Dict[str, Any]:
        today = dt.date.today()
        reset = settings.get()["net_reset_day"]
        if today.day >= reset:
            cycle_start = today.replace(day=reset)
        else:
            prev_month = (today.replace(day=1) - dt.timedelta(days=1))
            cycle_start = prev_month.replace(day=min(reset, 28))
        def total(start: dt.date) -> List[int]:
            s = start.isoformat()
            rx = sum(v[0] for k, v in self.days.items() if k >= s)
            tx = sum(v[1] for k, v in self.days.items() if k >= s)
            return [rx, tx]
        last30 = []
        for i in range(29, -1, -1):
            d = (today - dt.timedelta(days=i)).isoformat()
            v = self.days.get(d, [0, 0])
            last30.append({"day": d, "rx": v[0], "tx": v[1]})
        last24 = []
        now = dt.datetime.now()
        for i in range(23, -1, -1):
            h = (now - dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H")
            v = self.hours.get(h, [0, 0])
            last24.append({"hour": h, "rx": v[0], "tx": v[1]})
        return {
            "today": self.days.get(today.isoformat(), [0, 0]),
            "cycle": total(cycle_start),
            "cycleStart": cycle_start.isoformat(),
            "month": total(today.replace(day=1)),
            "days": last30,
            "hours": last24,
        }


# ---------------------------------------------------------------- latency monitor

def _parse_ping_ms(out: str) -> List[float]:
    return [float(x) for x in re.findall(r"time[=<]([\d.]+)\s*ms", out)]


async def ping_once(ip: str, timeout: int = 1) -> Optional[float]:
    wait = str(timeout) if config.IS_LINUX else str(timeout * 1000)  # BSD ping takes ms
    rc, out, _ = await run(host_net(["ping", "-n", "-c", "1", "-W", wait, ip]), timeout=timeout + 2)
    vals = _parse_ping_ms(out)
    return vals[0] if vals else None


async def resolve(host: str) -> str:
    """Resolve in the container namespace (Docker DNS works here, not in the host netns)."""
    try:
        socket.inet_pton(socket.AF_INET, host)
        return host
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return host
    except OSError:
        pass
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
    return (v4 or [infos[0][4][0]])[0]


class LatencyMonitor:
    INTERVAL = 5.0

    def __init__(self) -> None:
        self.series: Dict[str, Deque[Tuple[float, Optional[float]]]] = {}
        self.labels: Dict[str, str] = {}
        self.online: Optional[bool] = None

    async def tick(self) -> None:
        s = settings.get()
        if not s["latency_enabled"]:
            return
        targets: Dict[str, str] = {}
        _, gw = default_route()
        if gw:
            targets["gateway"] = gw
            self.labels["gateway"] = f"Router ({gw})"
        custom = (s.get("latency_target") or "1.1.1.1").strip()
        try:
            ip = await resolve(custom)
            targets["internet"] = ip
            self.labels["internet"] = custom if custom == ip else f"{custom} ({ip})"
        except Exception:
            pass
        results = await asyncio.gather(*(ping_once(ip) for ip in targets.values()))
        now = time.time()
        for key, ms in zip(targets.keys(), results):
            dq = self.series.setdefault(key, deque(maxlen=720))
            dq.append((now, ms))
        if "internet" in targets:
            self.online = results[list(targets.keys()).index("internet")] is not None

    def summary(self, points: int = 120) -> Dict[str, Any]:
        out = {}
        for key, dq in self.series.items():
            vals = list(dq)
            ok = [v for _, v in vals if v is not None]
            recent = vals[-points:]
            out[key] = {
                "label": self.labels.get(key, key),
                "last": vals[-1][1] if vals else None,
                "avg": round(statistics.mean(ok), 2) if ok else None,
                "min": min(ok) if ok else None,
                "max": max(ok) if ok else None,
                "jitter": round(statistics.mean(abs(a - b) for a, b in zip(ok, ok[1:])), 2) if len(ok) > 2 else None,
                "loss": round(100 * (1 - len(ok) / len(vals)), 1) if vals else None,
                "series": [[round(t), v] for t, v in recent],
            }
        return {"online": self.online, "targets": out}


# ---------------------------------------------------------------- speed test (Cloudflare)

CF = "https://speed.cloudflare.com"


def bufferbloat_grade(idle: Optional[float], loaded: Optional[float]) -> Optional[str]:
    if idle is None or loaded is None:
        return None
    inc = max(0.0, loaded - idle)
    for limit, g in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if inc < limit:
            return g
    return "F"


class SpeedTest:
    PATH = config.DATA / "speedtests.json"

    def __init__(self) -> None:
        self.history: List[Dict[str, Any]] = load_json(self.PATH, [])
        self.last_auto = 0.0

    def latest(self) -> Optional[Dict[str, Any]]:
        return self.history[-1] if self.history else None

    async def run(self, job: Job, session: aiohttp.ClientSession) -> Dict[str, Any]:
        res: Dict[str, Any] = {"at": time.time(), "server": "Cloudflare"}
        try:
            async with session.get(f"{CF}/meta", timeout=aiohttp.ClientTimeout(total=6)) as r:
                meta = await r.json(content_type=None)
            res["isp"] = meta.get("asOrganization")
            res["ip"] = meta.get("clientIp")
            res["colo"] = meta.get("colo")
            res["location"] = ", ".join(x for x in (meta.get("city"), meta.get("country")) if x)
        except Exception as e:
            raise RuntimeError(f"Can't reach the speed test server ({e.__class__.__name__}). Is the internet down?")

        # latency
        job.phase = "Latency"
        rtts: List[float] = []
        for i in range(12):
            t0 = time.perf_counter()
            async with session.get(f"{CF}/__down?bytes=0", timeout=aiohttp.ClientTimeout(total=5)) as r:
                await r.read()
                server_ms = 0.0
                m = re.search(r"dur=([\d.]+)", r.headers.get("Server-Timing", ""))
                if m:
                    server_ms = float(m.group(1))
            rtts.append(max(0.1, (time.perf_counter() - t0) * 1000 - server_ms))
            job.progress = 0.08 * (i + 1) / 12
        rtts = rtts[2:]  # drop connection warm-up
        res["ping"] = round(statistics.median(rtts), 1)
        res["jitter"] = round(statistics.mean(abs(a - b) for a, b in zip(rtts, rtts[1:])), 1)
        job.data["ping"] = res["ping"]
        job.data["jitter"] = res["jitter"]

        loaded: List[float] = []
        stop_ping = asyncio.Event()

        async def loaded_pinger() -> None:
            while not stop_ping.is_set():
                ms = await ping_once("1.1.1.1", 1)
                if ms is not None:
                    loaded.append(ms)
                await asyncio.sleep(0.4)

        # download
        job.phase = "Download"
        pinger = asyncio.ensure_future(loaded_pinger())
        res["download"] = await self._download(job, session, 0.08, 0.5)
        res["downloadLoadedPing"] = round(statistics.median(loaded), 1) if loaded else None
        loaded.clear()

        # upload
        job.phase = "Upload"
        res["upload"] = await self._upload(job, session, 0.5, 0.98)
        res["uploadLoadedPing"] = round(statistics.median(loaded), 1) if loaded else None
        stop_ping.set()
        await pinger

        idle_icmp = await ping_once("1.1.1.1", 1)
        worst = max([x for x in (res["downloadLoadedPing"], res["uploadLoadedPing"]) if x is not None], default=None)
        res["bufferbloat"] = bufferbloat_grade(idle_icmp, worst)
        res["idlePingIcmp"] = idle_icmp
        self.history.append(res)
        self.history = self.history[-200:]
        save_json(self.PATH, self.history)
        return res

    async def _download(self, job: Job, session: aiohttp.ClientSession, p0: float, p1: float) -> float:
        duration, warm = 10.0, 1.5
        total = 0
        counted = 0
        start = time.perf_counter()
        stop = start + duration

        async def worker() -> None:
            nonlocal total, counted
            while time.perf_counter() < stop and not job.cancelled:
                try:
                    async with session.get(f"{CF}/__down?bytes=25000000",
                                           timeout=aiohttp.ClientTimeout(total=duration + 5)) as r:
                        async for chunk in r.content.iter_any():
                            n = len(chunk)
                            total += n
                            if time.perf_counter() - start > warm:
                                counted += n
                            if time.perf_counter() >= stop or job.cancelled:
                                break
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    await asyncio.sleep(0.2)

        async def reporter() -> None:
            last_t, last_b = start, 0
            while time.perf_counter() < stop and not job.cancelled:
                await asyncio.sleep(0.25)
                now = time.perf_counter()
                job.data["live"] = round((total - last_b) * 8 / (now - last_t) / 1e6, 1)
                job.data.setdefault("dlSeries", []).append(job.data["live"])
                last_t, last_b = now, total
                job.progress = p0 + (p1 - p0) * min(1.0, (now - start) / duration)

        await asyncio.gather(reporter(), *(worker() for _ in range(6)))
        elapsed = max(0.1, min(time.perf_counter(), stop) - start - warm)
        mbps = round(counted * 8 / elapsed / 1e6, 1)
        job.data["download"] = mbps
        return mbps

    async def _upload(self, job: Job, session: aiohttp.ClientSession, p0: float, p1: float) -> float:
        duration, warm = 9.0, 1.0
        payload = os.urandom(25_000_000)
        sent = 0
        counted = 0
        start = time.perf_counter()
        stop = start + duration
        chunk_size = 500_000

        async def worker() -> None:
            nonlocal sent, counted, chunk_size
            while time.perf_counter() < stop and not job.cancelled:
                size = chunk_size
                t0 = time.perf_counter()
                try:
                    async with session.post(f"{CF}/__up", data=payload[:size],
                                            timeout=aiohttp.ClientTimeout(total=duration + 10)) as r:
                        await r.read()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    await asyncio.sleep(0.2)
                    continue
                took = time.perf_counter() - t0
                sent += size
                if t0 - start > warm:
                    counted += size
                # aim for ~0.5 s per request so progress stays smooth
                chunk_size = int(min(25_000_000, max(250_000, size * 0.5 / max(took, 0.01))))

        async def reporter() -> None:
            last_t, last_b = start, 0
            while time.perf_counter() < stop and not job.cancelled:
                await asyncio.sleep(0.25)
                now = time.perf_counter()
                if sent != last_b:
                    job.data["live"] = round((sent - last_b) * 8 / (now - last_t) / 1e6, 1)
                    job.data.setdefault("ulSeries", []).append(job.data["live"])
                    last_t, last_b = now, sent
                job.progress = p0 + (p1 - p0) * min(1.0, (now - start) / duration)

        await asyncio.gather(reporter(), *(worker() for _ in range(4)))
        elapsed = max(0.1, time.perf_counter() - start - warm)
        mbps = round(counted * 8 / elapsed / 1e6, 1)
        job.data["upload"] = mbps
        return mbps
