"""Temperatures, fans and power (Intel/AMD RAPL + optional smart plug)."""
from __future__ import annotations

import glob
import os
import time
from typing import Any, Dict, List, Optional

import aiohttp
import psutil

from . import config, settings
from .util import read_int, read_text

# Friendly names for common hwmon chips
CHIP_NAMES = {
    "coretemp": "CPU",
    "k10temp": "CPU",
    "zenpower": "CPU",
    "cpu_thermal": "CPU",
    "nvme": "NVMe drive",
    "drivetemp": "Drive",
    "acpitz": "Motherboard (ACPI)",
    "pch_cannonlake": "Chipset (PCH)",
    "pch_skylake": "Chipset (PCH)",
    "pch_cometlake": "Chipset (PCH)",
    "iwlwifi_1": "Wi-Fi card",
    "iwlwifi": "Wi-Fi card",
    "amdgpu": "GPU",
    "i915": "GPU",
    "x86_pkg_temp": "CPU package",
    "soc_thermal": "SoC",
    "rp1_adc": "RP1",
}


def read_temps() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        temps = psutil.sensors_temperatures()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        temps = {}
    for chip, entries in temps.items():
        for i, e in enumerate(entries):
            if e.current is None or e.current <= -50 or e.current > 150:
                continue
            label = e.label or (f"Sensor {i + 1}" if len(entries) > 1 else "Temperature")
            out.append({
                "chip": chip,
                "group": CHIP_NAMES.get(chip, chip.replace("_", " ").title()),
                "label": label,
                "current": round(e.current, 1),
                "high": round(e.high, 1) if e.high and 0 < e.high < 150 else None,
                "critical": round(e.critical, 1) if e.critical and 0 < e.critical < 150 else None,
            })
    return out


def cpu_temp(temps: List[Dict[str, Any]]) -> Optional[float]:
    """Pick the single number that best represents the CPU temperature."""
    prefs = [
        ("coretemp", "Package id 0"),
        ("k10temp", "Tctl"),
        ("k10temp", "Tdie"),
        ("zenpower", "Tdie"),
        ("cpu_thermal", None),
        ("x86_pkg_temp", None),
        ("soc_thermal", None),
    ]
    for chip, label in prefs:
        for t in temps:
            if t["chip"] == chip and (label is None or t["label"] == label):
                return t["current"]
    cores = [t["current"] for t in temps if t["chip"] in ("coretemp", "k10temp")]
    if cores:
        return max(cores)
    acpi = [t["current"] for t in temps if t["chip"] == "acpitz"]
    return max(acpi) if acpi else None


def read_fans() -> List[Dict[str, Any]]:
    try:
        fans = psutil.sensors_fans()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return []
    out = []
    for chip, entries in fans.items():
        for i, f in enumerate(entries):
            out.append({"chip": chip, "label": f.label or f"Fan {i + 1}", "rpm": f.current})
    return out


# ---------------------------------------------------------------- power

class Rapl:
    """Reads energy counters from /sys/class/powercap and turns them into watts."""

    def __init__(self) -> None:
        self.zones: List[Dict[str, Any]] = []
        self.error: Optional[str] = None
        self._last: Dict[str, Any] = {}
        self._discover()

    def _discover(self) -> None:
        base = os.path.join(config.SYS, "class", "powercap")
        for d in sorted(glob.glob(os.path.join(base, "*"))):
            if "mmio" in os.path.basename(d):
                continue  # duplicate of the MSR package zone
            name = read_text(os.path.join(d, "name"))
            if not name:
                continue
            energy = os.path.join(d, "energy_uj")
            if not os.path.exists(energy):
                continue
            key = {
                "package-0": "package", "package-1": "package1", "core": "cores",
                "uncore": "graphics", "dram": "memory", "psys": "platform",
            }.get(name, name)
            self.zones.append({
                "key": key,
                "path": energy,
                "max": read_int(os.path.join(d, "max_energy_range_uj"), 2 ** 32) or 2 ** 32,
            })
        if not self.zones:
            self.error = "No RAPL power sensors found (needs an Intel or recent AMD CPU)."

    def sample(self) -> Dict[str, float]:
        now = time.monotonic()
        out: Dict[str, float] = {}
        for z in self.zones:
            try:
                with open(z["path"]) as f:
                    uj = int(f.read())
            except PermissionError:
                self.error = "Power sensors are not readable — the app needs privileged mode."
                continue
            except (OSError, ValueError):
                continue
            prev = self._last.get(z["key"])
            self._last[z["key"]] = (now, uj)
            if prev:
                dt = now - prev[0]
                de = uj - prev[1]
                if de < 0:  # counter wrapped
                    de += z["max"]
                if dt > 0:
                    out[z["key"]] = round(de / dt / 1e6, 2)
        return out


class SmartPlug:
    """Optional real wall-power reading from a Shelly or Tasmota plug."""

    def __init__(self) -> None:
        self.watts: Optional[float] = None
        self.error: Optional[str] = None
        self.last = float("-inf")

    async def poll(self, session: aiohttp.ClientSession) -> None:
        cfg = settings.get().get("smart_plug") or {}
        kind, host = cfg.get("type", "none"), (cfg.get("host") or "").strip()
        if kind == "none" or not host:
            self.watts, self.error = None, None
            return
        if time.monotonic() - self.last < 5:
            return
        self.last = time.monotonic()
        host = host.replace("http://", "").replace("https://", "").strip("/")
        urls = {
            "shelly1": f"http://{host}/status",
            "shelly2": f"http://{host}/rpc/Switch.GetStatus?id=0",
            "tasmota": f"http://{host}/cm?cmnd=Status%208",
        }
        try:
            async with session.get(urls[kind], timeout=aiohttp.ClientTimeout(total=3)) as r:
                j = await r.json(content_type=None)
            if kind == "shelly1":
                w = (j.get("meters") or [{}])[0].get("power")
            elif kind == "shelly2":
                w = j.get("apower")
            else:
                w = j.get("StatusSNS", {}).get("ENERGY", {}).get("Power")
            self.watts = float(w) if w is not None else None
            self.error = None if w is not None else "Plug answered without a power reading"
        except Exception as e:
            self.watts = None
            self.error = f"Smart plug unreachable ({e.__class__.__name__})"


def power_summary(rapl_w: Dict[str, float], plug: SmartPlug, rapl_error: Optional[str]) -> Dict[str, Any]:
    s = settings.get()
    package = rapl_w.get("package")
    if package is not None and "package1" in rapl_w:
        package += rapl_w["package1"]
    if plug.watts is not None:
        wall, source = plug.watts, "plug"
    elif "platform" in rapl_w:
        wall, source = rapl_w["platform"] + s["power_overhead_w"] * 0.5, "estimate"
    elif package is not None:
        wall, source = package + s["power_overhead_w"], "estimate"
    else:
        wall, source = None, None
    monthly_kwh = wall * 24 * 30.4 / 1000 if wall is not None else None
    return {
        "package": package,
        "zones": rapl_w,
        "wall": round(wall, 1) if wall is not None else None,
        "source": source,
        "monthlyKwh": round(monthly_kwh, 2) if monthly_kwh is not None else None,
        "monthlyCost": round(monthly_kwh * s["price_per_kwh"], 2) if monthly_kwh is not None else None,
        "yearlyCost": round(monthly_kwh * 12 * s["price_per_kwh"], 2) if monthly_kwh is not None else None,
        "currency": s["currency"],
        "error": plug.error if s["smart_plug"]["type"] != "none" else (rapl_error if package is None else None),
    }
