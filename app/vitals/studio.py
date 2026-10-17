"""Studio: sample-library and project scanner for music producers."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import struct
import time
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .util import Job, has_symlink_between, is_within

AUDIO_EXT = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a", ".aac", ".alac", ".caf", ".rx2", ".rex"}
PROJECT_EXT = {
    ".als": "Ableton Live", ".flp": "FL Studio", ".logicx": "Logic Pro", ".ptx": "Pro Tools", ".cpr": "Cubase",
    ".rpp": "REAPER", ".bwproject": "Bitwig", ".song": "Studio One", ".band": "GarageBand", ".reason": "Reason",
    ".npr": "Nuendo", ".aup3": "Audacity",
}
BACKUP_HINTS = (".rpp-bak", ".bak")


def roots() -> List[Dict[str, str]]:
    """Places the scanner is allowed to look."""
    out = []
    u = config.UMBREL_ROOT_HOST
    for label, rel in (("Home", "home"), ("External drives", "external"), ("Network shares", "network")):
        p = os.path.join(u, rel)
        if os.path.isdir(p):
            out.append({"label": label, "path": p})
    return out


def _allowed(path: str) -> bool:
    rs = [r["path"] for r in roots()]
    return is_within(path, rs) and not any(has_symlink_between(r, path) for r in rs if is_within(path, [r]))


def display(path: str) -> str:
    u = config.UMBREL_ROOT_HOST
    for label, rel in (("Home", "home"), ("External", "external"), ("Network", "network")):
        base = os.path.join(u, rel)
        if path == base or path.startswith(base + "/"):
            return label + path[len(base):]
    return path


def browse(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {"path": "", "display": "", "parent": None,
                "dirs": [{"name": r["label"], "path": r["path"]} for r in roots()]}
    if not _allowed(path) or not os.path.isdir(path):
        raise ValueError("That folder isn't available")
    dirs = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                dirs.append({"name": e.name, "path": e.path})
    except OSError:
        pass
    parent = os.path.dirname(path)
    return {"path": path, "display": display(path), "parent": parent if _allowed(parent) else "",
            "dirs": dirs[:500]}


# ---------------------------------------------------------------- audio headers

def _wav_info(f) -> Optional[Tuple[int, int, int, float]]:
    head = f.read(12)
    if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
        return None
    sr = bits = ch = 0
    byte_rate = 0
    data_size = 0
    for _ in range(64):
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
        if cid == b"fmt ":
            fmt = f.read(min(size, 40))
            if len(fmt) >= 16:
                _, ch, sr, byte_rate, _, bits = struct.unpack("<HHIIHH", fmt[:16])
            f.seek(size - len(fmt) + (size & 1), 1)
        elif cid == b"data":
            data_size = size
            break
        else:
            f.seek(size + (size & 1), 1)
    if not sr:
        return None
    dur = data_size / byte_rate if byte_rate and data_size and data_size != 0xFFFFFFFF else 0.0
    return sr, bits, ch, dur


def _ext80(b: bytes) -> float:
    exp = ((b[0] & 0x7F) << 8) | b[1]
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0 and mant == 0:
        return 0.0
    return mant * 2.0 ** (exp - 16383 - 63)


def _aiff_info(f) -> Optional[Tuple[int, int, int, float]]:
    head = f.read(12)
    if len(head) < 12 or head[:4] != b"FORM" or head[8:12] not in (b"AIFF", b"AIFC"):
        return None
    for _ in range(64):
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        cid, size = hdr[:4], struct.unpack(">I", hdr[4:])[0]
        if cid == b"COMM":
            c = f.read(18)
            ch, frames, bits = struct.unpack(">hIh", c[:8])
            sr = int(_ext80(c[8:18]))
            return sr, bits, ch, frames / sr if sr else 0.0
        f.seek(size + (size & 1), 1)
    return None


def audio_info(path: str, ext: str) -> Optional[Tuple[int, int, int, float]]:
    try:
        with open(path, "rb") as f:
            if ext in (".wav", ".wave"):
                return _wav_info(f)
            if ext in (".aif", ".aiff"):
                return _aiff_info(f)
    except (OSError, struct.error):
        return None
    return None


# ---------------------------------------------------------------- scan

class Studio:
    def __init__(self) -> None:
        self.last: Optional[Dict[str, Any]] = None
        self._dupe_paths: Dict[str, str] = {}   # item id -> path
        self._backup_paths: Dict[str, str] = {}
        self._scan_root = ""

    async def scan(self, job: Job, path: str) -> Dict[str, Any]:
        if not _allowed(path) or not os.path.isdir(path):
            raise ValueError("Pick a folder inside Home or an external drive")
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, self._scan_sync, job, path)
        self.last = res
        return res

    def _scan_sync(self, job: Job, root: str) -> Dict[str, Any]:
        self._dupe_paths = {}
        self._backup_paths = {}
        self._scan_root = root
        by_ext: Counter = Counter()
        size_by_ext: Counter = Counter()
        rates: Counter = Counter()
        depths: Counter = Counter()
        total_dur = 0.0
        by_size: Dict[int, List[str]] = defaultdict(list)
        largest: List[Tuple[int, str]] = []
        projects: List[Dict[str, Any]] = []
        backups: List[Tuple[str, int, str]] = []
        files = 0
        audio_bytes = 0
        headers_read = 0
        t0 = time.monotonic()
        last_tick = 0.0

        for dirpath, dirnames, filenames in os.walk(root):
            if job.cancelled:
                break
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            in_backup = os.path.basename(dirpath).lower() == "backup"
            for d in list(dirnames):
                if d.endswith(".logicx"):
                    p = os.path.join(dirpath, d)
                    projects.append({"name": d[:-7], "daw": "Logic Pro", "path": display(p),
                                     "modified": _mtime(p)})
                    dirnames.remove(d)
            for name in filenames:
                files += 1
                p = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                low = name.lower()
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if ext in AUDIO_EXT:
                    by_ext[ext.lstrip(".")] += 1
                    size_by_ext[ext.lstrip(".")] += st.st_size
                    audio_bytes += st.st_size
                    if st.st_size > 4096:
                        by_size[st.st_size].append(p)
                    if len(largest) < 15 or st.st_size > largest[-1][0]:
                        largest.append((st.st_size, p))
                        largest.sort(reverse=True)
                        del largest[15:]
                    if ext in (".wav", ".wave", ".aif", ".aiff") and headers_read < 200000:
                        info = audio_info(p, ext)
                        headers_read += 1
                        if info:
                            sr, bits, ch, dur = info
                            rates[f"{sr / 1000:g} kHz"] += 1
                            depths[f"{bits}-bit"] += 1
                            total_dur += dur
                elif ext in PROJECT_EXT and not in_backup:
                    projects.append({"name": os.path.splitext(name)[0], "daw": PROJECT_EXT[ext],
                                     "path": display(p), "modified": st.st_mtime, "size": st.st_size})
                elif (in_backup and ext in (".als", ".flp", ".cpr", ".song")) or low.endswith(BACKUP_HINTS):
                    backups.append((p, st.st_size, "DAW backup"))
            if time.monotonic() - last_tick > 0.5:
                last_tick = time.monotonic()
                job.phase = f"Scanned {files:,} files"
                job.data["files"] = files

        # duplicates: same size -> same quick hash (head+tail) -> same full hash
        job.phase = "Looking for duplicates"
        candidates = [ps for ps in by_size.values() if len(ps) > 1]
        groups: List[Dict[str, Any]] = []
        for i, ps in enumerate(candidates):
            if job.cancelled:
                break
            quick: Dict[bytes, List[str]] = defaultdict(list)
            for p in ps:
                h = _quick_hash(p)
                if h:
                    quick[h].append(p)
            for qs in quick.values():
                if len(qs) < 2:
                    continue
                full: Dict[str, List[str]] = defaultdict(list)
                for p in qs:
                    h = _full_hash(p)
                    if h:
                        full[h].append(p)
                for same in full.values():
                    if len(same) < 2:
                        continue
                    same.sort(key=lambda p: (len(p), p))  # keep the shortest path
                    size = os.path.getsize(same[0])
                    copies = []
                    for p in same:
                        cid = uuid.uuid4().hex[:10]
                        self._dupe_paths[cid] = p
                        copies.append({"id": cid, "path": display(p)})
                    groups.append({"name": os.path.basename(same[0]), "size": size, "copies": copies,
                                   "wasted": size * (len(same) - 1)})
            job.progress = 0.5 + 0.5 * (i + 1) / max(1, len(candidates))
        groups.sort(key=lambda g: g["wasted"], reverse=True)

        backup_items = []
        for p, size, kind in backups:
            bid = uuid.uuid4().hex[:10]
            self._backup_paths[bid] = p
            backup_items.append({"id": bid, "path": display(p), "size": size, "kind": kind})
        projects.sort(key=lambda p: p.get("modified") or 0, reverse=True)

        return {
            "root": display(root),
            "files": files,
            "audioFiles": sum(by_ext.values()),
            "audioBytes": audio_bytes,
            "hours": round(total_dur / 3600, 1),
            "formats": [{"ext": e, "count": c, "size": size_by_ext[e]} for e, c in by_ext.most_common()],
            "sampleRates": rates.most_common(8),
            "bitDepths": depths.most_common(6),
            "largest": [{"path": display(p), "size": s} for s, p in largest],
            "duplicates": groups[:300],
            "duplicateWaste": sum(g["wasted"] for g in groups),
            "projects": projects[:300],
            "projectCount": len(projects),
            "backups": backup_items[:2000],
            "backupBytes": sum(b["size"] for b in backup_items),
            "seconds": round(time.monotonic() - t0, 1),
            "at": time.time(),
        }

    def delete(self, ids: List[str]) -> Dict[str, Any]:
        freed, removed, errors = 0, 0, []
        targets = []
        for i in ids:
            p = self._dupe_paths.get(i) or self._backup_paths.get(i)
            if p:
                targets.append((i, p))
        doomed = {p for _, p in targets}
        if self.last:  # never allow every copy of a duplicate group to go
            for g in self.last.get("duplicates", []):
                paths = [self._dupe_paths.get(c["id"]) for c in g["copies"]]
                if paths and all(p in doomed for p in paths):
                    raise ValueError(f"Keep at least one copy of {g['name']}")
        for i, p in targets:
            if not _allowed(p):
                errors.append(f"skipped {display(p)}")
                continue
            try:
                st = os.lstat(p)
                if not stat.S_ISREG(st.st_mode):
                    continue
                os.unlink(p)
                freed += st.st_size
                removed += 1
                self._dupe_paths.pop(i, None)
                self._backup_paths.pop(i, None)
            except OSError as e:
                errors.append(f"{display(p)}: {e.strerror}")
        return {"freed": freed, "removed": removed, "errors": errors}


def _mtime(p: str) -> Optional[float]:
    try:
        return os.stat(p).st_mtime
    except OSError:
        return None


def _quick_hash(p: str) -> Optional[bytes]:
    """Hash of the first and last 64 KB — cheap way to rule out non-duplicates."""
    try:
        with open(p, "rb") as f:
            h = hashlib.blake2b(f.read(65536), digest_size=16)
            if os.path.getsize(p) > 131072:
                f.seek(-65536, 2)
                h.update(f.read(65536))
            return h.digest()
    except OSError:
        return None


def _full_hash(p: str) -> Optional[str]:
    h = hashlib.blake2b(digest_size=20)
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()
