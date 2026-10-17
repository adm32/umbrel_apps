"""S.M.A.R.T. drive health via smartctl's JSON output."""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from . import config
from .util import run

# ATA attribute ids we care about
ATTR_REALLOCATED = 5
ATTR_PENDING = 197
ATTR_UNCORRECTABLE = 198
ATTR_CRC = 199
WEAR_ATTRS = (177, 231, 233, 169, 202, 173)  # vendor "life left / wear leveling" attributes
LBAS_WRITTEN = 241
LBAS_READ = 242

_cache: Dict[str, Dict[str, Any]] = {}


def _dev_path(dev: str) -> str:
    """Prefer our own /dev (smartctl recognises the name); fall back to the host's,
    which also sees USB drives plugged in after Vitals started."""
    if os.path.exists("/dev/" + dev):
        return "/dev/" + dev
    p = config.host_path("/dev/" + dev)
    return p if os.path.exists(p) else "/dev/" + dev


def _type_hints(dev: str) -> List[List[str]]:
    """smartctl guesses the device type from the path; under /host/dev it can't, so say it."""
    if _dev_path(dev).startswith("/dev/"):
        return [[]]
    if dev.startswith("nvme"):
        return [["-d", "nvme"]]
    if dev.startswith("sd"):
        return [["-d", "sat"], ["-d", "scsi"]]
    return [[]]


def available() -> bool:
    return shutil.which("smartctl") is not None


