from __future__ import annotations

import copy
from typing import Any, Dict

from . import config
from .util import load_json, save_json

PATH = config.DATA / "settings.json"

CUSTOM_WIDGET_METRICS = [
    "cpu", "cpu_temp", "power", "memory", "net_down", "net_up", "net_today",
    "ping", "storage", "disk_read", "disk_write", "uptime", "load", "hottest_drive",
]

DEFAULTS: Dict[str, Any] = {
    "temp_unit": "C",              # C or F
    "currency": "€",
    "price_per_kwh": 0.30,
    "power_overhead_w": 6.0,       # added to CPU package power to estimate wall power
    "smart_plug": {"type": "none", "host": ""},  # none | shelly1 | shelly2 | tasmota
    "theme": "light",              # light | dark | auto | home
    "accent": "orange",            # orange | wallpaper
    "wallpaper": "white",          # built-in name, or an umbrelOS wallpaper id (1-25)
    "glass": "liquid",             # liquid | frosted | solid
    "wallpaper_blur": 0,           # px
    "net_reset_day": 1,            # day of month your data plan resets
    "latency_enabled": True,
    "latency_target": "1.1.1.1",
    "auto_speedtest_hours": 0,     # 0 = off
    "custom_widget": ["cpu", "cpu_temp", "power", "net_down"],
    "alerts": {"cpu_temp": 85, "disk_temp": 55, "disk_usage": 90},
}

_settings: Dict[str, Any] = {}


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    global _settings
    _settings = _merge(DEFAULTS, load_json(PATH, {}))
    return _settings


def get() -> Dict[str, Any]:
    if not _settings:
        load()
    return _settings


def update(patch: Dict[str, Any]) -> Dict[str, Any]:
    global _settings
    merged = _merge(get(), patch)
    # light validation so a bad value can't break the sampler
    merged["temp_unit"] = "F" if merged.get("temp_unit") == "F" else "C"
    merged["glass"] = merged.get("glass") if merged.get("glass") in ("liquid", "frosted", "solid") else "liquid"
    for key, lo, hi in (("price_per_kwh", 0, 10), ("power_overhead_w", 0, 200)):
        try:
            merged[key] = min(hi, max(lo, float(merged[key])))
        except (TypeError, ValueError):
            merged[key] = DEFAULTS[key]
    try:
        merged["wallpaper_blur"] = min(40, max(0, int(merged["wallpaper_blur"])))
    except (TypeError, ValueError):
        merged["wallpaper_blur"] = 0
    merged["wallpaper"] = str(merged.get("wallpaper") or "white")[:20]
    merged["theme"] = merged.get("theme") if merged.get("theme") in ("light", "dark", "auto", "home") else "light"
    merged["accent"] = "wallpaper" if merged.get("accent") == "wallpaper" else "orange"
    try:
        merged["net_reset_day"] = min(28, max(1, int(merged["net_reset_day"])))
    except (TypeError, ValueError):
        merged["net_reset_day"] = 1
    try:
        merged["auto_speedtest_hours"] = int(merged["auto_speedtest_hours"])
    except (TypeError, ValueError):
        merged["auto_speedtest_hours"] = 0
    if merged["auto_speedtest_hours"] not in (0, 1, 3, 6, 12, 24):
        merged["auto_speedtest_hours"] = 0
    cw = [m for m in merged.get("custom_widget", []) if m in CUSTOM_WIDGET_METRICS][:4]
    while len(cw) < 4:
        cw.append(DEFAULTS["custom_widget"][len(cw)])
    merged["custom_widget"] = cw
    plug = merged.get("smart_plug") or {}
    if plug.get("type") not in ("none", "shelly1", "shelly2", "tasmota"):
        plug["type"] = "none"
    plug["host"] = str(plug.get("host", ""))[:120]
    merged["smart_plug"] = plug
    _settings = merged
    save_json(PATH, merged)
    return merged
