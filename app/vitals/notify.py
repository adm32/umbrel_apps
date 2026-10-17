"""Push notifications for alerts: ntfy, Discord, Telegram or a generic webhook."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional

import aiohttp

from . import config, settings
from .events import events
from .util import load_json, save_json

LEVELS = {"info": 0, "good": 0, "warning": 1, "critical": 2}
REMIND_AFTER = 12 * 3600   # re-send an alert that's still active
RESOLVE_AFTER = 60         # an alert must be gone this long before it counts as fixed


class NotifyError(Exception):
    pass


async def send(session: aiohttp.ClientSession, title: str, message: str, level: str = "warning",
               cfg: Optional[Dict[str, Any]] = None) -> None:
    cfg = cfg or settings.get()["notify"]
    kind = cfg.get("type", "none")
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        if kind == "ntfy":
            server = (cfg.get("url") or "https://ntfy.sh").rstrip("/")
            topic = (cfg.get("topic") or "").strip().strip("/")
            if not topic:
                raise NotifyError("Pick an ntfy topic name")
            headers = {"Title": title.encode("utf-8").decode("latin-1", "replace"),
                       "Priority": {"critical": "5", "warning": "4"}.get(level, "3"),
                       "Tags": {"critical": "rotating_light", "warning": "warning", "good": "white_check_mark"}.get(level, "information_source")}
            if cfg.get("token"):
                headers["Authorization"] = f"Bearer {cfg['token']}"
            async with session.post(f"{server}/{topic}", data=message.encode(), headers=headers, timeout=timeout) as r:
                await _check(r)
        elif kind == "discord":
            url = _url(cfg)
            async with session.post(url, json={"username": "Vitals", "content": f"**{title}**\n{message}"}, timeout=timeout) as r:
                await _check(r)
        elif kind == "telegram":
            token, chat = (cfg.get("token") or "").strip(), (cfg.get("chat") or "").strip()
            if not token or not chat:
                raise NotifyError("Telegram needs a bot token and a chat ID")
            async with session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                    json={"chat_id": chat, "text": f"{title}\n{message}"}, timeout=timeout) as r:
                await _check(r)
        elif kind == "webhook":
            async with session.post(_url(cfg), json={"app": "vitals", "title": title, "message": message, "level": level},
                                    timeout=timeout) as r:
                await _check(r)
        else:
            raise NotifyError("Notifications are turned off")
    except aiohttp.ClientError as e:
        raise NotifyError(f"Couldn't reach the notification service ({e.__class__.__name__})")
    except asyncio.TimeoutError:
        raise NotifyError("The notification service didn't answer")


def _url(cfg: Dict[str, Any]) -> str:
    url = (cfg.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise NotifyError("Enter the full webhook URL (starting with https://)")
    return url


async def _check(r: aiohttp.ClientResponse) -> None:
    if r.status >= 300:
        body = (await r.text())[:160]
        raise NotifyError(f"The service replied {r.status}: {body}")


class Notifier:
    """Turns the sampler's alert list into one-off notifications and timeline events."""

    STATE = config.DATA / "notify_state.json"
    EVENT_KINDS = {"new_device": "warning", "outage": "warning", "lan_outage": "critical", "boot": "warning"}

    def __init__(self) -> None:
        # alerts that were already active before a restart aren't announced twice
        saved = load_json(self.STATE, {})
        self.active: Dict[str, Dict[str, Any]] = {
            k: {"alert": v["alert"], "since": v["since"], "sent": v["sent"], "gone": None} for k, v in saved.items()}
        self.queue: List[Dict[str, Any]] = []
        self.last_error: Optional[str] = None

    def _save(self) -> None:
        save_json(self.STATE, {k: {"alert": v["alert"], "since": v["since"], "sent": v["sent"]}
                               for k, v in self.active.items()})

    def on_event(self, ev: Dict[str, Any]) -> None:
        """Outages, reboots and new devices aren't alerts, but are worth a push."""
        level = self.EVENT_KINDS.get(ev["kind"])
        if level:
            self._enqueue(ev["text"], level, {})

    def process(self, alerts: List[Dict[str, Any]], now: Optional[float] = None) -> None:
        now = now or time.time()
        before = set(self.active)
        seen = set()
        for a in alerts:
            aid = a.get("id") or a["text"]
            seen.add(aid)
            st = self.active.get(aid)
            if st is None:
                self.active[aid] = {"alert": a, "since": now, "sent": now, "gone": None}
                events.add("alert", a["text"], level=a["level"], id=aid)
                self._enqueue(a["text"], a["level"], a)
            else:
                st["alert"] = a
                st["gone"] = None
                if now - st["sent"] > REMIND_AFTER:
                    st["sent"] = now
                    self._enqueue(f"Still happening: {a['text']}", a["level"], a)
        for aid, st in list(self.active.items()):
            if aid in seen:
                continue
            if st["gone"] is None:
                st["gone"] = now
            elif now - st["gone"] >= RESOLVE_AFTER:
                del self.active[aid]
                what = re.sub(r"\s*\([^)]*\)\s*$", "", st["alert"]["text"])  # drop the stale reading
                text = f"Resolved: {what}"
                events.add("resolved", text, id=aid)
                if settings.get()["notify"].get("resolved", True):
                    self._enqueue(text, "good", st["alert"], resolved=True)
        if set(self.active) != before:
            self._save()

    def _enqueue(self, text: str, level: str, alert: Dict[str, Any], resolved: bool = False) -> None:
        cfg = settings.get()["notify"]
        if cfg.get("type", "none") == "none":
            return
        if not resolved and LEVELS.get(level, 0) < LEVELS.get(cfg.get("min_level", "warning"), 1):
            return
        self.queue.append({"title": "Umbrel: " + ("all good" if resolved else ("problem" if level == "critical" else "heads up")),
                           "message": text, "level": level})

    async def flush(self, session: aiohttp.ClientSession) -> None:
        while self.queue:
            item = self.queue.pop(0)
            try:
                await send(session, item["title"], item["message"], item["level"])
                self.last_error = None
            except NotifyError as e:
                if self.last_error != str(e):
                    events.add("notify_error", f"Couldn't send a notification: {e}")
                self.last_error = str(e)
                self.queue.clear()  # don't pile up retries
                return


notifier = Notifier()
