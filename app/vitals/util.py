from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from . import config


# ---------------------------------------------------------------- file helpers

def read_text(path: str, default: Optional[str] = None) -> Optional[str]:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path: str, default: Optional[int] = None) -> Optional[int]:
    v = read_text(path)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def host_resolve(path: str) -> str:
    """Follow symlinks inside the mounted host filesystem.

    An absolute link like /etc/resolv.conf -> /run/systemd/resolve/stub-resolv.conf
    must be re-rooted under the host mount, or it would point into the container.
    """
    p = config.host_path(path)
    for _ in range(16):
        try:
            target = os.readlink(p)
        except OSError:
            return p
        if target.startswith("/"):
            p = config.host_path(target)
        else:
            p = os.path.normpath(os.path.join(os.path.dirname(p), target))
    return p


def host_read_text(path: str, default: Optional[str] = None) -> Optional[str]:
    return read_text(host_resolve(path), default)


def load_json(path: Path, default: Any) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------- subprocesses

_nsenter_ok: Optional[bool] = None


def nsenter_available() -> bool:
    """True when we can run commands inside the host's network namespace."""
    global _nsenter_ok
    if _nsenter_ok is None:
        _nsenter_ok = bool(
            config.IS_LINUX
            and shutil.which("nsenter")
            and os.path.exists("/proc/1/ns/net")
            and os.access("/proc/1/ns/net", os.R_OK)
        )
    return _nsenter_ok


def host_net(cmd: List[str]) -> List[str]:
    """Prefix a command so it runs in the host network namespace.

    Name resolution inside that namespace would use the container's
    resolv.conf (Docker's 127.0.0.11, unreachable there), so callers must pass
    IP addresses, never hostnames.
    """
    if nsenter_available():
        return ["nsenter", "-t", "1", "-n", "--"] + cmd
    return cmd


def host_all(cmd: List[str]) -> List[str]:
    """Run a host binary in every host namespace (used for journalctl)."""
    return ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "-p", "--"] + cmd


async def run(cmd: List[str], timeout: float = 30.0) -> Tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except (FileNotFoundError, PermissionError) as e:
        return 127, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def stream(cmd: List[str], on_line: Callable[[str], None], job: "Job", timeout: float = 120.0,
                 env: Optional[Dict[str, str]] = None) -> int:
    """Run a command and hand each output line to on_line as it arrives."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
        )
    except (FileNotFoundError, PermissionError) as e:
        on_line(f"error: {e}")
        return 127
    job.proc = proc
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.kill()
            on_line("… stopped (time limit reached)")
            break
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), remaining)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        on_line(line.decode(errors="replace").rstrip("\n"))
    await proc.wait()
    job.proc = None
    return proc.returncode or 0


# ---------------------------------------------------------------- validation

_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$")
_IP6_RE = re.compile(r"^[0-9A-Fa-f:\.]{2,45}$")


def clean_host(value: str) -> str:
    """Accept a hostname, IPv4 or IPv6 address and nothing else."""
    v = (value or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    if _HOST_RE.match(v) or (":" in v and _IP6_RE.match(v)):
        return v
    raise ValueError("Enter a valid hostname or IP address")


# ---------------------------------------------------------------- background jobs

class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "running"
        self.progress = 0.0
        self.phase = ""
        self.lines: List[str] = []
        self.data: Dict[str, Any] = {}
        self.result: Any = None
        self.error: Optional[str] = None
        self.started = time.time()
        self.finished: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.cancelled = False

    def log(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > 2000:
            del self.lines[:500]

    def to_dict(self, since: int = 0) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": round(self.progress, 4),
            "phase": self.phase,
            "lines": self.lines[since:],
            "lineCount": len(self.lines),
            "data": self.data,
            "result": self.result,
            "error": self.error,
            "started": self.started,
            "finished": self.finished,
        }


class Jobs:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def start(self, kind: str, fn: Callable[[Job], Awaitable[Any]]) -> Job:
        job = Job(kind)
        self._jobs[job.id] = job

        async def runner() -> None:
            try:
                job.result = await fn(job)
                job.status = "cancelled" if job.cancelled else "done"
                job.progress = 1.0
            except asyncio.CancelledError:
                job.status = "cancelled"
            except Exception as e:  # surface the message to the UI
                job.status = "error"
                job.error = str(e) or e.__class__.__name__
            finally:
                job.finished = time.time()

        job.task = asyncio.ensure_future(runner())
        self._trim()
        return job

    def running(self, kind: str) -> Optional[Job]:
        for j in self._jobs.values():
            if j.kind == kind and j.status == "running":
                return j
        return None

    def latest(self, kind: str) -> Optional[Job]:
        jobs = [j for j in self._jobs.values() if j.kind == kind]
        return max(jobs, key=lambda j: j.started) if jobs else None

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status != "running":
            return False
        job.cancelled = True
        if job.proc and job.proc.returncode is None:
            try:
                job.proc.kill()
            except ProcessLookupError:
                pass
        return True

    def _trim(self) -> None:
        if len(self._jobs) <= 40:
            return
        done = sorted((j for j in self._jobs.values() if j.status != "running"), key=lambda j: j.started)
        for j in done[: len(self._jobs) - 40]:
            self._jobs.pop(j.id, None)


jobs = Jobs()


# ---------------------------------------------------------------- misc

def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while abs(n) >= 1000 and i < len(units) - 1:
        n /= 1000.0
        i += 1
    return f"{n:.0f} {units[i]}" if i == 0 or n >= 100 else f"{n:.1f} {units[i]}"


def is_within(path: str, roots: List[str]) -> bool:
    """Lexical containment check.

    Host symlinks with absolute targets would resolve against the container's
    root, so we never resolve them: callers walk with followlinks=False and
    refuse symlinked paths instead.
    """
    p = os.path.normpath(path)
    for root in roots:
        r = os.path.normpath(root)
        if p == r or p.startswith(r.rstrip("/") + "/"):
            return True
    return False


def has_symlink_between(root: str, path: str) -> bool:
    """True when any component of path below root is a symlink."""
    root = os.path.normpath(root)
    cur = os.path.normpath(path)
    while cur.startswith(root) and cur != root:
        if os.path.islink(cur):
            return True
        cur = os.path.dirname(cur)
    return False
