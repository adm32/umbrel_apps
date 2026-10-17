"""Demo mode (VITALS_DEMO=1): realistic fake hardware so the UI can be
previewed on any computer. Never active on a real Umbrel."""
from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List

from . import cleaner, netlab, network, processes, sensors, smart, storage, studio

T0 = time.time()
GB = 1000 ** 3


def _wave(period: float, lo: float, hi: float, phase: float = 0) -> float:
    x = (math.sin((time.time() - T0) / period * 2 * math.pi + phase) + 1) / 2
    return lo + (hi - lo) * x + random.uniform(-(hi - lo) * 0.04, (hi - lo) * 0.04)


DRIVES = [
    {"name": "nvme0n1", "model": "Samsung SSD 970 EVO Plus 1TB", "serial": "S4EWNX0R123456", "size": 1000 * GB,
     "transport": "nvme", "kind": "NVMe SSD", "rotational": False, "removable": False, "external": False, "system": True,
     "partitions": [
         {"name": "nvme0n1p1", "size": 0.5 * GB, "label": "EFI", "fstype": "vfat", "mountpoint": "/boot/efi",
          "mountpoints": ["/boot/efi"], "readonly": False, "usage": {"total": 0.5 * GB, "used": 0.01 * GB, "free": 0.49 * GB}, "umbrel": False},
         {"name": "nvme0n1p4", "size": 990 * GB, "label": "data", "fstype": "ext4", "mountpoint": "/data",
          "mountpoints": ["/data", "/home"], "readonly": False, "usage": {"total": 974 * GB, "used": 612 * GB, "free": 362 * GB}, "umbrel": True},
     ]},
    {"name": "sda", "model": "WD Red Plus 4TB (WDC WD40EFZX)", "serial": "WD-WX12D80ABCDE", "size": 4000 * GB,
     "transport": "usb", "kind": "USB hard drive", "rotational": True, "removable": True, "external": True, "system": False,
     "partitions": [
         {"name": "sda1", "size": 4000 * GB, "label": "Samples", "fstype": "exfat", "mountpoint": "/home/umbrel/umbrel/external/Samples",
          "mountpoints": ["/home/umbrel/umbrel/external/Samples"], "readonly": False,
          "usage": {"total": 3998 * GB, "used": 2210 * GB, "free": 1788 * GB}, "umbrel": False},
     ]},
    {"name": "sdb", "model": "SanDisk Extreme Portable SSD", "serial": "32143V401234", "size": 1000 * GB,
     "transport": "usb", "kind": "USB drive", "rotational": False, "removable": True, "external": True, "system": False,
     "partitions": [
         {"name": "sdb1", "size": 1000 * GB, "label": "Projects", "fstype": "apfs", "mountpoint": "",
          "mountpoints": [], "readonly": False, "usage": None, "umbrel": False},
     ]},
]

SMART = {
    "nvme0n1": {"available": True, "standby": False, "model": "Samsung SSD 970 EVO Plus 1TB", "serial": "S4EWNX0R123456",
                "firmware": "2B2QEXM7", "protocol": "NVMe", "passed": True, "temp": 41, "powerOnHours": 11873,
                "powerCycles": 412, "percentUsed": 6, "lifeLeft": 94, "availableSpare": 100, "spareThreshold": 10,
                "mediaErrors": 0, "unsafeShutdowns": 23, "criticalWarning": 0, "written": 48.2e12, "read": 61.7e12,
                "issues": [], "notes": [], "health": "good",
                "lastSelfTest": {"type": "Short", "result": "Completed without error", "passed": True, "hours": 11820},
                "attributes": [{"name": "Temperature", "raw": 41}, {"name": "Available spare", "raw": 100},
                               {"name": "Percentage used", "raw": 6}, {"name": "Power on hours", "raw": 11873},
                               {"name": "Unsafe shutdowns", "raw": 23}, {"name": "Media errors", "raw": 0}]},
    "sda": {"available": True, "standby": False, "model": "WDC WD40EFZX-68AWUN0", "serial": "WD-WX12D80ABCDE",
            "firmware": "81.00B81", "protocol": "ATA", "rpm": 5400, "passed": True, "temp": 38, "powerOnHours": 23011,
            "powerCycles": 87, "reallocated": 8, "pending": 0, "uncorrectable": 0, "crcErrors": 2,
            "issues": ["8 reallocated sectors (the surface is wearing out)"],
            "notes": ["2 cable/connection (CRC) errors — usually a loose or bad cable, not the drive"], "health": "warning",
            "attributes": [{"id": 1, "name": "Raw Read Error Rate", "value": 200, "worst": 200, "thresh": 51, "raw": "0", "failing": False},
                           {"id": 5, "name": "Reallocated Sector Ct", "value": 199, "worst": 199, "thresh": 140, "raw": "8", "failing": False},
                           {"id": 9, "name": "Power On Hours", "value": 69, "worst": 69, "thresh": 0, "raw": "23011", "failing": False},
                           {"id": 194, "name": "Temperature Celsius", "value": 112, "worst": 99, "thresh": 0, "raw": "38", "failing": False},
                           {"id": 197, "name": "Current Pending Sector", "value": 200, "worst": 200, "thresh": 0, "raw": "0", "failing": False},
                           {"id": 199, "name": "UDMA CRC Error Count", "value": 200, "worst": 200, "thresh": 0, "raw": "2", "failing": False}]},
    "sdb": {"available": False, "standby": False, "error": "This USB enclosure doesn't pass health data through",
            "issues": [], "notes": []},
}