async def _smartctl(dev: str, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    cmd = ["smartctl", "-a", "-j", "-n", "standby"] + (extra or []) + [_dev_path(dev)]
    _, out, err = await run(cmd, timeout=40)
    try:
        return json.loads(out)
    except ValueError:
        return {"_error": (err or out or "smartctl failed").strip()[:300]}


def _attr(table: List[Dict[str, Any]], aid: int) -> Optional[Dict[str, Any]]:
    for a in table:
        if a.get("id") == aid:
            return a
    return None


def parse(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": False, "standby": False, "issues": [], "notes": []}
    exit_status = (data.get("smartctl") or {}).get("exit_status", 0)
    messages = " ".join(m.get("string", "") for m in (data.get("smartctl") or {}).get("messages", []))
    if "STANDBY" in messages.upper():
        out["standby"] = True
        return out
    if data.get("_error") or (exit_status & 0b11 and not data.get("smart_status")):
        out["error"] = data.get("_error") or messages or "Drive doesn't report health data"
        return out

    out["available"] = True
    out["model"] = data.get("model_name") or data.get("model_family") or ""
    out["family"] = data.get("model_family") or ""
    out["serial"] = data.get("serial_number") or ""
    out["firmware"] = data.get("firmware_version") or ""
    out["protocol"] = (data.get("device") or {}).get("protocol", "")
    out["rpm"] = data.get("rotation_rate")
    out["formFactor"] = (data.get("form_factor") or {}).get("name")
    out["sataVersion"] = (data.get("sata_version") or {}).get("string")
    out["passed"] = (data.get("smart_status") or {}).get("passed")
    out["temp"] = (data.get("temperature") or {}).get("current")
    out["powerOnHours"] = (data.get("power_on_time") or {}).get("hours")
    out["powerCycles"] = data.get("power_cycle_count")

    issues: List[str] = out["issues"]
    notes: List[str] = out["notes"]
    critical = False

    nvme = data.get("nvme_smart_health_information_log")
    table = (data.get("ata_smart_attributes") or {}).get("table") or []
    if nvme:
        used = nvme.get("percentage_used")
        out["percentUsed"] = used
        out["lifeLeft"] = max(0, 100 - used) if used is not None else None
        out["availableSpare"] = nvme.get("available_spare")
        out["spareThreshold"] = nvme.get("available_spare_threshold")
        out["mediaErrors"] = nvme.get("media_errors")
        out["unsafeShutdowns"] = nvme.get("unsafe_shutdowns")
        out["criticalWarning"] = nvme.get("critical_warning")
        if nvme.get("data_units_written") is not None:
            out["written"] = nvme["data_units_written"] * 512000
        if nvme.get("data_units_read") is not None:
            out["read"] = nvme["data_units_read"] * 512000
        if out["temp"] is None:
            out["temp"] = nvme.get("temperature")
        if nvme.get("critical_warning"):
            issues.append("The drive is reporting a critical warning")
            critical = True
        if (nvme.get("media_errors") or 0) > 0:
            issues.append(f"{nvme['media_errors']} media/data integrity errors logged")
        spare, thr = nvme.get("available_spare"), nvme.get("available_spare_threshold")
        if spare is not None and thr and spare < thr:  # thr 0 = not reported (virtual drives)
            issues.append("Spare blocks are below the safe threshold")
            critical = True
        out["attributes"] = [{"name": k.replace("_", " ").capitalize(), "raw": v}
                             for k, v in nvme.items() if not isinstance(v, (list, dict))]
    else:
        attrs = []
        for a in table:
            attrs.append({
                "id": a.get("id"), "name": (a.get("name") or "").replace("_", " "),
                "value": a.get("value"), "worst": a.get("worst"), "thresh": a.get("thresh"),
                "raw": (a.get("raw") or {}).get("string", (a.get("raw") or {}).get("value")),
                "failing": a.get("when_failed") not in (None, ""),
            })
        out["attributes"] = attrs

        def raw(aid: int) -> Optional[int]:
            a = _attr(table, aid)
            return (a.get("raw") or {}).get("value") if a else None

        out["reallocated"] = raw(ATTR_REALLOCATED)
        out["pending"] = raw(ATTR_PENDING)
        out["uncorrectable"] = raw(ATTR_UNCORRECTABLE)
        out["crcErrors"] = raw(ATTR_CRC)
        for aid in WEAR_ATTRS:
            a = _attr(table, aid)
            if a and a.get("value") is not None and a.get("value") <= 100:
                out["lifeLeft"] = a["value"]
                break
        lw = raw(LBAS_WRITTEN)
        if lw:
            out["written"] = lw * (data.get("logical_block_size") or 512)
        lr = raw(LBAS_READ)
        if lr:
            out["read"] = lr * (data.get("logical_block_size") or 512)

        if out["reallocated"]:
            issues.append(f"{out['reallocated']} reallocated sectors (the surface is wearing out)")
            critical = critical or out["reallocated"] > 100
        if out["pending"]:
            issues.append(f"{out['pending']} sectors waiting to be reallocated")
        if out["uncorrectable"]:
            issues.append(f"{out['uncorrectable']} sectors that couldn't be read")
        if out["crcErrors"]:
            notes.append(f"{out['crcErrors']} cable/connection (CRC) errors — usually a loose or bad cable, not the drive")
        for a in attrs:
            if a["failing"]:
                issues.append(f"Attribute '{a['name']}' has passed its failure threshold")
                critical = True

    if out["passed"] is False:
        issues.insert(0, "The drive's own self-assessment FAILED — back up now")
        critical = True
    life = out.get("lifeLeft")
    if life is not None:
        if life < 10:
            issues.append(f"Only {life}% of rated write endurance left")
            critical = True
        elif life < 30:
            issues.append(f"{life}% of rated write endurance left")
    t = out.get("temp")
    if t is not None and t >= (70 if nvme else 55):
        issues.append(f"Running hot ({t}°C)")

    tests = ((data.get("ata_smart_self_test_log") or {}).get("standard") or {}).get("table") or []
    if not tests:
        tests = (data.get("nvme_self_test_log") or {}).get("table") or []
    if tests:
        last = tests[0]
        status = last.get("status") or last.get("self_test_result") or {}
        out["lastSelfTest"] = {
            "type": (last.get("type") or last.get("self_test_code") or {}).get("string", ""),
            "result": status.get("string", "") if isinstance(status, dict) else str(status),
            "passed": status.get("passed") if isinstance(status, dict) else None,
            "hours": last.get("lifetime_hours") or last.get("power_on_hours"),
        }
    running = (data.get("ata_smart_data") or {}).get("self_test", {}).get("status", {})
    if running.get("remaining_percent"):
        out["selfTestRunning"] = 100 - running["remaining_percent"]

    out["health"] = "critical" if critical else ("warning" if issues else "good")
    return out


async def read(dev: str, transport: str, force: bool = False) -> Dict[str, Any]:
    cached = _cache.get(dev)
    if cached and not force and time.time() - cached["at"] < 1800:
        return cached
    if not available():
        res = {"available": False, "error": "smartctl not installed", "issues": [], "notes": []}
    else:
        res = parse({})
        for hint in _type_hints(dev):
            res = parse(await _smartctl(dev, hint))
            if res["available"] or res["standby"]:
                break
        if not res["available"] and not res["standby"] and transport == "usb":
            # USB enclosures hide the drive behind a bridge chip: try the common ones
            # (SATA via SAT, and the JMicron / ASMedia / Realtek NVMe bridges)
            for dtype in ("sat", "sntjmicron", "sntasmedia", "sntrealtek"):
                alt = parse(await _smartctl(dev, ["-d", dtype]))
                if alt["available"] or alt["standby"]:
                    res = alt
                    break
            else:
                res["error"] = "This USB enclosure doesn't pass health data through"
        if res.get("standby") and cached and cached.get("available"):
            keep = dict(cached)
            keep["standby"] = True
            res = keep
    res["at"] = time.time()
    _cache[dev] = res
    return res


def cached(dev: str) -> Optional[Dict[str, Any]]:
    return _cache.get(dev)


async def self_test(dev: str) -> str:
    _, out, err = await run(["smartctl", "-t", "short", _dev_path(dev)], timeout=30)
    text = (out + err).strip().splitlines()
    for line in text:
        if "Please wait" in line or "Testing has begun" in line or "test has begun" in line.lower():
            return line.strip()
    return text[-1] if text else "Self-test requested"
