"""Paths and runtime flags.

Inside the Umbrel container the host's root filesystem is bind-mounted at
/host, the host PID namespace is shared (so /proc/1 is the host's init) and
the Docker socket is mounted. Every path can be overridden with an env var
so the server can also run on a dev machine (set VITALS_DEMO=1 there).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEMO = os.environ.get("VITALS_DEMO") == "1"
IS_LINUX = sys.platform.startswith("linux")

HOST = os.environ.get("VITALS_HOST_ROOT", "/host").rstrip("/")
PROC = os.environ.get("VITALS_PROC", "/proc")
SYS = os.environ.get("VITALS_SYS", "/sys")
DATA = Path(os.environ.get("VITALS_DATA", "/data"))
UMBREL_ROOT = os.environ.get("UMBREL_ROOT") or "/home/umbrel/umbrel"
DOCKER_SOCK = os.environ.get("VITALS_DOCKER_SOCK", "/var/run/docker.sock")
WEB_DIR = Path(os.environ.get("VITALS_WEB", Path(__file__).resolve().parent.parent / "web"))
PORT = int(os.environ.get("PORT", "3000"))
APP_ID = os.environ.get("APP_ID", "adam-vitals")

SAMPLE_INTERVAL = 2.0  # seconds between live samples


def host_path(path: str) -> str:
    """Translate an absolute host path into the path we can open."""
    if not path.startswith("/"):
        path = "/" + path
    return HOST + path if HOST else path


def host_sys(rel: str) -> str:
    """Prefer the host's sysfs (correct network namespace) and fall back to ours."""
    p = host_path("/sys/" + rel.lstrip("/"))
    return p if os.path.exists(p) else os.path.join(SYS, rel.lstrip("/"))


def host_proc_net(name: str) -> str:
    """/proc/1/net/* shows the host's network namespace when pid: host is set."""
    p = os.path.join(PROC, "1", "net", name)
    try:
        with open(p):
            return p
    except OSError:
        return os.path.join(PROC, "net", name)


UMBREL_ROOT_HOST = host_path(UMBREL_ROOT)

DATA.mkdir(parents=True, exist_ok=True)