def apply() -> None:
    def temps() -> List[Dict[str, Any]]:
        pkg = _wave(90, 44, 63)
        out = [{"chip": "coretemp", "group": "CPU", "label": "Package id 0", "current": round(pkg, 1), "high": 80.0, "critical": 100.0}]
        for i in range(4):
            out.append({"chip": "coretemp", "group": "CPU", "label": f"Core {i}", "current": round(pkg - random.uniform(0, 5), 1),
                        "high": 80.0, "critical": 100.0})
        out += [
            {"chip": "nvme", "group": "NVMe drive", "label": "Composite", "current": round(_wave(200, 38, 44), 1), "high": 84.8, "critical": 84.8},
            {"chip": "acpitz", "group": "Motherboard (ACPI)", "label": "Temperature", "current": 27.8, "high": None, "critical": 119.0},
            {"chip": "pch_cannonlake", "group": "Chipset (PCH)", "label": "Temperature", "current": round(_wave(300, 48, 52), 1), "high": None, "critical": None},
        ]
        return out

    from . import sampler as sampler_mod

    fans = lambda: [{"chip": "dell_smm", "label": "CPU fan", "rpm": int(_wave(60, 1100, 2400))}]  # noqa: E731
    sensors.read_temps = sampler_mod.read_temps = temps  # type: ignore
    sensors.read_fans = sampler_mod.read_fans = fans  # type: ignore

    class FakeRapl:
        error = None
        zones = [1]

        def sample(self) -> Dict[str, float]:
            p = _wave(90, 3.2, 14.5)
            return {"package": round(p, 2), "cores": round(p * 0.55, 2), "graphics": 0.12}

    sampler_mod.Rapl = FakeRapl  # type: ignore

    storage.list_drives = lambda: DRIVES
    storage.umbrel_storage = lambda: {"total": 974 * GB, "used": 612 * GB, "free": 362 * GB}
    storage.writable_targets = lambda: [
        {"drive": "nvme0n1", "model": DRIVES[0]["model"], "partition": "nvme0n1p4", "mountpoint": "/data", "free": 362 * GB},
        {"drive": "sda", "model": DRIVES[1]["model"], "partition": "sda1", "mountpoint": "/home/umbrel/umbrel/external/Samples", "free": 1788 * GB},
    ]

    counters: Dict[str, List[float]] = {d["name"]: [0, 0, 0, 0, 0] for d in DRIVES}

    def io(raw: Any, names: List[str]) -> Dict[str, Any]:
        for i, n in enumerate(counters):
            c = counters[n]
            c[0] += _wave(40 + i * 7, 0, 60e6 / (i + 1)) * 2
            c[1] += _wave(55 + i * 5, 0, 25e6 / (i + 1), 1.3) * 2
            c[2] += 200
            c[3] += 120
            c[4] += _wave(30, 50, 900)
        return {n: tuple(int(x) for x in c) for n, c in counters.items()}

    storage.io_counters_by_disk = io

    async def fake_smart(dev: str, transport: str, force: bool = False) -> Dict[str, Any]:
        d = dict(SMART.get(dev, {"available": False, "issues": [], "notes": []}))
        d["at"] = time.time()
        smart._cache[dev] = d
        return d

    smart.read = fake_smart

    async def fake_selftest(dev: str) -> str:
        return "Short self-test started — it takes about 2 minutes."

    smart.self_test = fake_selftest

    async def fake_bench(job: Any, mountpoint: str, size_mb: int) -> Dict[str, Any]:
        import asyncio
        fast = mountpoint == "/data"
        for phase, n, top in (("Writing", 20, 2400 if fast else 180), ("Reading", 20, 3300 if fast else 190)):
            job.phase = phase
            for i in range(n):
                await asyncio.sleep(0.12)
                job.data["live"] = round(top * random.uniform(0.85, 1.0), 1)
                job.progress = (0 if phase == "Writing" else 0.45) + 0.45 * (i + 1) / n
        job.phase = "Random 4K reads"
        await asyncio.sleep(1)
        return {"mountpoint": mountpoint, "sizeMb": size_mb, "direct": True,
                "writeMBps": 2310.4 if fast else 176.2, "readMBps": 3120.8 if fast else 188.9,
                "randIops": 14200 if fast else 92, "randLatencyMs": 0.07 if fast else 10.8,
                "drive": "nvme0n1" if fast else "sda", "model": DRIVES[0 if fast else 1]["model"], "at": time.time()}

    storage.benchmark = fake_bench

    async def fake_analyze(job: Any) -> Dict[str, Any]:
        import asyncio
        await asyncio.sleep(1.2)
        apps = [("bitcoin", "Bitcoin Node", 712), ("immich", "Immich", 186), ("nextcloud", "Nextcloud", 74),
                ("jellyfin", "Jellyfin", 12.4), ("home-assistant", "Home Assistant", 3.1), ("adam-vitals", "Vitals", 0.01),
                ("pi-hole", "Pi-hole", 0.4), ("old-app", "Lightning Node", 9.6)]
        out = []
        for a, n, g in apps:
            out.append({"app": a, "name": n, "icon": f"https://getumbrel.github.io/umbrel-apps-gallery/{a}/icon.svg",
                        "data": g * GB, "images": random.uniform(0.2, 2.5) * GB, "installed": a != "old-app"})
        for r in out:
            r["total"] = r["data"] + r["images"]
        out.sort(key=lambda r: -r["total"])
        return {"apps": out, "other": {"Home (your files)": 48 * GB, "Backups": 0, "Trash": 1.3 * GB}, "at": time.time()}

    storage.analyze_apps = fake_analyze

    async def fake_ifaces() -> List[Dict[str, Any]]:
        return [
            {"name": "enp0s31f6", "kind": "ethernet", "default": True, "speedMbps": 1000, "duplex": "full",
             "mac": "a4:bb:6d:12:34:56", "mtu": 1500, "state": "up", "ipv4": ["192.168.1.50/24"],
             "ipv6": ["fe80::a6bb:6dff:fe12:3456/64"]},
            {"name": "wlp1s0", "kind": "wifi", "default": False, "speedMbps": None, "duplex": None,
             "mac": "3c:6a:a7:ab:cd:ef", "mtu": 1500, "state": "down", "ipv4": [], "ipv6": [],
             "wifi": {}},
            {"name": "tailscale0", "kind": "vpn", "default": False, "speedMbps": None, "duplex": None,
             "mac": None, "mtu": 1280, "state": "unknown", "ipv4": ["100.101.12.34/32"], "ipv6": []},
        ]

    network.interfaces = fake_ifaces
    netlab.interfaces = fake_ifaces
    network.default_route = lambda: ("enp0s31f6", None)
    network.dns_servers = lambda: ["192.168.1.1", "1.1.1.1"]
    network.local_subnet = netlab.local_subnet = lambda: "192.168.1.0/24"  # type: ignore
    netlab.default_route = network.default_route  # type: ignore

    async def fake_lan(job: Any, subnet: Any) -> Dict[str, Any]:
        import asyncio
        job.data["command"] = "nmap -sn 192.168.1.0/24"
        for i in range(10):
            await asyncio.sleep(0.25)
            job.progress = (i + 1) / 10
            job.phase = "Discovering devices"
        devs = [
            ("192.168.1.1", "FRITZ!Box 7590", "AVM GmbH", True, False),
            ("192.168.1.20", "Adams-MacBook-Pro", "Apple", False, False),
            ("192.168.1.21", "iPhone", "Apple", False, False),
            ("192.168.1.30", "focusrite-dante", "Focusrite Audio Engineering", False, False),
            ("192.168.1.40", "HP-LaserJet", "HP", False, False),
            ("192.168.1.50", "umbrel", "This Umbrel", False, True),
            ("192.168.1.60", "Living-Room-TV", "Samsung Electronics", False, False),
            ("192.168.1.77", None, "Espressif (IoT)", False, False),
        ]
        from .devices import devices as registry
        out = [{"ip": ip, "name": n, "vendor": v, "mac": "A4:83:E7:%02X:%02X:%02X" % (i, i * 7 % 256, i * 13 % 256),
                "latency": round(random.uniform(0.3, 9), 2), "router": r, "self": me}
               for i, (ip, n, v, r, me) in enumerate(devs)]
        new = registry.merge(out)
        return {"subnet": "192.168.1.0/24", "at": time.time(), "devices": out, "new": len(new)}

    netlab.lan_scan = fake_lan

    import vitals.devices as devmod

    async def fake_wake(mac: str, subnet: Any) -> str:
        devmod.norm_mac(mac)
        return f"Wake-up packet sent to {devmod.norm_mac(mac)}"

    devmod.wake = fake_wake

    from .events import events
    if not events.items:
        now = time.time()
        events.add("boot", "Your Umbrel restarted", at=now - 86400 * 3)
        events.add("drive_added", "WD Red Plus 4TB (4.00 TB) was connected", at=now - 86400 * 2)
        events.add("outage", "The internet was down for 4 minutes", at=now - 86400 * 1.2,
                   start=now - 86400 * 1.2 - 240, end=now - 86400 * 1.2, seconds=240)
        events.add("speedtest", "Speed test: 942 Mb/s down, 41 Mb/s up, 11 ms ping", at=now - 3600 * 20)
        events.add("new_device", "New device joined your network: Espressif (192.168.1.77)", at=now - 3600 * 6)
        events.add("clean", "Cleaner freed 3.2 GB", at=now - 3600 * 2)

    async def fake_listening() -> List[Dict[str, Any]]:
        rows = [("tcp", 22, "sshd", None), ("tcp", 80, "umbreld", "umbrelOS"), ("tcp", 443, "umbreld", "umbrelOS"),
                ("tcp", 445, "smbd", "umbrelOS file sharing"), ("tcp", 2100, "umbreld", "Bitcoin Node"),
                ("tcp", 8333, "docker-proxy", "Bitcoin Node"), ("tcp", 7468, "umbreld", "Vitals"),
                ("tcp", 2283, "umbreld", "Immich"), ("udp", 5353, "avahi-daemon", None), ("udp", 41641, "tailscaled", "Tailscale"),
                ("tcp", 8096, "umbreld", "Jellyfin"), ("tcp", 9050, "tor", None)]
        return [{"proto": p, "port": port, "addresses": ["0.0.0.0"], "process": proc, "pid": 1000 + port,
                 "app": app, "scope": "local only" if port == 9050 else "network",
                 "service": netlab.SERVICE_HINTS.get(port, "")} for p, port, proc, app in rows]

    netlab.listening = fake_listening

    fake_apps = [("bitcoin", "Bitcoin Node"), ("immich", "Immich"), ("nextcloud", "Nextcloud"),
                 ("jellyfin", "Jellyfin"), ("home-assistant", "Home Assistant"), ("adam-vitals", "Vitals")]
    orig_sample = processes.ProcessMonitor.sample

    async def sample(self: Any, total_mem: int) -> None:
        await orig_sample(self, total_mem)
        for r in self.rows:
            k = r["pid"] % 9
            if k < len(fake_apps):
                r["app"], r["appName"] = fake_apps[k]
                r["container"] = f"{fake_apps[k][0]}_app_1"
        groups: Dict[str, Dict[str, Any]] = {}
        for r in self.rows:
            key = r["app"] or r["appName"]
            g = groups.setdefault(key, {"app": r["app"], "name": r["appName"], "cpu": 0.0, "mem": 0, "procs": 0,
                                        "icon": f"https://getumbrel.github.io/umbrel-apps-gallery/{r['app']}/icon.svg" if r["app"] not in ("", "system") else "",
                                        "containers": []})
            g["cpu"] = round(g["cpu"] + r["cpu"], 1)
            g["mem"] += r["mem"]
            g["procs"] += 1
        for g in groups.values():
            g["cpuSystem"] = round(g["cpu"] / self.ncpu, 1)
        self.apps = sorted(groups.values(), key=lambda g: -g["cpu"])

    processes.ProcessMonitor.sample = sample  # type: ignore

    async def fake_scan(self: Any, job: Any) -> Dict[str, Any]:
        import asyncio
        for i, label in enumerate(["Docker images", "build cache", "container logs", "system journal",
                                   "old log files", "temporary files", "leftover app data", "junk files"]):
            job.phase = f"Checking {label}…"
            job.progress = i / 8
            await asyncio.sleep(0.35)

        def item(label: str, size: float, detail: str) -> Dict[str, Any]:
            iid = cleaner._uid()
            return {"id": iid, "label": label, "size": int(size), "detail": detail}

        cats = [
            self._category("images", "Unused app images", "Old app versions and images left behind by uninstalled apps. Apps you still have installed — even stopped ones — are never touched.",
                           "brand-docker", "safe", True,
                           [item("ghcr.io/immich-app/immich-server:v1.118.2", 1.42 * GB, "Not used by any installed app"),
                            item("lncm/lnd:v0.17.4 (old version)", 0.61 * GB, "Not used by any installed app"),
                            item("Untagged layer", 0.18 * GB, "Not used by any installed app")]),
            self._category("buildcache", "Docker build cache", "Temporary files from building Docker images.", "box", "safe", True, []),
            self._category("containers", "Leftover containers", "Stopped containers from apps that are no longer installed.", "box-multiple", "safe", True,
                           [item("lightning_lnd_1", 0.02 * GB, "From 'lightning', which isn't installed")]),
            self._category("logs", "Oversized app logs", "Log files from apps that have grown large. They're emptied, not deleted — apps keep logging normally.",
                           "file-text", "safe", True,
                           [item("bitcoin_app_1", 0.34 * GB, "Container log file"), item("immich_server_1", 0.09 * GB, "Container log file")]),
            self._category("journal", "System journal", "umbrelOS's own log history. The newest 100 MB is kept.", "notebook", "safe", True,
                           [item("System journal", 0.62 * GB, "720 MB now — trimmed to the newest 100 MB")]),
            self._category("rotated", "Old log files", "Rotated and compressed logs in /var/log that are no longer written to.", "file-zip", "safe", True,
                           [item("214 old log files", 0.087 * GB, "syslog.4.gz, kern.log.2.gz, daemon.log.1")]),
            self._category("tmp", "Old temporary files", "Files in the system's temp folders untouched for 10+ days.", "clock-hour-4", "safe", True,
                           [item("37 temporary files", 0.21 * GB, "update-cache.tar, tmp9x2k1, npm-1234.log")]),
            self._category("appdata", "Leftover app data", "Data folders from apps that are no longer installed. Deleting is permanent — if you plan to reinstall an app, keep its data.",
                           "folder-x", "careful", False, [item("Lightning Node", 9.6 * GB, "app-data/lightning — this app isn't installed")]),
            self._category("junk", "Mac & Windows junk files", "Tiny hidden files computers leave behind when you copy things onto your Umbrel.", "brand-apple", "safe", True,
                           [item(".DS_Store files (1,284)", 0.009 * GB, "Folder view settings macOS leaves on shared drives"),
                            item("AppleDouble ._ files (18,920)", 0.077 * GB, "Hidden metadata copies macOS writes next to files on non-Mac drives"),
                            item("Spotlight & fsevents folders (2)", 0.41 * GB, "macOS search indexes on external drives — rebuilt automatically")]),
            self._category("trash", "Umbrel Files trash", "Files you deleted in the Files app. Empty it from Files → Trash so umbrelOS stays in sync.",
                           "trash", "info", False, [{"id": "trash", "label": "Trash", "size": int(1.3 * GB), "detail": "Open Files → Trash → Empty trash"}], info_only=True),
        ]
        for c in cats:
            for it in c["items"]:
                if c["id"] != "trash":
                    self._register(c["id"], it["id"], it["size"], lambda: 0)
        self.scan_result = {"id": cleaner._uid(), "at": time.time(), "categories": cats,
                            "total": sum(c["size"] for c in cats if not c["infoOnly"]), "stats": self.stats, "skipped": []}
        return self.scan_result

    cleaner.Cleaner.scan = fake_scan  # type: ignore

    studio.roots = lambda: [{"label": "Home", "path": "/demo/home"}, {"label": "External drives", "path": "/demo/external"}]

    def fake_browse(path: Any) -> Dict[str, Any]:
        tree = {
            "": [("Home", "/demo/home"), ("External drives", "/demo/external")],
            "/demo/home": [("Music", "/demo/home/Music"), ("Documents", "/demo/home/Documents"), ("Downloads", "/demo/home/Downloads")],
            "/demo/external": [("Samples", "/demo/external/Samples")],
            "/demo/external/Samples": [("Splice", "/demo/external/Samples/Splice"), ("Drum Kits", "/demo/external/Samples/Drum Kits"),
                                       ("Ableton Projects", "/demo/external/Samples/Ableton Projects")],
        }
        p = path or ""
        disp = p.replace("/demo/home", "Home").replace("/demo/external", "External")
        parent = p.rsplit("/", 1)[0] if p.count("/") > 2 else ""
        return {"path": p, "display": disp, "parent": parent if p else None,
                "dirs": [{"name": n, "path": q} for n, q in tree.get(p, [])]}

    studio.browse = fake_browse

    async def fake_studio_scan(self: Any, job: Any, path: str) -> Dict[str, Any]:
        import asyncio
        for i in range(12):
            await asyncio.sleep(0.2)
            job.phase = f"Scanned {(i + 1) * 4211:,} files"
        dup = []
        for name, size, paths in (
            ("Kick 01 - Punchy.wav", 412_000, ["External/Samples/Splice/Kicks/Kick 01 - Punchy.wav", "External/Samples/Drum Kits/Trap Kit/Kick 01 - Punchy.wav", "Home/Downloads/kit/Kick 01 - Punchy.wav"]),
            ("Vox Chop 128bpm Am.wav", 5_290_000, ["External/Samples/Splice/Vocals/Vox Chop 128bpm Am.wav", "Home/Music/Bounces/Vox Chop 128bpm Am.wav"]),
            ("808 Long C.wav", 1_870_000, ["External/Samples/Drum Kits/808s/808 Long C.wav", "External/Samples/Splice/808 Long C.wav"]),
        ):
            copies = []
            for p in paths:
                cid = cleaner._uid()
                self._dupe_paths[cid] = p
                copies.append({"id": cid, "path": p})
            dup.append({"name": name, "size": size, "copies": copies, "wasted": size * (len(paths) - 1)})
        backups = []
        for i in range(6):
            bid = cleaner._uid()
            self._backup_paths[bid] = f"b{i}"
            backups.append({"id": bid, "path": f"External/Samples/Ableton Projects/Night Drive Project/Backup/Night Drive [2026-0{i + 1}-12 2104{i}].als",
                            "size": 380_000 + i * 2000, "kind": "DAW backup"})
        res = {
            "root": "External/Samples", "files": 50532, "audioFiles": 46120, "audioBytes": 412 * GB, "hours": 318.4,
            "formats": [{"ext": "wav", "count": 39880, "size": 371 * GB}, {"ext": "aif", "count": 3110, "size": 28 * GB},
                        {"ext": "mp3", "count": 2410, "size": 9.2 * GB}, {"ext": "flac", "count": 720, "size": 3.8 * GB}],
            "sampleRates": [["44.1 kHz", 31200], ["48 kHz", 10840], ["96 kHz", 950]],
            "bitDepths": [["24-bit", 30120], ["16-bit", 11870], ["32-bit", 1000]],
            "largest": [{"path": "External/Samples/Ableton Projects/Live Set 2025/Recorded/Take 1.wav", "size": 1.9 * GB},
                        {"path": "Home/Music/Bounces/Album Master 96k.wav", "size": 1.2 * GB}],
            "duplicates": dup, "duplicateWaste": sum(d["wasted"] for d in dup),
            "projects": [{"name": "Night Drive", "daw": "Ableton Live", "path": "External/Samples/Ableton Projects/Night Drive Project/Night Drive.als", "modified": time.time() - 86400 * 2, "size": 420_000},
                         {"name": "Summer Bounce", "daw": "FL Studio", "path": "Home/Music/FL/Summer Bounce.flp", "modified": time.time() - 86400 * 9, "size": 210_000},
                         {"name": "Demo 14", "daw": "Logic Pro", "path": "Home/Music/Logic/Demo 14.logicx", "modified": time.time() - 86400 * 31}],
            "projectCount": 3, "backups": backups, "backupBytes": sum(b["size"] for b in backups),
            "seconds": 2.6, "at": time.time(),
        }
        self.last = res
        return res

    studio.Studio.scan = fake_studio_scan  # type: ignore
    studio.Studio.delete = lambda self, ids: {"freed": 0, "removed": len(ids), "errors": []}  # type: ignore
