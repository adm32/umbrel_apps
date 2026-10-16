"""Tiny async Docker Engine API client over the unix socket."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import aiohttp

from . import config
from .util import read_text

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None


class DockerError(Exception):
    pass


_session: Optional[aiohttp.ClientSession] = None


def available() -> bool:
    return os.path.exists(config.DOCKER_SOCK)


async def _sess() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(path=config.DOCKER_SOCK),
            timeout=aiohttp.ClientTimeout(total=180),
        )
    return _session


async def close() -> None:
    if _session and not _session.closed:
        await _session.close()


async def request(method: str, path: str, params: Optional[Dict[str, str]] = None, timeout: float = 60) -> Any:
    if not available():
        raise DockerError("Docker socket not mounted")
    s = await _sess()
    async with s.request(method, "http://docker" + path, params=params,
                         timeout=aiohttp.ClientTimeout(total=timeout)) as r:
        body = await r.read()
        if r.status >= 400:
            try:
                msg = json.loads(body).get("message", "")
            except ValueError:
                msg = body.decode(errors="replace")
            raise DockerError(f"{r.status}: {msg}")
        if not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return body.decode(errors="replace")


async def get(path: str, **params: str) -> Any:
    return await request("GET", path, params or None)


# ---------------------------------------------------------------- Umbrel app metadata

_containers_cache: Dict[str, Any] = {"t": 0.0, "data": {}}


def installed_apps() -> List[str]:
    """App ids from umbrel.yaml (the list umbreld itself uses)."""
    raw = read_text(os.path.join(config.UMBREL_ROOT_HOST, "umbrel.yaml"))
    if not raw:
        return []
    if yaml is not None:
        try:
            doc = yaml.safe_load(raw) or {}
            apps = doc.get("apps") or []
            return [str(a) for a in apps]
        except Exception:
            pass
    # minimal fallback parser for "apps:\n  - id"
    m = re.search(r"^apps:\s*\n((?:\s+-\s*.+\n?)+)", raw, re.M)
    return re.findall(r"-\s*([\w\-]+)", m.group(1)) if m else []


_names: Dict[str, str] = {}
_icons: Dict[str, str] = {}


def app_name(app_id: str) -> str:
    """Human name from the installed app's umbrel-app.yml."""
    if app_id in _names:
        return _names[app_id]
    name = app_id
    icon = ""
    raw = read_text(os.path.join(config.UMBREL_ROOT_HOST, "app-data", app_id, "umbrel-app.yml"))
    if raw:
        m = re.search(r"^name:\s*['\"]?(.+?)['\"]?\s*$", raw, re.M)
        if m:
            name = m.group(1)
        m = re.search(r"^icon:\s*['\"]?(\S+?)['\"]?\s*$", raw, re.M)
        if m:
            icon = m.group(1)
    if not icon:
        icon = f"https://getumbrel.github.io/umbrel-apps-gallery/{app_id}/icon.svg"
    _names[app_id] = name
    _icons[app_id] = icon
    return name


def manifest_ports() -> Dict[int, str]:
    """Browser port of every installed app (the port umbrelOS serves it on)."""
    out: Dict[int, str] = {}
    for app_id in installed_apps():
        raw = read_text(os.path.join(config.UMBREL_ROOT_HOST, "app-data", app_id, "umbrel-app.yml")) or ""
        m = re.search(r"^port:\s*(\d+)", raw, re.M)
        if m:
            out[int(m.group(1))] = app_name(app_id)
    return out


def app_icon(app_id: str) -> str:
    app_name(app_id)
    return _icons.get(app_id, "")


async def containers(max_age: float = 15.0) -> Dict[str, Dict[str, Any]]:
    """Map of full container id -> {name, app, appName, state, image}."""
    now = time.monotonic()
    if now - _containers_cache["t"] < max_age:
        return _containers_cache["data"]
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for c in await get("/containers/json", all="1"):
            labels = c.get("Labels") or {}
            app = labels.get("com.docker.compose.project", "")
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            out[c["Id"]] = {
                "name": name,
                "app": app,
                "appName": app_name(app) if app else name,
                "service": labels.get("com.docker.compose.service", ""),
                "state": c.get("State"),
                "image": c.get("Image"),
                "imageId": c.get("ImageID"),
                "ports": sorted({p["PublicPort"] for p in c.get("Ports") or [] if p.get("PublicPort")}),
            }
    except Exception:
        out = _containers_cache["data"]
    _containers_cache.update(t=now, data=out)
    return out
