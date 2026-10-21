"""Login for Vitals: password (scrypt), optional TOTP 2FA, sessions, brute-force protection.

Vitals can control the whole Umbrel, so when it's reachable from the internet
(e.g. through a Cloudflare Tunnel) it must never be open. Rules:
- No password yet: only the home network may set one, or someone holding the
  one-time setup code written to the app's data folder.
- Every page and API call needs a session, except the login page, its assets,
  and widget data requested from inside the Umbrel (umbreld can't log in).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .events import events
from .util import load_json, save_json

PATH = config.DATA / "auth.json"
SETUP_CODE_FILE = config.DATA / "SETUP-CODE.txt"
COOKIE = "vitals_session"
SESSION_SHORT = 12 * 3600          # without "keep me signed in"
SESSION_LONG = 30 * 86400          # with it
IDLE_LIMIT = 7 * 86400             # unused this long -> signed out
MIN_PASSWORD = 10

FORWARD_HEADERS = ("X-Forwarded-For", "X-Real-Ip", "Forwarded", "Cf-Connecting-Ip", "True-Client-Ip")


# ---------------------------------------------------------------- passwords

def hash_password(password: str, salt: Optional[bytes] = None) -> Dict[str, Any]:
    salt = salt or os.urandom(16)
    n, r, p = 2 ** 15, 8, 1
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return {"salt": salt.hex(), "hash": digest.hex(), "n": n, "r": r, "p": p}


def check_password(password: str, rec: Dict[str, Any]) -> bool:
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(rec["salt"]), n=rec["n"], r=rec["r"],
                            p=rec["p"], maxmem=64 * 1024 * 1024, dklen=32)
    return hmac.compare_digest(digest.hex(), rec["hash"])


def password_problem(password: str) -> Optional[str]:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters"
    if len(set(password)) < 5:
        return "That password is too repetitive"
    if password.lower() in ("password123", "umbrelumbrel", "qwertyuiop", "1234567890", "vitalsvitals"):
        return "That password is too common"
    return None


# ---------------------------------------------------------------- TOTP (RFC 6238)

def totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret: str, code: str, now: Optional[float] = None, last_step: int = -1) -> Optional[int]:
    """Returns the matching time step (±30 s of clock drift), refusing re-used codes."""
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != 6:
        return None
    step = int((now or time.time()) // 30)
    for s in (step, step - 1, step + 1):
        if s > last_step and hmac.compare_digest(totp_code(secret, s), code):
            return s
    return None


def totp_uri(secret: str, label: str = "Vitals") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(label)}?secret={secret}&issuer=Vitals&digits=6&period=30"


# ---------------------------------------------------------------- who is asking

def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local or (a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10"))


def client_ip(headers: Any, peer: Optional[str]) -> str:
    """The real visitor. Proxies append to X-Forwarded-For, so walk it from the right and
    take the first public address; Cloudflare's own header wins when present."""
    cf = (headers.get("Cf-Connecting-Ip") or "").strip()
    if cf:
        return cf
    chain = [x.strip() for x in (headers.get("X-Forwarded-For") or "").split(",") if x.strip()]
    for ip in reversed(chain):
        if not _private(ip):
            return ip
    real = (headers.get("X-Real-Ip") or "").strip()
    if real and not _private(real):
        return real
    return chain[0] if chain else (peer or "")


def is_local(headers: Any, peer: Optional[str], host: str) -> bool:
    """From the home network (or Tailscale), not via a public tunnel or domain."""
    ip = client_ip(headers, peer)
    hostname = (host or "").rsplit(":", 1)[0].strip("[]").lower()
    public_name = "." in hostname and not hostname.endswith((".local", ".lan", ".home", ".internal", ".ts.net")) \
        and not _is_ip(hostname)
    return _private(ip) and not public_name and not headers.get("Cf-Connecting-Ip")


def is_internal(headers: Any, peer: Optional[str]) -> bool:
    """A direct request from inside the Umbrel (umbreld fetching widgets): no proxy in between."""
    return bool(peer) and _private(peer) and not any(headers.get(h) for h in FORWARD_HEADERS)


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- the store

