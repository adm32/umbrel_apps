"""JSON for the umbrelOS home-screen widgets (see widgets: in umbrel-app.yml).

umbreld fetches these directly from the container (not through the app
proxy), so they must stay small, fast and free of anything sensitive.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import settings, smart

DASH = "–"


def _temp(c: Optional[float]) -> Tuple[str, str]:
    if c is None:
        return DASH, ""
    if settings.get()["temp_unit"] == "F":
        return f"{c * 9 / 5 + 32:.0f}", "°F"
    return f"{c:.0f}", "°C"


def _rate(bytes_per_s: Optional[float]) -> Tuple[str, str]:
    if bytes_per_s is None:
        return DASH, ""
    bits = bytes_per_s * 8
    if bits >= 1e9:
        return f"{bits / 1e9:.2f}", "Gb/s"
    if bits >= 1e6:
        return f"{bits / 1e6:.1f}", "Mb/s"
    if bits >= 1e3:
        return f"{bits / 1e3:.0f}", "kb/s"
    return f"{bits:.0f}", "b/s"


def _bytes(n: Optional[float]) -> Tuple[str, str]:
    if n is None:
        return DASH, ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return (f"{n:.0f}" if unit in ("B", "KB") or n >= 100 else f"{n:.1f}"), unit
        n /= 1000.0
    return DASH, ""


def _disk_rate(bps: Optional[float]) -> Tuple[str, str]:
    t, u = _bytes(bps)
    return t, (u + "/s") if u else ""


def _uptime(sec: Optional[float]) -> Tuple[str, str]:
    if not sec:
        return DASH, ""
    d = int(sec // 86400)
    if d >= 1:
        return str(d), "days" if d != 1 else "day"
    h = int(sec // 3600)
    return (str(h), "hours") if h >= 1 else (str(int(sec // 60)), "min")


def metric(key: str, snap: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, str]:
    cpu = snap.get("cpu") or {}
    mem = snap.get("memory") or {}
    net = snap.get("net") or {}
    p = snap.get("power") or {}
    st = snap.get("storage") or {}
    if key == "cpu":
        return {"title": "CPU", "text": f"{cpu.get('total', 0):.0f}", "subtext": "%"}
    if key == "cpu_temp":
        t, u = _temp(cpu.get("temp"))
        return {"title": "CPU temp", "text": t, "subtext": u}
    if key == "power":
        w = p.get("wall")
        return {"title": "Power", "text": f"{w:.1f}" if w is not None else DASH, "subtext": "W" if w is not None else ""}
    if key == "memory":
        return {"title": "Memory", "text": f"{mem.get('percent', 0):.0f}", "subtext": "%"}
    if key == "net_down":
        t, u = _rate(net.get("rx"))
        return {"title": "Download", "text": t, "subtext": u}
    if key == "net_up":
        t, u = _rate(net.get("tx"))
        return {"title": "Upload", "text": t, "subtext": u}
    if key == "net_today":
        today = ctx.get("today") or [0, 0]
        t, u = _bytes(today[0] + today[1])
        return {"title": "Data today", "text": t, "subtext": u}
    if key == "ping":
        ms = snap.get("ping")
        return {"title": "Ping", "text": f"{ms:.0f}" if ms is not None else DASH, "subtext": "ms" if ms is not None else ""}
    if key == "storage":
        if st.get("total"):
            return {"title": "Storage", "text": f"{100 * st['used'] / st['total']:.0f}", "subtext": "% used"}
        return {"title": "Storage", "text": DASH, "subtext": ""}
    if key == "disk_read":
        t, u = _disk_rate(sum(d.get("read", 0) for d in (snap.get("disks") or {}).values()))
        return {"title": "Disk read", "text": t, "subtext": u}
    if key == "disk_write":
        t, u = _disk_rate(sum(d.get("write", 0) for d in (snap.get("disks") or {}).values()))
        return {"title": "Disk write", "text": t, "subtext": u}
    if key == "uptime":
        t, u = _uptime(snap.get("uptime"))
        return {"title": "Uptime", "text": t, "subtext": u}
    if key == "load":
        load = cpu.get("load") or [0]
        return {"title": "Load", "text": f"{load[0]:.2f}", "subtext": "1 min"}
    if key == "hottest_drive":
        temps = [(smart.cached(n) or {}).get("temp") for n in ctx.get("drives", [])]
        temps = [t for t in temps if t is not None]
        t, u = _temp(max(temps) if temps else None)
        return {"title": "Hottest drive", "text": t, "subtext": u}
    return {"title": key, "text": DASH, "subtext": ""}


def build(widget_id: str, snap: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if widget_id == "system":
        return {"type": "four-stats", "refresh": "5s", "link": "",
                "items": [metric(k, snap, ctx) for k in ("cpu", "cpu_temp", "power", "memory")]}

    if widget_id == "custom":
        keys = settings.get()["custom_widget"]
        return {"type": "four-stats", "refresh": "5s", "link": "",
                "items": [metric(k, snap, ctx) for k in keys]}

    if widget_id == "network":
        return {"type": "four-stats", "refresh": "5s", "link": "/#network",
                "items": [metric(k, snap, ctx) for k in ("net_down", "net_up", "net_today", "ping")]}

    if widget_id == "power":
        p = snap.get("power") or {}
        w = p.get("wall")
        cost = p.get("monthlyCost")
        label = f"≈ {p.get('currency', '')}{cost:.2f} per month" if cost is not None else "No power sensor found"
        cpu_w = p.get("package")
        return {"type": "text-with-progress", "refresh": "5s", "link": "/#sensors",
                "title": "Power draw" + (" (smart plug)" if p.get("source") == "plug" else ""),
                "text": f"{w:.1f}" if w is not None else DASH, "subtext": " W" if w is not None else "",
                "progressLabel": label + (f" · CPU {cpu_w:.1f} W" if cpu_w is not None else ""),
                "progress": min(1.0, (w or 0) / 65.0)}

    if widget_id == "thermals":
        cpu_t = (snap.get("cpu") or {}).get("temp")
        drives = []
        for n in ctx.get("drives", []):
            t = (smart.cached(n) or {}).get("temp")
            if t is not None:
                drives.append(t)
        hot = max(drives) if drives else None
        a, au = _temp(cpu_t)
        b, bu = _temp(hot)
        return {"type": "two-stats-with-guage", "refresh": "10s", "link": "/#sensors",
                "items": [
                    {"title": "CPU", "text": a, "subtext": au, "progress": min(1.0, (cpu_t or 0) / 100)},
                    {"title": "Hottest drive", "text": b, "subtext": bu, "progress": min(1.0, (hot or 0) / 70)},
                ]}

    if widget_id == "drives":
        items: List[Dict[str, str]] = []
        for d in ctx.get("driveList", []):
            sm = smart.cached(d["name"]) or {}
            health = {"good": "Healthy", "warning": "Needs attention", "critical": "Failing"}.get(sm.get("health", ""), "")
            if sm.get("standby"):
                health = "Asleep"
            parts = [health] if health else []
            if sm.get("temp") is not None:
                t, u = _temp(sm["temp"])
                parts.append(f"{t}{u}")
            if d.get("fsTotal"):
                parts.append(f"{100 * d['used'] / d['fsTotal']:.0f}% full")
            items.append({"text": d["model"][:40], "subtext": " · ".join(parts) or d["kind"]})
        return {"type": "list", "refresh": "1m", "link": "/#storage", "items": items[:4],
                "noItemsText": "No drives found"}

    if widget_id == "speedtest":
        last = ctx.get("speedtest")
        if not last:
            return {"type": "three-stats", "refresh": "1m", "link": "/#network",
                    "items": [{"icon": "arrow-down", "subtext": "Download", "text": DASH},
                              {"icon": "arrow-up", "subtext": "Upload", "text": DASH},
                              {"icon": "activity-heartbeat", "subtext": "No test yet", "text": DASH}]}
        def mbps(v: Optional[float]) -> str:
            if v is None:
                return DASH
            return f"{v / 1000:.2f} Gb/s" if v >= 1000 else f"{v:.0f} Mb/s"
        return {"type": "three-stats", "refresh": "1m", "link": "/#network",
                "items": [{"icon": "arrow-down", "subtext": "Download", "text": mbps(last.get("download"))},
                          {"icon": "arrow-up", "subtext": "Upload", "text": mbps(last.get("upload"))},
                          {"icon": "activity-heartbeat", "subtext": "Ping", "text": f"{last.get('ping', 0):.0f} ms"}]}

    if widget_id == "apps":
        apps = [a for a in ctx.get("apps", []) if a.get("app") not in ("", None)]
        items = []
        for a in apps[:4]:
            t, u = _bytes(a["mem"])
            items.append({"text": a["name"], "subtext": f"{a['cpuSystem']:.1f}% CPU · {t} {u}"})
        return {"type": "list", "refresh": "5s", "link": "/#activity", "items": items,
                "noItemsText": "Measuring…"}

    return None

