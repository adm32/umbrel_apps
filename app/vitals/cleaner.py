"""Disk cleaner: finds leftovers and removes only what the scan found.

Every category records exactly what it would touch on the server side; the
clean step accepts item ids from the latest scan, never raw paths, and
re-checks each path against the category's allowed roots before acting.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, docker_api
from .util import Job, has_symlink_between, host_all, is_within, load_json, nsenter_available, read_text, run, save_json

STATS_PATH = config.DATA / "cleaner.json"

JUNK_FILES = {".DS_Store": "ds", "Thumbs.db": "thumbs", "desktop.ini": "thumbs", "ehthumbs.db": "thumbs"}
JUNK_DIRS = {".Spotlight-V100": "index", ".fseventsd": "index", ".TemporaryItems": "index"}
ROTATED_RE = re.compile(r"(\.\d+(\.(gz|xz|bz2|zst))?|\.gz|\.xz|\.bz2|\.zst|\.old|-\d{8}(\.gz)?)$")


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _norm_ref(ref: str) -> Tuple[str, Optional[str]]:
    """'docker.io/library/nginx:1.2@sha256:…' -> ('nginx:1.2', 'nginx@sha256:…')."""
    ref = ref.strip().strip("'\"")
    digest = None
    if "@" in ref:
        ref, digest = ref.split("@", 1)
    for prefix in ("docker.io/library/", "docker.io/", "index.docker.io/library/", "index.docker.io/"):
        if ref.startswith(prefix):
            ref = ref[len(prefix):]
            break
    if ref.startswith("library/"):
        ref = ref[len("library/"):]
    name = ref.rsplit(":", 1)[0] if ":" in ref.split("/")[-1] else ref
    if ":" not in ref.split("/")[-1]:
        ref += ":latest"
    return ref, (f"{name}@{digest}" if digest else None)


def _norm_repo(s: str) -> str:
    for prefix in ("docker.io/library/", "docker.io/"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


class Cleaner:
    def __init__(self) -> None:
        self.scan_result: Optional[Dict[str, Any]] = None
        self._actions: Dict[str, Dict[str, Callable[[], Any]]] = {}
        self._sizes: Dict[str, Dict[str, int]] = {}
        self.stats = load_json(STATS_PATH, {"freed": 0, "runs": 0, "last": None})

    # ------------------------------------------------------------ helpers

    @property
    def umbrel(self) -> str:
        return config.UMBREL_ROOT_HOST

    def _category(self, cid: str, title: str, desc: str, icon: str, safety: str, default: bool,
                  items: List[Dict[str, Any]], note: str = "", info_only: bool = False) -> Dict[str, Any]:
        return {
            "id": cid, "title": title, "description": desc, "icon": icon, "safety": safety,
            "default": default, "note": note, "infoOnly": info_only,
            "size": sum(i["size"] for i in items), "count": len(items), "items": items,
        }

    def _register(self, cid: str, item_id: str, size: int, action: Callable[[], Any]) -> None:
        self._actions.setdefault(cid, {})[item_id] = action
        self._sizes.setdefault(cid, {})[item_id] = size

    def _safe_remove(self, path: str, roots: List[str], tree: bool = False) -> int:
        if not is_within(path, roots) or any(has_symlink_between(r, path) for r in roots if is_within(path, [r])):
            raise PermissionError(f"refusing to touch {path}")
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            return 0
        if stat.S_ISLNK(st.st_mode):
            os.unlink(path)
            return 0
        if stat.S_ISDIR(st.st_mode):
            if not tree:
                raise PermissionError(f"{path} is a folder")
            size = _tree_size(path)
            shutil.rmtree(path, ignore_errors=True)
            return size
        os.unlink(path)
        return st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size

    # ------------------------------------------------------------ scan

    async def scan(self, job: Job) -> Dict[str, Any]:
        self._actions = {}
        self._sizes = {}
        cats: List[Dict[str, Any]] = []
        steps: List[Tuple[str, Callable[[Job], Any]]] = [
            ("Docker images", self._scan_images),
            ("Docker build cache", self._scan_build_cache),
            ("Docker volumes", self._scan_volumes),
            ("Stopped containers", self._scan_containers),
            ("Container logs", self._scan_container_logs),
            ("System journal", self._scan_journal),
            ("Old log files", self._scan_rotated_logs),
            ("Temporary files", self._scan_tmp),
            ("Leftover app data", self._scan_orphan_appdata),
            ("Mac & Windows junk files", self._scan_junk),
            ("Umbrel trash", self._scan_trash),
        ]
        for i, (label, fn) in enumerate(steps):
            if job.cancelled:
                break
            job.phase = f"Checking {label.lower()}…"
            job.progress = i / len(steps)
            try:
                cat = await fn(job)
                if cat:
                    cats.append(cat)
            except Exception as e:
                job.log(f"{label}: skipped ({e})")
        reclaim = sum(c["size"] for c in cats if not c["infoOnly"])
        self.scan_result = {"id": _uid(), "at": time.time(), "categories": cats, "total": reclaim,
                            "stats": self.stats, "skipped": job.lines}
        return self.scan_result

    async def _installed_image_refs(self) -> Tuple[set, set]:
        tags, digests = set(), set()
        base = os.path.join(self.umbrel, "app-data")
        try:
            apps = os.listdir(base)
        except OSError:
            apps = []
        for app in apps:
            raw = read_text(os.path.join(base, app, "docker-compose.yml")) or ""
            for m in re.finditer(r"^\s*image:\s*(\S+)", raw, re.M):
                t, d = _norm_ref(m.group(1))
                tags.add(t)
                if d:
                    digests.add(d)
        return tags, digests

    async def _scan_images(self, job: Job) -> Optional[Dict[str, Any]]:
        if not docker_api.available():
            return None
        images = await docker_api.get("/images/json")
        conts = await docker_api.get("/containers/json", all="1")
        used_ids = {c.get("ImageID") for c in conts}
        tags, digests = await self._installed_image_refs()
        try:
            df = await docker_api.request("GET", "/system/df", {"type": "image"}, timeout=120)
            shared = {i["Id"]: i.get("SharedSize", 0) for i in (df or {}).get("Images") or []}
        except Exception:
            shared = {}
        items = []
        for img in images:
            iid = img["Id"]
            if iid in used_ids:
                continue
            rtags = [_norm_repo(t) for t in (img.get("RepoTags") or []) if t != "<none>:<none>"]
            rdigests = [_norm_repo(d) for d in (img.get("RepoDigests") or [])]
            if any(t in tags for t in rtags) or any(d in digests for d in rdigests):
                continue  # belongs to an installed (maybe stopped) app
            size = img.get("Size", 0)
            sh = shared.get(iid, 0)
            unique = size - sh if sh and sh > 0 else size
            label = rtags[0] if rtags else (rdigests[0].split("@")[0] + " (old version)" if rdigests else "Untagged layer")
            item_id = _uid()
            items.append({"id": item_id, "label": label, "size": unique,
                          "detail": "Not used by any installed app", "created": img.get("Created")})

            async def remove(iid: str = iid) -> int:
                await docker_api.request("DELETE", f"/images/{iid}", {"force": "false"})
                return 0
            self._register("images", item_id, unique, remove)
        items.sort(key=lambda i: i["size"], reverse=True)
        return self._category(
            "images", "Unused app images",
            "Old app versions and images left behind by uninstalled apps. Apps you still have installed — even stopped ones — are never touched.",
            "brand-docker", "safe", True, items)

    async def _scan_build_cache(self, job: Job) -> Optional[Dict[str, Any]]:
        if not docker_api.available():
            return None
        df = await docker_api.request("GET", "/system/df", {"type": "build-cache"}, timeout=120)
        size = sum(b.get("Size", 0) for b in (df or {}).get("BuildCache") or [] if not b.get("InUse"))
        items = []
        if size > 0:
            item_id = _uid()
            items.append({"id": item_id, "label": "Build cache", "size": size, "detail": "Leftovers from building images"})

            async def prune() -> int:
                r = await docker_api.request("POST", "/build/prune", timeout=300)
                return (r or {}).get("SpaceReclaimed", 0)
            self._register("buildcache", item_id, size, prune)
        return self._category("buildcache", "Docker build cache", "Temporary files from building Docker images.",
                              "box", "safe", True, items)

    async def _scan_volumes(self, job: Job) -> Optional[Dict[str, Any]]:
        if not docker_api.available():
            return None
        df = await docker_api.request("GET", "/system/df", {"type": "volume"}, timeout=180)
        items = []
        for v in (df or {}).get("Volumes") or []:
            usage = v.get("UsageData") or {}
            anonymous = re.fullmatch(r"[0-9a-f]{64}", v.get("Name", "")) is not None
            if usage.get("RefCount", 1) != 0 or not anonymous:
                continue
            size = max(0, usage.get("Size", 0))
            item_id = _uid()
            items.append({"id": item_id, "label": v["Name"][:12] + "…", "size": size, "detail": "Anonymous volume, not attached"})

            async def rm(name: str = v["Name"]) -> int:
                await docker_api.request("DELETE", f"/volumes/{name}")
                return 0
            self._register("volumes", item_id, size, rm)
        return self._category(
            "volumes", "Unattached Docker volumes",
            "Anonymous volumes no container is using. Usually leftovers — but check before removing.",
            "database", "review", False, items)

    async def _scan_containers(self, job: Job) -> Optional[Dict[str, Any]]:
        if not docker_api.available():
            return None
        installed = set(docker_api.installed_apps())
        if not installed:
            return None
        conts = await docker_api.get("/containers/json", all="1")  # size=1 here is very slow on busy hosts
        items = []
        for c in conts:
            labels = c.get("Labels") or {}
            project = labels.get("com.docker.compose.project", "")
            if c.get("State") not in ("exited", "created", "dead") or not project:
                continue
            if project in installed or project in ("umbrel",) or project == config.APP_ID:
                continue
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            try:  # measure only the leftovers we found
                size = (await docker_api.get(f"/containers/{c['Id']}/json", size="1")).get("SizeRw") or 0
            except Exception:
                size = 0
            item_id = _uid()
            items.append({"id": item_id, "label": name, "size": size, "detail": f"From '{project}', which isn't installed"})

            async def rm(cid: str = c["Id"]) -> int:
                await docker_api.request("DELETE", f"/containers/{cid}")
                return 0
            self._register("containers", item_id, size, rm)
        return self._category("containers", "Leftover containers",
                              "Stopped containers from apps that are no longer installed.",
                              "box-multiple", "safe", True, items)

    async def _scan_container_logs(self, job: Job) -> Optional[Dict[str, Any]]:
        if not docker_api.available():
            return None
        conts = await docker_api.get("/containers/json", all="1")
        items = []
        info = await docker_api.get("/info")
        root = config.host_path(info.get("DockerRootDir") or "/var/lib/docker")
        for c in conts:
            try:
                ins = await docker_api.get(f"/containers/{c['Id']}/json")
            except Exception:
                continue
            log = ins.get("LogPath") or ""
            if not log:
                continue
            path = config.host_path(log)
            try:
                size = os.stat(path).st_size
            except OSError:
                continue
            if size < 5 * 1024 * 1024:
                continue
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            item_id = _uid()
            items.append({"id": item_id, "label": name, "size": size, "detail": "Container log file"})

            def trunc(path: str = path, root: str = root) -> int:
                if not is_within(path, [root]):
                    raise PermissionError("unexpected log location")
                before = os.stat(path).st_size
                os.truncate(path, 0)
                return before
            self._register("logs", item_id, size, trunc)
        items.sort(key=lambda i: i["size"], reverse=True)
        return self._category("logs", "Oversized app logs",
                              "Log files from apps that have grown large. They're emptied, not deleted — apps keep logging normally.",
                              "file-text", "safe", True, items)

    async def _scan_journal(self, job: Job) -> Optional[Dict[str, Any]]:
        jdir = config.host_path("/var/log/journal")
        if not os.path.isdir(jdir):
            return None
        size = _tree_size(jdir)
        keep = 100 * 1024 * 1024
        items = []
        if size > keep + 20 * 1024 * 1024 and nsenter_available():
            item_id = _uid()
            items.append({"id": item_id, "label": "System journal", "size": size - keep,
                          "detail": f"{_h(size)} now — trimmed to the newest 100 MB"})

            async def vacuum(jdir: str = jdir, before: int = size) -> int:
                rc, out, err = await run(host_all(["journalctl", "--vacuum-size=100M"]), timeout=180)
                if rc != 0:
                    raise RuntimeError(err.strip() or "journalctl failed")
                return max(0, before - _tree_size(jdir))
            self._register("journal", item_id, size - keep, vacuum)
        return self._category("journal", "System journal", "umbrelOS's own log history. The newest 100 MB is kept.",
                              "notebook", "safe", True, items)

    async def _scan_rotated_logs(self, job: Job) -> Optional[Dict[str, Any]]:
        root = config.host_path("/var/log")
        paths: List[Tuple[str, int]] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "journal"]
            for f in filenames:
                if ROTATED_RE.search(f):
                    p = os.path.join(dirpath, f)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        paths.append((p, st.st_size))
        return self._paths_category("rotated", "Old log files", "Rotated and compressed logs in /var/log that are no longer written to.",
                                    "file-zip", "safe", True, paths, [root], group_label="old log files")

    async def _scan_tmp(self, job: Job) -> Optional[Dict[str, Any]]:
        cutoff = time.time() - 10 * 86400
        roots = [config.host_path("/tmp"), config.host_path("/var/tmp")]
        in_use = _open_files(config.HOST)  # never delete a file a running program has open
        paths: List[Tuple[str, int]] = []
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(("systemd-private-", ".X11", ".ICE"))]
                for f in filenames:
                    p = os.path.join(dirpath, f)
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if (stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff and st.st_atime < cutoff
                            and st.st_size >= 4096 and not f.endswith((".pid", ".lock", ".sock", ".lck"))
                            and p not in in_use):
                        paths.append((p, st.st_size))
        return self._paths_category("tmp", "Old temporary files", "Files in the system's temp folders untouched for 10+ days.",
                                    "clock-hour-4", "safe", True, paths, roots, group_label="temporary files")

    async def _scan_orphan_appdata(self, job: Job) -> Optional[Dict[str, Any]]:
        installed = set(docker_api.installed_apps())
        base = os.path.join(self.umbrel, "app-data")
        if not installed or not os.path.isdir(base):
            return None
        try:
            projects = {c["app"] for c in (await docker_api.containers(max_age=0)).values()}
        except Exception:
            projects = set()
        items = []
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d)
            if d in installed or d in projects or d == config.APP_ID or not os.path.isdir(p) or os.path.islink(p):
                continue
            rc, out, _ = await run(["du", "-s", "-B1", "-x", p], timeout=300)
            try:
                size = int(out.split()[0])
            except (IndexError, ValueError):
                size = 0
            item_id = _uid()
            items.append({"id": item_id, "label": docker_api.app_name(d), "size": size,
                          "detail": f"app-data/{d} — this app isn't installed"})

            def rm(p: str = p) -> int:
                return self._safe_remove(p, [base], tree=True)
            self._register("appdata", item_id, size, rm)
        return self._category(
            "appdata", "Leftover app data",
            "Data folders from apps that are no longer installed. Deleting is permanent — if you plan to reinstall an app, keep its data.",
            "folder-x", "careful", False, items)

    async def _scan_junk(self, job: Job) -> Optional[Dict[str, Any]]:
        roots = [os.path.join(self.umbrel, "home"), os.path.join(self.umbrel, "external")]
        roots = [r for r in roots if os.path.isdir(r)]
        groups: Dict[str, List[Tuple[str, int]]] = {"ds": [], "apple": [], "thumbs": [], "index": [], "bench": []}
        deadline = time.monotonic() + 60
        scanned = 0
        partial = False
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                if time.monotonic() > deadline:
                    partial = True
                    break
                for d in list(dirnames):
                    if d in JUNK_DIRS:
                        p = os.path.join(dirpath, d)
                        groups["index"].append((p, _tree_size(p)))
                        dirnames.remove(d)
                    elif d in (".git", "node_modules"):
                        dirnames.remove(d)
                for f in filenames:
                    scanned += 1
                    p = os.path.join(dirpath, f)
                    if f in JUNK_FILES:
                        g = JUNK_FILES[f]
                    elif f.startswith("._") and _is_appledouble(p):
                        g = "apple"
                    elif f.startswith(".vitals-benchmark-"):
                        g = "bench"
                    else:
                        continue
                    try:
                        st = os.lstat(p)
                    except OSError:
                        continue
                    if stat.S_ISREG(st.st_mode):
                        groups[g].append((p, st.st_blocks * 512))
                if scanned % 5000 == 0:
                    job.phase = f"Checking your files… ({scanned:,} so far)"
                    await asyncio.sleep(0)
        labels = {
            "ds": (".DS_Store files", "Folder view settings macOS leaves on shared drives"),
            "apple": ("AppleDouble ._ files", "Hidden metadata copies macOS writes next to files on non-Mac drives"),
            "thumbs": ("Thumbs.db & desktop.ini", "Windows thumbnail caches and folder settings"),
            "index": ("Spotlight & fsevents folders", "macOS search indexes on external drives — rebuilt automatically"),
            "bench": ("Leftover benchmark files", "From a disk speed test that was interrupted"),
        }
        items = []
        for g, paths in groups.items():
            if not paths:
                continue
            size = sum(s for _, s in paths)
            item_id = _uid()
            items.append({"id": item_id, "label": f"{labels[g][0]} ({len(paths):,})", "size": size, "detail": labels[g][1]})

            def rm(paths: List[Tuple[str, int]] = paths, tree: bool = g == "index") -> int:
                freed = 0
                for p, _ in paths:
                    try:
                        freed += self._safe_remove(p, roots, tree=tree)
                    except OSError:
                        pass
                return freed
            self._register("junk", item_id, size, rm)
        note = "Stopped after 60 seconds — run again to find more." if partial else ""
        return self._category("junk", "Mac & Windows junk files",
                              "Tiny hidden files computers leave behind when you copy things onto your Umbrel.",
                              "brand-apple", "safe", True, items, note=note)

    async def _scan_trash(self, job: Job) -> Optional[Dict[str, Any]]:
        p = os.path.join(self.umbrel, "trash")
        if not os.path.isdir(p):
            return None
        size = _tree_size(p)
        if size == 0:
            return None
        return self._category("trash", "Umbrel Files trash",
                              "Files you deleted in the Files app. Empty it from Files → Trash so umbrelOS stays in sync.",
                              "trash", "info", False,
                              [{"id": "trash", "label": "Trash", "size": size, "detail": "Open Files → Trash → Empty trash"}],
                              info_only=True)

    def _paths_category(self, cid: str, title: str, desc: str, icon: str, safety: str, default: bool,
                        paths: List[Tuple[str, int]], roots: List[str], group_label: str) -> Optional[Dict[str, Any]]:
        items = []
        if paths:
            size = sum(s for _, s in paths)
            item_id = _uid()
            items.append({"id": item_id, "label": f"{len(paths):,} {group_label}", "size": size,
                          "detail": ", ".join(os.path.basename(p) for p, _ in sorted(paths, key=lambda x: -x[1])[:3])})

            def rm(paths: List[Tuple[str, int]] = paths) -> int:
                freed = 0
                for p, _ in paths:
                    try:
                        freed += self._safe_remove(p, roots)
                    except OSError:
                        pass
                return freed
            self._register(cid, item_id, size, rm)
        return self._category(cid, title, desc, icon, safety, default, items)

    # ------------------------------------------------------------ clean

    async def clean(self, job: Job, scan_id: str, selection: Dict[str, List[str]]) -> Dict[str, Any]:
        if not self.scan_result or self.scan_result["id"] != scan_id:
            raise RuntimeError("The scan is out of date — scan again first.")
        todo = [(cid, iid) for cid, ids in selection.items() for iid in ids if iid in self._actions.get(cid, {})]
        if not todo:
            raise ValueError("Nothing selected")
        freed = 0
        errors = []
        for n, (cid, iid) in enumerate(todo):
            if job.cancelled:
                break
            action = self._actions[cid][iid]
            est = self._sizes[cid][iid]
            job.phase = f"Cleaning {n + 1} of {len(todo)}"
            try:
                r = action()
                if asyncio.iscoroutine(r):
                    r = await r
                got = r if isinstance(r, int) and r > 0 else est
                freed += got
                job.log(f"✓ {cid}: freed {_h(got)}")
            except Exception as e:
                errors.append(f"{cid}: {e}")
                job.log(f"✗ {cid}: {e}")
            job.progress = (n + 1) / len(todo)
            job.data["freed"] = freed
        from .events import events
        if freed:
            events.add("clean", f"Cleaner freed {_h(freed)}", freed=freed)
        self.stats["freed"] = self.stats.get("freed", 0) + freed
        self.stats["runs"] = self.stats.get("runs", 0) + 1
        self.stats["last"] = {"at": time.time(), "freed": freed}
        save_json(STATS_PATH, self.stats)
        self.scan_result = None
        self._actions = {}
        return {"freed": freed, "errors": errors, "stats": self.stats}


def _is_appledouble(path: str) -> bool:
    try:
        if os.path.getsize(path) > 8 * 1024 * 1024:
            return False
        with open(path, "rb") as f:
            return f.read(4) == b"\x00\x05\x16\x07"
    except OSError:
        return False


def _tree_size(path: str) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            try:
                st = os.lstat(os.path.join(dirpath, f))
                total += st.st_blocks * 512 if hasattr(st, "st_blocks") else st.st_size
            except OSError:
                pass
    return total


def _h(n: float) -> str:
    from .util import human_bytes
    return human_bytes(n)


def _open_files(host_prefix: str) -> set:
    """Host paths currently held open by any process (we share the host's PID namespace)."""
    out = set()
    proc = config.PROC
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("/"):
                out.add(host_prefix + target)
    return out