class Auth:
    def __init__(self) -> None:
        d = load_json(PATH, {})
        self.password: Optional[Dict[str, Any]] = d.get("password")
        self.totp: Dict[str, Any] = d.get("totp") or {}
        self.sessions: Dict[str, Dict[str, Any]] = d.get("sessions") or {}
        self.known_ips: List[str] = d.get("knownIps") or []
        self.fails: Dict[str, List[float]] = {}           # ip -> [count, locked_until]
        self.recent_fails: List[float] = []               # all failures, for the global brake
        self.pending_totp: Optional[str] = None
        if not self.password:
            self._write_setup_code()

    # persistence ------------------------------------------------------
    def save(self) -> None:
        save_json(PATH, {"password": self.password, "totp": self.totp, "sessions": self.sessions,
                         "knownIps": self.known_ips[-50:]})
        try:
            os.chmod(PATH, 0o600)
        except OSError:
            pass

    def _write_setup_code(self) -> None:
        if not SETUP_CODE_FILE.exists():
            code = "-".join(secrets.token_hex(2).upper() for _ in range(4))
            SETUP_CODE_FILE.write_text(
                f"{code}\n\nVitals setup code. You only need it when setting the password from outside your home "
                "network. It is deleted once a password is set.\n")
            try:
                os.chmod(SETUP_CODE_FILE, 0o600)
            except OSError:
                pass

    def setup_code(self) -> str:
        try:
            return SETUP_CODE_FILE.read_text().split()[0]
        except (OSError, IndexError):
            return ""

    @property
    def configured(self) -> bool:
        return bool(self.password)

    @property
    def totp_on(self) -> bool:
        return bool(self.totp.get("enabled") and self.totp.get("secret"))

    # brute force ------------------------------------------------------
    def locked_for(self, ip: str, now: Optional[float] = None) -> float:
        now = now or time.time()
        rec = self.fails.get(ip)
        return max(0.0, rec[1] - now) if rec else 0.0

    def global_delay(self, now: Optional[float] = None) -> float:
        """Many failures from anywhere (a botnet) slow every attempt down."""
        now = now or time.time()
        self.recent_fails = [t for t in self.recent_fails if now - t < 600]
        return 3.0 if len(self.recent_fails) >= 30 else 0.0

    def record_failure(self, ip: str, now: Optional[float] = None) -> float:
        now = now or time.time()
        self.recent_fails.append(now)
        rec = self.fails.setdefault(ip, [0, 0.0])
        rec[0] += 1
        if rec[0] >= 5:
            lock = min(3600.0, 60.0 * 2 ** (rec[0] - 5))
            rec[1] = now + lock
            if rec[0] == 5 or rec[0] % 5 == 0:
                events.add("login_fail", f"{int(rec[0])} wrong passwords for Vitals from {ip} — locked for {int(lock // 60)} min",
                           level="warning", ip=ip)
            return lock
        return 0.0

    def record_success(self, ip: str) -> None:
        self.fails.pop(ip, None)

    # sessions ---------------------------------------------------------
    def create_session(self, ip: str, agent: str, remember: bool, now: Optional[float] = None) -> str:
        now = now or time.time()
        token = secrets.token_urlsafe(32)
        self.sessions[_h(token)] = {"created": now, "last": now, "ip": ip, "agent": agent[:160],
                                    "expires": now + (SESSION_LONG if remember else SESSION_SHORT)}
        if ip not in self.known_ips:
            if self.known_ips:  # the very first sign-in isn't news
                events.add("login_new", f"New sign-in to Vitals from {ip}", level="warning", ip=ip)
            self.known_ips.append(ip)
        self.prune(now)
        self.save()
        return token

    def check_session(self, token: Optional[str], now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now = now or time.time()
        s = self.sessions.get(_h(token))
        if not s or s["expires"] < now or now - s["last"] > IDLE_LIMIT:
            return None
        if now - s["last"] > 300:  # don't write to disk on every request
            s["last"] = now
            self.save()
        return s

    def end_session(self, token: Optional[str]) -> None:
        if token and self.sessions.pop(_h(token), None):
            self.save()

    def end_all(self, keep: Optional[str] = None) -> int:
        keep_h = _h(keep) if keep else None
        n = len(self.sessions)
        self.sessions = {k: v for k, v in self.sessions.items() if k == keep_h}
        self.save()
        return n - len(self.sessions)

    def prune(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        self.sessions = {k: v for k, v in self.sessions.items()
                         if v["expires"] > now and now - v["last"] <= IDLE_LIMIT}

    def list_sessions(self, current: Optional[str]) -> List[Dict[str, Any]]:
        cur = _h(current) if current else None
        return sorted(({"id": k[:12], "current": k == cur, **{x: v[x] for x in ("created", "last", "ip", "agent")}}
                       for k, v in self.sessions.items()), key=lambda s: -s["last"])

    def end_session_id(self, sid: str) -> bool:
        for k in list(self.sessions):
            if k[:12] == sid:
                del self.sessions[k]
                self.save()
                return True
        return False

    # password ---------------------------------------------------------
    def set_password(self, password: str) -> None:
        problem = password_problem(password)
        if problem:
            raise ValueError(problem)
        self.password = hash_password(password)
        self.save()
        try:
            SETUP_CODE_FILE.unlink()
        except OSError:
            pass

    def verify(self, password: str, code: str = "") -> Tuple[bool, str]:
        """(ok, reason) — reason is 'password', 'totp' or ''."""
        if not self.password or not check_password(password, self.password):
            return False, "password"
        if self.totp_on:
            step = totp_match(self.totp["secret"], code, last_step=self.totp.get("lastStep", -1))
            if step is None:
                return False, "totp"
            self.totp["lastStep"] = step
        return True, ""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


auth = Auth()
