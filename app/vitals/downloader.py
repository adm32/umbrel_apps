"""YouTube (and other sites) to MP3 / WAV / MP4 with yt-dlp.

Files land in the Umbrel's Home → Downloads → YouTube folder, owned by the
Umbrel user so the Files app and network shares can manage them. yt-dlp
breaks whenever YouTube changes, so it can be updated in place (into the
app's data folder) without a new Vitals release.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import config
from .events import events
from .util import Job, load_json, run, save_json, stream

PYLIB = str(config.DATA / "pylib")          # updated yt-dlp lives here, ahead of the bundled one
HISTORY = config.DATA / "downloads.json"
UMBREL_UID = 1000

AUDIO = {"mp3": ["-x", "--audio-format", "mp3", "--audio-quality", "320K"],
         "wav": ["-x", "--audio-format", "wav"]}
HEIGHTS = {"best": None, "1080": 1080, "720": 720, "480": 480}
PROGRESS = ("download:VPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
            "%(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s")


def dest_dir() -> str:
    return os.path.join(config.UMBREL_ROOT_HOST, "home", "Downloads", "YouTube")


def display_dir() -> str:
    return "Home / Downloads / YouTube"


def env() -> Dict[str, str]:
    e = dict(os.environ)
    e["PYTHONPATH"] = PYLIB + (os.pathsep + e["PYTHONPATH"] if e.get("PYTHONPATH") else "")
    return e


def ytdlp_cmd(*args: str) -> List[str]:
    return [sys.executable, "-m", "yt_dlp", "--ignore-config", *args]


def check_url(url: str) -> str:
    """Only real web addresses: no options smuggled in, no local network targets."""
    url = (url or "").strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Paste a link that starts with https://")
    host = u.hostname.lower()
    if host in ("localhost",) or host.endswith((".local", ".internal", ".lan")):
        raise ValueError("Links to your own network can't be downloaded")
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("Links to your own network can't be downloaded")
    except ValueError as e:
        if "own network" in str(e):
            raise
    if len(url) > 2000:
        raise ValueError("That link is too long")
    return url


def build_args(fmt: str, quality: str, dest: str) -> List[str]:
    if fmt not in ("mp3", "wav", "mp4"):
        raise ValueError("Pick MP3, WAV or MP4")
    args = ["--no-playlist", "--newline", "--progress", "--no-simulate", "--progress-template", PROGRESS,
            "--print", "after_move:VFILE %(filepath)s", "--no-quiet", "--windows-filenames", "--trim-filenames", "160",
            "--no-mtime", "-P", dest, "-o", "%(title)s [%(id)s].%(ext)s", "--embed-metadata"]
    if fmt in AUDIO:
        args += ["-f", "ba/b"] + AUDIO[fmt]
        if fmt == "mp3":
            args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    else:
        h = HEIGHTS.get(quality, 1080)
        sort = (f"res:{h}," if h else "res,") + "vcodec:h264,acodec:aac"  # h264/aac opens everywhere
        args += ["-f", "bv*+ba/b", "-S", sort, "--merge-output-format", "mp4", "--embed-thumbnail"]
    return args


def parse_progress(line: str) -> Optional[Dict[str, float]]:
    if not line.startswith("VPROG "):
        return None
    vals = line.split()[1:]

    def num(s: str) -> Optional[float]:
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    done, total, est, speed, eta = (num(v) for v in (vals + ["NA"] * 5)[:5])
    total = total or est
    return {"done": done or 0.0, "total": total or 0.0, "speed": speed or 0.0, "eta": eta or 0.0,
            "fraction": (done / total) if done and total else 0.0}


def friendly_error(text: str) -> str:
    t = text.lower()
    if "sign in to confirm" in t or "not a bot" in t:
        return "YouTube is asking for a sign-in check from your Umbrel's internet address. Try again later."
    if "private video" in t:
        return "That video is private"
    if "unavailable" in t or "removed" in t:
        return "That video isn't available"
    if "unsupported url" in t:
        return "That site isn't supported"
    if "copyright" in t or "blocked" in t:
        return "That video is blocked in your country or by its owner"
    if "requested format is not available" in t or "nsig" in t or "challenge" in t:
        return "YouTube changed something. Press “Update downloader” and try again."
    last = [ln for ln in text.strip().splitlines() if ln.strip()]
    return (last[-1] if last else "Download failed")[:240]


class Downloader:
    def __init__(self) -> None:
        self.history: List[Dict[str, Any]] = load_json(HISTORY, [])
        self.updating = False

    def save(self) -> None:
        save_json(HISTORY, self.history[-300:])

    def items(self) -> List[Dict[str, Any]]:
        out = []
        for h in reversed(self.history):
            h = dict(h)
            h["exists"] = os.path.exists(h.get("path", ""))
            h.pop("path", None)
            out.append(h)
        return out

    def find(self, item_id: str) -> Optional[Dict[str, Any]]:
        return next((h for h in self.history if h["id"] == item_id), None)

    def safe_path(self, item_id: str) -> Optional[str]:
        h = self.find(item_id)
        if not h:
            return None
        p = os.path.normpath(h["path"])
        root = os.path.normpath(dest_dir())
        if not p.startswith(root + os.sep) or os.path.islink(p) or not os.path.isfile(p):
            return None
        return p

    def delete(self, item_id: str) -> bool:
        p = self.safe_path(item_id)
        if p:
            os.unlink(p)
        before = len(self.history)
        self.history = [h for h in self.history if h["id"] != item_id]
        self.save()
        return before != len(self.history)

    async def version(self) -> str:
        rc, out, _ = await run_env(ytdlp_cmd("--version"), 20)
        return out.strip() if rc == 0 else ""

    async def info(self, url: str) -> Dict[str, Any]:
        url = check_url(url)
        rc, out, err = await run_env(ytdlp_cmd("-J", "--no-playlist", "--skip-download", "--", url), 60)
        if rc != 0 or not out.strip().startswith("{"):
            raise RuntimeError(friendly_error(err or out))
        d = json.loads(out)
        heights = sorted({f.get("height") for f in d.get("formats") or [] if f.get("height")}, reverse=True)
        return {"title": d.get("title"), "uploader": d.get("uploader") or d.get("channel"),
                "duration": d.get("duration"), "thumbnail": d.get("thumbnail"), "id": d.get("id"),
                "url": d.get("webpage_url") or url, "maxHeight": heights[0] if heights else None,
                "isLive": bool(d.get("is_live")), "site": d.get("extractor_key")}

    async def download(self, job: Job, url: str, fmt: str, quality: str) -> Dict[str, Any]:
        url = check_url(url)
        dest = dest_dir()
        os.makedirs(dest, exist_ok=True)
        _own(dest)
        cmd = ytdlp_cmd(*build_args(fmt, quality, dest), "--", url)
        job.data.update(format=fmt, url=url)
        files: List[str] = []
        tail: List[str] = []
        phase = {"n": 0}

        def on_line(line: str) -> None:
            p = parse_progress(line)
            if p:
                job.data.update(p)
                job.phase = "Downloading"
                # video + audio come as two downloads: show them as one bar
                job.progress = min(0.9, (phase["n"] + p["fraction"]) / (2 if fmt == "mp4" else 1) * 0.9)
                if p["fraction"] >= 1:
                    phase["n"] = min(phase["n"] + 1, 1)
                return
            if line.startswith("VFILE "):
                files.append(line[6:].strip())
                return
            if "[ExtractAudio]" in line:
                job.phase = f"Converting to {fmt.upper()}"
                job.progress = max(job.progress, 0.92)
            elif "[Merger]" in line:
                job.phase = "Joining video and audio"
                job.progress = max(job.progress, 0.92)
            elif "[EmbedThumbnail]" in line or "[Metadata]" in line:
                job.phase = "Adding cover art"
            elif "[youtube]" in line or "[info]" in line:
                job.phase = "Getting video info"
            tail.append(line)
            del tail[:-30]

        rc = await stream(cmd, on_line, job, timeout=3600, env=env())
        if job.cancelled:
            raise asyncio.CancelledError()
        if rc != 0 or not files:
            raise RuntimeError(friendly_error("\n".join(tail)))
        path = files[-1]
        _own(path)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        title = re.sub(r"\s*\[[\w-]{6,}\]$", "", os.path.splitext(os.path.basename(path))[0])
        item = {"id": uuid.uuid4().hex[:12], "title": title, "name": os.path.basename(path), "path": path,
                "format": fmt, "size": size, "at": time.time(), "url": url}
        self.history.append(item)
        self.save()
        events.add("download", f"Downloaded {title} ({fmt.upper()})")
        out = dict(item)
        out.pop("path")
        return out

    async def update(self) -> str:
        """pip-install the newest yt-dlp (with its YouTube challenge solver) into the data folder."""
        if self.updating:
            raise RuntimeError("Already updating")
        self.updating = True
        try:
            os.makedirs(PYLIB, exist_ok=True)
            rc, out, err = await run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                      "--no-cache-dir", "--upgrade", "--target", PYLIB, "yt-dlp[default]"], 600)
            if rc != 0:
                raise RuntimeError((err or out).strip().splitlines()[-1][:200] if (err or out).strip() else "Update failed")
            return await self.version()
        finally:
            self.updating = False


def _own(path: str) -> None:
    """Hand files to the Umbrel user so Files and network shares can rename/delete them."""
    try:
        os.chown(path, UMBREL_UID, UMBREL_UID)
    except (OSError, AttributeError):
        pass


async def run_env(cmd: List[str], timeout: float) -> Tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=env())
    except FileNotFoundError as e:
        return 127, "", str(e)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", "Timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


downloader = Downloader()
