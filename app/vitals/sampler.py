"""The live sampler: one loop that reads every sensor and keeps history."""
from __future__ import annotations

import asyncio
import os
import platform
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

import aiohttp
import psutil

from . import config, settings, smart, storage
from .network import LatencyMonitor, UsageTracker, read_counters, iface_kind
from .sensors import Rapl, SmartPlug, cpu_temp, power_summary, read_fans, read_temps
from .util import host_read_text, load_json, read_text, save_json

HISTORY_PATH = config.DATA / "history.json"
SERIES_KEYS = ("cpu", "mem", "temp", "power", "rx", "tx", "dr", "dw")


def cpu_model() -> str:
    raw = read_text(os.path.join(config.PROC, "cpuinfo")) or ""
    for line in raw.splitlines():
        if line.lower().startswith(("model name", "hardware", "processor\t: arm")) and ":" in line:
            return " ".join(line.split(":", 1)[1].split())
    return platform.processor() or platform.machine()


def device_model() -> str:
    vendor = (read_text(config.host_path("/sys/class/dmi/id/sys_vendor")) or "").strip()
    product = (read_text(config.host_path("/sys/class/dmi/id/product_name")) or "").strip()
    if not product:
        product = (read_text(config.host_path("/proc/device-tree/model")) or "").strip("\x00 ")
    junk = ("to be filled", "default string", "system product name", "o.e.m", "none")
    if any(j in product.lower() for j in junk):
        product = ""
    if vendor and product and vendor.lower().split()[0] not in product.lower():
        return f"{vendor} {product}"
    return product or vendor


def os_name() -> str:
    raw = host_read_text("/etc/os-release") or ""
    for line in raw.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return platform.system()


