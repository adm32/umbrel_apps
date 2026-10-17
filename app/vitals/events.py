"""Timeline of things that happened: outages, reboots, drives, alerts, new devices."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import config
from .util import load_json, save_json

PATH = config.DATA / "events.json"
MAX_EVENTS = 1000

# kind -> (icon, default level)
KINDS = {
    "boot": ("refresh", "info"),
    "outage": ("world", "warning"),
    "lan_outage": ("router", "critical"),
    "drive_added": ("usb", "info"),
    "drive_removed": ("usb", "info"),
    "alert": ("alert-triangle", "warning"),
    "resolved": ("circle-check", "good"),
    "speedtest": ("brand-speedtest", "info"),
    "clean": ("brush", "good"),
    "new_device": ("radar-2", "warning"),
    "notify_error": ("bell", "warning"),
}


class Events:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = load_json(PATH, [])
        self.listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._dirty = False

    def add(self, kind: str, text: str, level: Optional[str] = None, at: Optional[float] = None, **data: Any) -> Dict[str, Any]:
        icon, default_level = KINDS.get(kind, ("info-circle", "info"))
        ev = {"at": at or time.time(), "kind": kind, "text": text, "level": level or default_level, "icon": icon}
        if data:
            ev["data"] = data
        self.items.append(ev)
        if len(self.items) > MAX_EVENTS:
            del self.items[: len(self.items) - MAX_EVENTS]
        self._dirty = True
        self.save()
        for fn in self.listeners:
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def save(self) -> None:
        if self._dirty:
            save_json(PATH, self.items)
            self._dirty = False

    def recent(self, limit: int = 50, kind: Optional[str] = None, since: Optional[float] = None) -> List[Dict[str, Any]]:
        out = [e for e in self.items if (kind is None or e["kind"] == kind) and (since is None or e["at"] >= since)]
        return list(reversed(out[-limit:]))

    def uptime(self, seconds: float, ongoing_since: Optional[float] = None,
               monitored_since: Optional[float] = None) -> Dict[str, Any]:
        """Internet availability over the last `seconds`, from recorded outages."""
        now = time.time()
        start = max(now - seconds, monitored_since or 0)
        seconds = max(1.0, now - start)
        down = 0.0
        count = 0
        for e in self.items:
            if e["kind"] not in ("outage", "lan_outage"):
                continue
            d = e.get("data") or {}
            s, en = d.get("start", e["at"]), d.get("end", e["at"])
            if en < start:
                continue
            down += max(0.0, min(en, now) - max(s, start))
            count += 1
        if ongoing_since:
            down += now - max(ongoing_since, start)
            count += 1
        return {"percent": round(100 * (1 - down / seconds), 3), "downSeconds": round(down), "outages": count}


events = Events()
