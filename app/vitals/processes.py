"""Activity monitor: host processes, grouped by Umbrel app."""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional

import psutil

from . import config, docker_api
from .util import host_read_text, read_text

_CID = re.compile(r"([0-9a-f]{64})")


def _host_users() -> Dict[int, str]:
    out = {}
    raw = host_read_text("/etc/passwd") or ""
    for line in raw.splitlines():
        f = line.split(":")
        if len(f) > 2 and f[2].isdigit():
            out[int(f[2])] = f[0]
    return out


def own_container_id() -> Optional[str]:
    """Our container's id. In our own cgroup namespace our processes show "0::/", so map that to us."""
    for path in ("/proc/self/mountinfo", "/proc/self/cgroup"):
        m = re.search(r"/(?:docker/)?containers/([0-9a-f]{64})/", read_text(path) or "") or \
            re.search(r"docker-([0-9a-f]{64})\.scope", read_text(path) or "")
        if m:
            return m.group(1)
    return None


class ProcessMonitor:
    """Samples processes only while someone is looking (UI or a widget)."""

    def __init__(self) -> None:
        self.procs: Dict[int, psutil.Process] = {}
        self.cgroup: Dict[int, Optional[str]] = {}
        self.users = _host_users()
        self.rows: List[Dict[str, Any]] = []
        self.apps: List[Dict[str, Any]] = []
        self.last_request = float("-inf")
        self.last_sample = 0.0
        self.ncpu = psutil.cpu_count() or 1
        self.self_id = own_container_id() if config.IS_LINUX else None

    def touch(self) -> None:
        self.last_request = time.monotonic()

    @property
    def active(self) -> bool:
        return time.monotonic() - self.last_request < 30

    def _container(self, pid: int) -> Optional[str]:
        if pid in self.cgroup:
            return self.cgroup[pid]
        raw = read_text(f"{config.PROC}/{pid}/cgroup") or ""
        m = _CID.search(raw)
        cid = m.group(1) if m else None
        if cid is None and self.self_id and raw.strip().endswith("::/"):
            cid = self.self_id  # a process in our own cgroup namespace = this container
        self.cgroup[pid] = cid
        return cid

    async def sample(self, total_mem: int) -> None:
        conts = await docker_api.containers()
        seen = set()
        rows = []
        for p in psutil.process_iter():
            pid = p.pid
            seen.add(pid)
            if pid not in self.procs:
                self.procs[pid] = p
                try:
                    p.cpu_percent(None)  # prime
                except psutil.Error:
                    pass
                continue
            proc = self.procs[pid]
            try:
                with proc.oneshot():
                    cpu = proc.cpu_percent(None)
                    mem = proc.memory_info().rss
                    name = proc.name()
                    uid = proc.uids().real if hasattr(proc, "uids") else None
                    threads = proc.num_threads()
                    status = proc.status()
                    started = proc.create_time()
                    try:
                        cmd = " ".join(proc.cmdline())[:300]
                    except psutil.Error:
                        cmd = ""
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                continue
            if mem == 0 and not cmd:
                continue  # kernel threads
            cid = self._container(pid) if config.IS_LINUX else None
            c = conts.get(cid) if cid else None
            rows.append({
                "pid": pid,
                "name": name,
                "cmd": cmd,
                "cpu": round(cpu, 1),
                "cpuSystem": round(cpu / self.ncpu, 1),
                "mem": mem,
                "memPct": round(100 * mem / total_mem, 2) if total_mem else 0,
                "threads": threads,
                "status": status,
                "user": self.users.get(uid, str(uid)) if uid is not None else "",
                "started": started,
                "app": c["app"] if c else ("" if cid else "system"),
                "appName": c["appName"] if c else ("Container" if cid else "umbrelOS / system"),
                "container": c["name"] if c else "",
            })
        for pid in list(self.procs):
            if pid not in seen:
                self.procs.pop(pid, None)
                self.cgroup.pop(pid, None)
        rows.sort(key=lambda r: r["cpu"], reverse=True)

        apps: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            key = r["app"] or r["appName"]
            a = apps.setdefault(key, {
                "app": r["app"], "name": r["appName"], "cpu": 0.0, "mem": 0, "procs": 0,
                "icon": docker_api.app_icon(r["app"]) if r["app"] and r["app"] != "system" else "",
                "containers": set(),
            })
            a["cpu"] += r["cpu"]
            a["mem"] += r["mem"]
            a["procs"] += 1
            if r["container"]:
                a["containers"].add(r["container"])
        app_list = []
        for a in apps.values():
            a["cpu"] = round(a["cpu"], 1)
            a["cpuSystem"] = round(a["cpu"] / self.ncpu, 1)
            a["containers"] = sorted(a["containers"])
            app_list.append(a)
        app_list.sort(key=lambda a: a["cpu"], reverse=True)
        self.rows = rows
        self.apps = app_list
        self.last_sample = time.time()

    def snapshot(self, limit: int = 400) -> Dict[str, Any]:
        return {
            "processes": self.rows[:limit],
            "apps": self.apps,
            "count": len(self.rows),
            "threads": sum(r["threads"] for r in self.rows),
            "ncpu": self.ncpu,
            "at": self.last_sample,
        }
