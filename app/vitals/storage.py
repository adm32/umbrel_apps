"""Drives, partitions, mounts, usage and the disk benchmark."""
from __future__ import annotations

import asyncio
import mmap
import os
import random
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .util import Job, read_int, read_text, run

SKIP_PREFIXES = ("loop", "ram", "zram", "dm-", "sr", "fd", "nbd", "md")
VIRTUAL_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2", "securityfs", "pstore",
    "bpf", "debugfs", "tracefs", "configfs", "fusectl", "mqueue", "hugetlbfs", "autofs", "efivarfs",
    "overlay", "nsfs", "squashfs", "binfmt_misc", "ramfs", "rpc_pipefs", "nfsd", "fuse.lxcfs",
}


def _unescape(s: str) -> str:
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def read_mounts() -> List[Dict[str, str]]:
    """Mounts of the host (pid 1) mount namespace."""
    path = os.path.join(config.PROC, "1", "mountinfo")
    raw = read_text(path) or read_text(os.path.join(config.PROC, "self", "mountinfo")) or ""
    out = []
    for line in raw.splitlines():
        parts = line.split()
        try:
            sep = parts.index("-")
        except ValueError:
            continue
        out.append({
            "majmin": parts[2],
            "root": _unescape(parts[3]),
            "mountpoint": _unescape(parts[4]),
            "fstype": parts[sep + 1],
            "source": _unescape(parts[sep + 2]) if len(parts) > sep + 2 else "",
            "opts": parts[5],
        })
    return out


def statvfs(mountpoint: str) -> Optional[Dict[str, int]]:
    try:
        st = os.statvfs(config.host_path(mountpoint))
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return {"total": total, "used": used, "free": free}


def _transport(dev: str, syspath: str) -> str:
    if dev.startswith("nvme"):
        return "nvme"
    if dev.startswith("mmcblk"):
        return "mmc"
    if "/usb" in syspath:
        return "usb"
    if "/ata" in syspath:
        return "sata"
    if "virtio" in syspath:
        return "virtio"
    return "other"


def _kind(transport: str, rotational: bool, removable: bool) -> str:
    if transport == "nvme":
        return "NVMe SSD"
    if transport == "mmc":
        return "SD card" if removable else "eMMC"
    if transport == "usb":
        return "USB hard drive" if rotational else "USB drive"
    return "Hard drive" if rotational else "SSD"


def _labels() -> Dict[str, str]:
    out = {}
    d = config.host_path("/dev/disk/by-label")
    try:
        for name in os.listdir(d):
            target = os.path.basename(os.readlink(os.path.join(d, name)))
            # udev escapes bytes as \xNN; decode them back to UTF-8 (é, ü, emoji…)
            raw = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), name)
            out[target] = raw.encode("latin-1", "replace").decode("utf-8", "replace")
    except OSError:
        pass
    return out


def list_drives() -> List[Dict[str, Any]]:
    block = os.path.join(config.SYS, "block")
    try:
        devs = sorted(os.listdir(block))
    except OSError:
        return []
    mounts = read_mounts()
    by_majmin: Dict[str, List[Dict[str, str]]] = {}
    for m in mounts:
        by_majmin.setdefault(m["majmin"], []).append(m)
    labels = _labels()
    umbrel_dev = None
    try:
        umbrel_dev = os.stat(config.UMBREL_ROOT_HOST).st_dev
    except OSError:
        pass

    drives = []
    for dev in devs:
        if dev.startswith(SKIP_PREFIXES) or "boot" in dev or "rpmb" in dev:
            continue
        base = os.path.join(block, dev)
        size = (read_int(os.path.join(base, "size"), 0) or 0) * 512
        if size == 0:
            continue
        syspath = os.path.realpath(base)
        rotational = read_text(os.path.join(base, "queue", "rotational")) == "1"
        removable = read_text(os.path.join(base, "removable")) == "1"
        transport = _transport(dev, syspath)
        vendor = (read_text(os.path.join(base, "device", "vendor")) or "").strip()
        model = (read_text(os.path.join(base, "device", "model")) or read_text(os.path.join(base, "device", "name")) or "").strip()
        if vendor and vendor.lower() not in model.lower() and vendor.upper() not in ("ATA", "NVME"):
            model = f"{vendor} {model}".strip()

        parts = []
        for entry in sorted(os.listdir(base)):
            pbase = os.path.join(base, entry)
            if entry.startswith(dev) and os.path.exists(os.path.join(pbase, "partition")):
                parts.append((entry, pbase))
        if not parts:
            parts = [(dev, base)]

        partitions = []
        holds_umbrel = False
        for pname, pbase in parts:
            majmin = read_text(os.path.join(pbase, "dev")) or ""
            psize = (read_int(os.path.join(pbase, "size"), 0) or 0) * 512
            ms = by_majmin.get(majmin, [])
            # prefer a mount of the filesystem root, then the shortest path
            ms_sorted = sorted(ms, key=lambda m: (m["root"] != "/", len(m["mountpoint"])))
            primary = ms_sorted[0] if ms_sorted else None
            usage = statvfs(primary["mountpoint"]) if primary else None
            is_umbrel = False
            if primary and umbrel_dev is not None:
                try:
                    is_umbrel = os.stat(config.host_path(primary["mountpoint"])).st_dev == umbrel_dev
                except OSError:
                    pass
            holds_umbrel = holds_umbrel or is_umbrel
            partitions.append({
                "name": pname,
                "size": psize,
                "label": labels.get(pname, ""),
                "fstype": primary["fstype"] if primary else "",
                "mountpoint": primary["mountpoint"] if primary else "",
                "mountpoints": sorted({m["mountpoint"] for m in ms}),
                "readonly": bool(primary and primary["opts"].split(",")[0] == "ro"),
                "usage": usage,
                "umbrel": is_umbrel,
            })

        used = sum((p["usage"] or {}).get("used", 0) for p in partitions)
        total = sum((p["usage"] or {}).get("total", 0) for p in partitions)
        drives.append({
            "name": dev,
            "model": model or dev,
            "serial": (read_text(os.path.join(base, "device", "serial")) or "").strip(),
            "size": size,
            "transport": transport,
            "kind": _kind(transport, rotational, removable),
            "rotational": rotational,
            "removable": removable or transport == "usb",
            "external": transport == "usb" or removable,
            "system": holds_umbrel,
            "partitions": partitions,
            "used": used,
            "fsTotal": total,
        })
    drives.sort(key=lambda d: (not d["system"], d["external"], d["name"]))
    return drives