class Sampler:
    def __init__(self) -> None:
        self.rapl = Rapl()
        self.plug = SmartPlug()
        self.usage = UsageTracker()
        self.latency = LatencyMonitor()
        self.short: Deque[Dict[str, Any]] = deque(maxlen=180)  # 6 minutes at 2 s
        stored = load_json(HISTORY_PATH, [])
        self.long: Deque[Dict[str, Any]] = deque(stored[-1440:], maxlen=1440)  # 24 h of minutes
        self.disk_short: Dict[str, Deque[List[float]]] = {}
        self._acc: Dict[str, float] = {}
        self._acc_n = 0
        self._acc_minute = int(time.time() // 60)
        self._prev_disk: Dict[str, Any] = {}
        self._prev_net: Dict[str, Any] = {}
        self._prev_t = 0.0
        self.drive_names: List[str] = []
        self.drives: List[Dict[str, Any]] = []
        self._drives_at = float("-inf")
        self._drives_loaded = False
        from .events import events
        self.events = events
        self._check_boot()
        self.snapshot: Dict[str, Any] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.static = {
            "cpuModel": cpu_model(),
            "physical": psutil.cpu_count(logical=False) or psutil.cpu_count(),
            "logical": psutil.cpu_count(),
            "device": device_model(),
            "os": os_name(),
            "kernel": platform.release(),
            "hostname": (host_read_text("/etc/hostname") or platform.node()).strip(),
            "arch": platform.machine(),
        }
        psutil.cpu_percent(percpu=True)
        psutil.cpu_times_percent()

    def _check_boot(self) -> None:
        """Log a timeline event when the Umbrel itself restarted."""
        path = config.DATA / "boot.json"
        boot = round(psutil.boot_time())
        last = load_json(path, {}).get("boot")
        if last and abs(last - boot) > 120:
            self.events.add("boot", "Your Umbrel restarted", at=boot)
        if last != boot:
            save_json(path, {"boot": boot})

    # ------------------------------------------------------------ drives

    def refresh_drives(self, force: bool = False) -> List[Dict[str, Any]]:
        """Cached drive list; update_drives() keeps it fresh without blocking."""
        return self.drives

    async def update_drives(self, force: bool = False) -> List[Dict[str, Any]]:
        if not force and time.monotonic() - self._drives_at < 30:
            return self.drives
        self._drives_at = time.monotonic()
        loop = asyncio.get_running_loop()
        try:
            # statvfs on a drive that's spinning up can take seconds: keep it off the event loop
            drives = await asyncio.wait_for(loop.run_in_executor(None, storage.list_drives), 30)
        except (asyncio.TimeoutError, OSError):
            return self.drives
        self._diff_drives(drives)
        self.drives = drives
        self.drive_names = [d["name"] for d in drives]
        return drives

    def _diff_drives(self, drives: List[Dict[str, Any]]) -> None:
        def key(d: Dict[str, Any]) -> str:
            return f"{d['model']}|{d.get('serial', '')}|{d['size']}"
        new = {key(d): d for d in drives}
        if self._drives_loaded:
            old = {key(d): d for d in self.drives}
            for k, d in new.items():
                if k not in old:
                    self.events.add("drive_added", f"{d['model']} ({_size(d['size'])}) was connected", drive=d["name"])
            for k, d in old.items():
                if k not in new:
                    self.events.add("drive_removed", f"{d['model']} was disconnected", drive=d["name"])
        self._drives_loaded = True

    async def refresh_smart(self, force: bool = False) -> None:
        for d in await self.update_drives():
            await smart.read(d["name"], d["transport"], force=force)

    # ------------------------------------------------------------ one sample

    async def sample(self) -> None:
        now = time.time()
        mono = time.monotonic()
        dt = mono - self._prev_t if self._prev_t else 0.0
        self._prev_t = mono
        s = settings.get()

        cores = psutil.cpu_percent(percpu=True)
        total = round(sum(cores) / len(cores), 1) if cores else 0.0
        times = psutil.cpu_times_percent()
        freqs: List[float] = []
        try:
            fl = psutil.cpu_freq(percpu=True) or []
            freqs = [round(f.current) for f in fl]
            fmax = max((f.max for f in fl), default=0) or None
        except (AttributeError, NotImplementedError, OSError):
            fmax = None
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = [0, 0, 0]

        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        cached = getattr(vm, "cached", 0) + getattr(vm, "buffers", 0)
        mem_used = vm.total - vm.available

        temps = read_temps()
        ctemp = cpu_temp(temps)

        if self.session:
            await self.plug.poll(self.session)
        power = power_summary(self.rapl.sample(), self.plug, self.rapl.error)

        # disks
        await self.update_drives()
        disks: Dict[str, Dict[str, float]] = {}
        try:
            raw = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            raw = {}
        names = self.drive_names or [n for n in raw if not n.startswith(("loop", "ram", "zram", "dm-"))]
        cur = storage.io_counters_by_disk(raw, names)
        dr_sum = dw_sum = 0.0
        for name, c in cur.items():
            p = self._prev_disk.get(name)
            if p and dt > 0:
                r = max(0, c[0] - p[0]) / dt
                w = max(0, c[1] - p[1]) / dt
                disks[name] = {
                    "read": round(r), "write": round(w),
                    "readIops": round(max(0, c[2] - p[2]) / dt, 1),
                    "writeIops": round(max(0, c[3] - p[3]) / dt, 1),
                    "busy": round(min(100.0, max(0, c[4] - p[4]) / (dt * 10)), 1),
                }
                dr_sum += r
                dw_sum += w
                dq = self.disk_short.setdefault(name, deque(maxlen=90))
                dq.append([round(now), round(r), round(w)])
        self._prev_disk = cur

        # network
        counters = read_counters()
        self.usage.add(counters)
        ifaces: Dict[str, Dict[str, Any]] = {}
        rx_sum = tx_sum = 0.0
        for name, c in counters.items():
            p = self._prev_net.get(name)
            kind = iface_kind(name)
            if p and dt > 0 and kind != "virtual":
                rx = max(0, c[0] - p[0]) / dt
                tx = max(0, c[1] - p[1]) / dt
                ifaces[name] = {"rx": round(rx), "tx": round(tx), "kind": kind,
                                "rxTotal": c[0], "txTotal": c[1]}
                if kind in ("ethernet", "wifi"):
                    rx_sum += rx
                    tx_sum += tx
        self._prev_net = counters

        ust = storage.umbrel_storage()
        lat = self.latency.series.get("internet")
        ping = lat[-1][1] if lat else None

        snap = {
            "t": now,
            "uptime": now - psutil.boot_time(),
            "cpu": {
                "total": total, "cores": cores,
                "user": round(times.user, 1), "system": round(times.system, 1),
                "iowait": round(getattr(times, "iowait", 0.0), 1),
                "freq": round(sum(freqs) / len(freqs)) if freqs else None,
                "freqs": freqs, "freqMax": fmax, "load": load,
                "temp": ctemp,
                **{k: self.static[k] for k in ("cpuModel", "physical", "logical")},
            },
            "memory": {
                "total": vm.total, "used": mem_used, "available": vm.available,
                "cached": cached, "percent": round(100 * mem_used / vm.total, 1),
                "swapTotal": sw.total, "swapUsed": sw.used,
            },
            "temps": temps,
            "fans": read_fans(),
            "power": power,
            "disks": disks,
            "net": {"ifaces": ifaces, "rx": round(rx_sum), "tx": round(tx_sum)},
            "storage": ust,
            "online": self.latency.online,
            "ping": ping,
        }
        snap["alerts"] = self._alerts(snap, s)
        self.snapshot = snap

        point = {
            "t": round(now), "cpu": total, "mem": snap["memory"]["percent"], "temp": ctemp,
            "power": power["wall"], "rx": round(rx_sum), "tx": round(tx_sum),
            "dr": round(dr_sum), "dw": round(dw_sum),
        }
        if dt > 0:
            self.short.append(point)
            self._accumulate(point)

    def _accumulate(self, point: Dict[str, Any]) -> None:
        minute = int(point["t"] // 60)
        if minute != self._acc_minute and self._acc_n:
            avg = {"t": self._acc_minute * 60}
            for k in SERIES_KEYS:
                n = self._acc.get(k + "_n", 0)
                avg[k] = round(self._acc.get(k, 0) / n, 1) if n else None
            self.long.append(avg)
            self._acc, self._acc_n = {}, 0
        self._acc_minute = minute
        self._acc_n += 1
        for k in SERIES_KEYS:
            v = point.get(k)
            if v is not None:
                self._acc[k] = self._acc.get(k, 0) + v
                self._acc[k + "_n"] = self._acc.get(k + "_n", 0) + 1

    def _alerts(self, snap: Dict[str, Any], s: Dict[str, Any]) -> List[Dict[str, str]]:
        a = s["alerts"]
        out = []
        t = snap["cpu"]["temp"]
        if t is not None and t >= a["cpu_temp"]:
            out.append({"id": "cpu_temp", "level": "warning", "page": "sensors", "text": f"CPU is running hot ({t:.0f}°C)"})
        st = snap["storage"]
        if st and st["total"] and 100 * st["used"] / st["total"] >= a["disk_usage"]:
            pct = 100 * st["used"] / st["total"]
            out.append({"id": "storage_full", "level": "warning", "page": "cleaner", "text": f"Storage is {pct:.0f}% full — the cleaner can help"})
        for d in self.drives:
            sm = smart.cached(d["name"])
            if not sm:
                continue
            if sm.get("health") == "critical":
                out.append({"id": f"drive_critical:{d['model']}", "level": "critical", "page": "storage", "text": f"{d['model']}: {sm['issues'][0] if sm['issues'] else 'health is critical'}"})
            elif sm.get("health") == "warning":
                out.append({"id": f"drive_warning:{d['model']}", "level": "warning", "page": "storage", "text": f"{d['model']}: {sm['issues'][0] if sm['issues'] else 'needs attention'}"})
            if sm.get("temp") and sm["temp"] >= a["disk_temp"] and sm.get("health") == "good":
                out.append({"id": f"drive_hot:{d['model']}", "level": "warning", "page": "storage", "text": f"{d['model']} is hot ({sm['temp']}°C)"})
        if snap["memory"]["percent"] > 95:
            out.append({"id": "memory", "level": "warning", "page": "activity", "text": "Memory is almost full"})
        if snap["online"] is False:
            out.append({"id": "offline", "level": "critical", "page": "network", "text": "Your Umbrel can't reach the internet"})
        return out

    def save(self) -> None:
        save_json(HISTORY_PATH, list(self.long))
        self.usage.save()

    def history(self, rng: str) -> List[Dict[str, Any]]:
        if rng == "24h":
            return list(self.long)
        if rng == "1h":
            return list(self.long)[-60:]
        return list(self.short)


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1000.0
    return ""