def umbrel_storage() -> Optional[Dict[str, int]]:
    try:
        st = os.statvfs(config.UMBREL_ROOT_HOST)
    except OSError:
        return None
    return {
        "total": st.f_blocks * st.f_frsize,
        "used": (st.f_blocks - st.f_bfree) * st.f_frsize,
        "free": st.f_bavail * st.f_frsize,
    }


def writable_targets() -> List[Dict[str, Any]]:
    out = []
    for d in list_drives():
        for p in d["partitions"]:
            if p["mountpoint"] and not p["readonly"] and p["usage"] and p["fstype"] not in VIRTUAL_FS:
                if p["mountpoint"] in ("/boot", "/boot/efi") or p["fstype"] in ("vfat",) and p["size"] < 2e9:
                    continue
                out.append({"drive": d["name"], "model": d["model"], "partition": p["name"],
                            "mountpoint": p["mountpoint"], "free": p["usage"]["free"]})
    return out


# ---------------------------------------------------------------- benchmark

def _bench_dir(mountpoint: str) -> str:
    """Write the test file somewhere harmless on that filesystem."""
    hp = config.host_path(mountpoint)
    umbrel = config.UMBREL_ROOT_HOST
    if os.path.normpath(umbrel).startswith(os.path.normpath(hp).rstrip("/") + "/") or os.path.normpath(umbrel) == os.path.normpath(hp):
        d = os.path.join(str(config.DATA), "bench")
        try:
            if os.stat(d if os.path.exists(d) else str(config.DATA)).st_dev == os.stat(hp).st_dev:
                os.makedirs(d, exist_ok=True)
                return d
        except OSError:
            pass
    return hp


def _benchmark_sync(job: Job, mountpoint: str, size_mb: int) -> Dict[str, Any]:
    block = 4 * 1024 * 1024
    count = max(1, size_mb * 1024 * 1024 // block)
    path = os.path.join(_bench_dir(mountpoint), f".vitals-benchmark-{uuid.uuid4().hex[:8]}.tmp")
    direct = getattr(os, "O_DIRECT", 0)
    buf = mmap.mmap(-1, block)
    buf.write(os.urandom(block))
    result: Dict[str, Any] = {"mountpoint": mountpoint, "sizeMb": size_mb, "direct": bool(direct)}

    def open_w() -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if direct:
            try:
                return os.open(path, flags | direct, 0o600)
            except OSError:
                result["direct"] = False
        return os.open(path, flags, 0o600)

    try:
        # sequential write
        job.phase = "Writing"
        fd = open_w()
        t0 = time.perf_counter()
        for i in range(count):
            if job.cancelled:
                raise RuntimeError("cancelled")
            view = memoryview(buf)
            written = 0
            while written < block:
                written += os.write(fd, view[written:])
            job.progress = 0.45 * (i + 1) / count
            job.data["live"] = round((i + 1) * block / (time.perf_counter() - t0) / 1e6, 1)
        os.fsync(fd)
        os.close(fd)
        result["writeMBps"] = round(count * block / (time.perf_counter() - t0) / 1e6, 1)

        # sequential read
        job.phase = "Reading"
        use_direct = result["direct"]
        fd = os.open(path, os.O_RDONLY | (direct if use_direct else 0))
        if not use_direct and hasattr(os, "posix_fadvise"):
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        t0 = time.perf_counter()
        total = 0
        for i in range(count):
            if job.cancelled:
                raise RuntimeError("cancelled")
            n = os.readv(fd, [buf])
            if n <= 0:
                break
            total += n
            job.progress = 0.45 + 0.4 * (i + 1) / count
            job.data["live"] = round(total / (time.perf_counter() - t0) / 1e6, 1)
        os.close(fd)
        result["readMBps"] = round(total / (time.perf_counter() - t0) / 1e6, 1)

        # random 4K reads (only meaningful when the page cache is bypassed)
        if use_direct:
            job.phase = "Random 4K reads"
            small = mmap.mmap(-1, 4096)
            fd = os.open(path, os.O_RDONLY | direct)
            blocks = (count * block) // 4096
            ops = 0
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 3.0 and not job.cancelled:
                os.preadv(fd, [small], random.randrange(blocks) * 4096)
                ops += 1
                if ops % 256 == 0:
                    job.progress = 0.85 + 0.15 * min(1.0, (time.perf_counter() - t0) / 3.0)
            os.close(fd)
            elapsed = time.perf_counter() - t0
            result["randIops"] = int(ops / elapsed)
            result["randLatencyMs"] = round(elapsed / max(ops, 1) * 1000, 3)
        return result
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def benchmark(job: Job, mountpoint: str, size_mb: int) -> Dict[str, Any]:
    targets = {t["mountpoint"]: t for t in writable_targets()}
    if mountpoint not in targets:
        raise ValueError("That location can't be tested (not mounted or read-only).")
    if targets[mountpoint]["free"] < size_mb * 1024 * 1024 * 2:
        raise ValueError("Not enough free space for the test file.")
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, _benchmark_sync, job, mountpoint, size_mb)
    res["drive"] = targets[mountpoint]["drive"]
    res["model"] = targets[mountpoint]["model"]
    res["at"] = time.time()
    return res


# ---------------------------------------------------------------- per-app storage

async def _du(path: str) -> int:
    rc, out, _ = await run(["du", "-s", "-B1", "-x", path], timeout=600)
    try:
        return int(out.split()[0])
    except (IndexError, ValueError):
        return 0


async def analyze_apps(job: Job) -> Dict[str, Any]:
    from . import docker_api

    base = os.path.join(config.UMBREL_ROOT_HOST, "app-data")
    try:
        app_ids = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    except OSError:
        app_ids = []
    installed = set(docker_api.installed_apps())
    results: List[Dict[str, Any]] = []
    sem = asyncio.Semaphore(3)
    done = 0

    async def one(app_id: str) -> None:
        nonlocal done
        async with sem:
            if job.cancelled:
                return
            size = await _du(os.path.join(base, app_id))
            results.append({
                "app": app_id,
                "name": docker_api.app_name(app_id),
                "icon": docker_api.app_icon(app_id),
                "data": size,
                "installed": (app_id in installed) if installed else None,
            })
            done += 1
            job.progress = done / max(1, len(app_ids))
            job.phase = f"Measured {done} of {len(app_ids)} apps"

    await asyncio.gather(*(one(a) for a in app_ids))

    # Docker image size per app (unique layers aren't exposed, so this is the full image size)
    images: Dict[str, int] = {}
    try:
        conts = await docker_api.containers(max_age=0)
        imgs = {i["Id"]: i.get("Size", 0) for i in await docker_api.get("/images/json")}
        seen = set()
        for c in conts.values():
            if c["app"] and (c["app"], c["imageId"]) not in seen:
                seen.add((c["app"], c["imageId"]))
                images[c["app"]] = images.get(c["app"], 0) + imgs.get(c["imageId"], 0)
    except Exception:
        pass
    for r in results:
        r["images"] = images.get(r["app"], 0)
        r["total"] = r["data"] + r["images"]
    results.sort(key=lambda r: r["total"], reverse=True)

    other: Dict[str, int] = {}
    for label, rel in (("Home (your files)", "home"), ("Backups", "backups"), ("Trash", "trash")):
        p = os.path.join(config.UMBREL_ROOT_HOST, rel)
        if os.path.isdir(p):
            other[label] = await _du(p)
    return {"apps": results, "other": other, "at": time.time()}


def io_counters_by_disk(raw: Dict[str, Any], drives: List[str]) -> Dict[str, Tuple[int, int, int, int, int]]:
    out = {}
    for name in drives:
        c = raw.get(name)
        if c is not None:
            out[name] = (c.read_bytes, c.write_bytes, c.read_count, c.write_count, getattr(c, "busy_time", 0))
    return out
