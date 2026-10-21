"""Sign-in: only through a public domain, never at home or over Tailscale; hard to brute-force."""
import asyncio
import base64
import re
import unittest

import tests  # noqa: F401

from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDict

from vitals import auth as A
from vitals import main

TUNNEL = {"Host": "vitals.admcore.hu", "Cf-Connecting-Ip": "81.2.69.9", "X-Forwarded-For": "81.2.69.9",
          "X-Forwarded-Proto": "https"}


def H(**kw):
    return CIMultiDict({k.replace("_", "-"): v for k, v in kw.items()})


def reset():
    a = A.auth
    a.password, a.totp, a.sessions, a.known_ips = None, {}, {}, []
    a.fails.clear()
    a.recent_fails.clear()
    try:
        A.SETUP_CODE_FILE.unlink()
    except OSError:
        pass
    a._write_setup_code()


class WhoIsAsking(unittest.TestCase):
    def test_home_and_tailscale_are_local(self):
        self.assertTrue(A.is_local(H(), "192.168.1.20", "umbrel.local:7468"))
        self.assertTrue(A.is_local(H(), "192.168.1.20", "192.168.1.50:7468"))
        self.assertTrue(A.is_local(H(), "100.82.10.4", "100.82.237.74:7468"))           # Tailscale IP
        self.assertTrue(A.is_local(H(), "100.82.10.4", "umbrel.tail1234.ts.net:7468"))  # MagicDNS
        # through umbrelOS's own proxy on the LAN
        self.assertTrue(A.is_local(H(X_Forwarded_For="192.168.1.20"), "10.21.0.1", "umbrel.local:7468"))

    def test_the_domain_is_never_local(self):
        self.assertFalse(A.is_local(CIMultiDict(TUNNEL), "10.21.0.9", "vitals.admcore.hu"))
        # the tunnel told to rewrite Host still carries Cloudflare's visitor header
        self.assertFalse(A.is_local(H(Cf_Connecting_Ip="81.2.69.9"), "10.21.0.9", "umbrel.local:7468"))
        # a proxy that adds no headers at all: the public hostname still gives it away
        self.assertFalse(A.is_local(H(), "10.21.0.9", "vitals.admcore.hu"))

    def test_spoofed_forwarded_for_doesnt_work(self):
        # attacker sends "X-Forwarded-For: 192.168.1.5"; the proxy appends their real address
        h = H(X_Forwarded_For="192.168.1.5, 62.4.21.7")
        self.assertEqual(A.client_ip(h, "10.21.0.9"), "62.4.21.7")
        self.assertFalse(A.is_local(h, "10.21.0.9", "umbrel.local:7468"))

    def test_widgets_only_from_inside(self):
        self.assertTrue(A.is_internal(H(), "10.21.0.1"))                                   # umbreld
        self.assertFalse(A.is_internal(CIMultiDict(TUNNEL), "10.21.0.9"))
        self.assertFalse(A.is_internal(H(X_Forwarded_For="10.0.0.5"), "10.21.0.9"))
        self.assertFalse(A.is_internal(H(), "81.2.69.9"))


class Secrets(unittest.TestCase):
    def test_password_hashing(self):
        rec = A.hash_password("correct horse battery")
        self.assertNotIn("correct", str(rec))
        self.assertTrue(A.check_password("correct horse battery", rec))
        self.assertFalse(A.check_password("correct horse batterY", rec))
        self.assertIsNotNone(A.password_problem("short"))
        self.assertIsNotNone(A.password_problem("aaaaaaaaaaaa"))
        self.assertIsNone(A.password_problem("studio-night-2026"))

    def test_totp_rfc6238_vectors(self):
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(A.totp_code(secret, 59 // 30), "287082")
        self.assertEqual(A.totp_code(secret, 1111111109 // 30), "081804")
        self.assertEqual(A.totp_code(secret, 2000000000 // 30), "279037")

    def test_totp_window_and_no_reuse(self):
        s = A.totp_secret()
        now = 1_800_000_000
        code = A.totp_code(s, now // 30)
        step = A.totp_match(s, code, now)
        self.assertEqual(step, now // 30)
        self.assertIsNotNone(A.totp_match(s, code, now + 30))  # 30 s of clock drift is fine
        self.assertIsNone(A.totp_match(s, code, now + 120))
        self.assertIsNone(A.totp_match(s, code, now, last_step=step), "a code works only once")
        self.assertIsNone(A.totp_match(s, "12345", now))


class BruteForce(unittest.TestCase):
    def setUp(self):
        reset()

    def test_lockout_grows(self):
        a = A.auth
        for _ in range(4):
            self.assertEqual(a.record_failure("62.4.21.7", now=1000), 0)
        self.assertEqual(a.record_failure("62.4.21.7", now=1000), 60)
        self.assertEqual(a.record_failure("62.4.21.7", now=1000), 120)
        self.assertGreater(a.locked_for("62.4.21.7", now=1001), 100)
        self.assertEqual(a.locked_for("81.2.69.1", now=1001), 0, "other visitors aren't locked out")
        for _ in range(20):
            a.record_failure("62.4.21.7", now=1000)
        self.assertEqual(a.locked_for("62.4.21.7", now=1000), 3600, "capped at an hour")

    def test_many_addresses_trigger_the_global_brake(self):
        a = A.auth
        for i in range(30):
            a.record_failure(f"62.4.21.{i}", now=5000)
        self.assertEqual(a.global_delay(now=5001), 3.0)
        self.assertEqual(a.global_delay(now=5000 + 700), 0.0)

    def test_sessions_expire(self):
        a = A.auth
        t = a.create_session("81.2.69.9", "Safari", remember=False, now=1000)
        self.assertIsNotNone(a.check_session(t, now=1000 + 3600))
        self.assertIsNone(a.check_session(t, now=1000 + A.SESSION_SHORT + 1))
        long = a.create_session("81.2.69.9", "Safari", remember=True, now=1000)
        self.assertIsNone(a.check_session(long, now=1000 + A.IDLE_LIMIT + 10), "idle too long")
        keep = a.create_session("81.2.69.9", "A", True)
        other = a.create_session("81.2.69.9", "B", True)
        a.end_all(keep=keep)
        self.assertIsNotNone(a.check_session(keep))
        self.assertIsNone(a.check_session(other))
        self.assertNotIn(keep, str(a.sessions), "tokens are stored hashed")


class LoginFlow(unittest.TestCase):
    def setUp(self):
        reset()

    def test_flow(self):
        asyncio.run(self._run())

    async def _run(self):
        jar = {}

        def T():
            # a browser on https sends the Secure cookie back; the plain-http test client won't, so we do
            return {**TUNNEL, **({"Cookie": f"vitals_session={jar['t']}"} if jar.get("t") else {})}

        def keep(r):
            m = re.search(r"vitals_session=([^;]*)", r.headers.get("Set-Cookie", ""))
            if m:
                jar["t"] = m.group(1) or None
            return r

        async with TestClient(TestServer(main.make_app())) as c:
            # at home: no login, no matter what
            self.assertEqual((await c.get("/api/live")).status, 200)
            self.assertEqual((await c.get("/", allow_redirects=False)).status, 200)

            # through the domain with no password set: everything is closed
            r = await c.get("/", headers=T(), allow_redirects=False)
            self.assertEqual((r.status, r.headers["Location"]), (302, "/login"))
            self.assertEqual((await c.get("/api/live", headers=T())).status, 401)
            self.assertEqual((await c.get("/api/settings", headers=T())).status, 401)
            self.assertEqual((await c.get("/js/app.js", headers=T(), allow_redirects=False)).status, 302)
            self.assertEqual((await c.get("/widgets/system", headers=T())).status, 401)
            self.assertEqual((await c.get("/widgets/system")).status, 200, "umbreld can still fetch widgets")
            for path in ("/login", "/js/login.js", "/css/app.css", "/api/auth/status"):
                self.assertEqual((await c.get(path, headers=T())).status, 200, path)
            st = await (await c.get("/api/auth/status", headers=T())).json()
            self.assertEqual((st["configured"], st["local"], st["required"]), (False, False, True))

            # remote setup needs the code from the app's data folder
            r = await c.post("/api/auth/setup", headers=T(), json={"password": "studio-night-2026"})
            self.assertEqual(r.status, 403)
            r = keep(await c.post("/api/auth/setup", headers=T(), json={"password": "studio-night-2026",
                                                                       "setupCode": A.auth.setup_code()}))
            self.assertEqual(r.status, 200)
            self.assertIn("HttpOnly", r.headers["Set-Cookie"])
            self.assertIn("Secure", r.headers["Set-Cookie"], "https through the tunnel -> Secure cookie")
            self.assertFalse(A.SETUP_CODE_FILE.exists(), "setup code is deleted once used")
            self.assertEqual((await c.get("/api/live", headers=T())).status, 200)

            # sign out, then wrong passwords lock the visitor out (the wrong setup code above counted too)
            A.auth.fails.clear()
            keep(await c.post("/api/auth/logout", headers=T(), json={}))
            self.assertEqual((await c.get("/api/live", headers=T())).status, 401)
            codes = [(await c.post("/api/auth/login", headers=T(), json={"password": "nope-nope-nope"})).status
                     for _ in range(6)]
            self.assertEqual(codes[:4], [401] * 4)
            self.assertEqual(codes[4:], [429, 429])
            r = await c.post("/api/auth/login", headers=T(), json={"password": "studio-night-2026"})
            self.assertEqual(r.status, 429, "even the right password waits out the lock")
            A.auth.fails.clear()

            r = keep(await c.post("/api/auth/login", headers=T(), json={"password": "studio-night-2026", "remember": True}))
            self.assertEqual(r.status, 200)

            # turn on two-factor, then signing in needs the code
            setup = await (await c.get("/api/auth/totp/setup", headers=T())).json()
            self.assertIn("<svg", setup["svg"])
            code = A.totp_code(setup["secret"], int(__import__("time").time() // 30))
            self.assertEqual((await c.post("/api/auth/totp/enable", headers=T(), json={"code": code})).status, 200)
            keep(await c.post("/api/auth/logout", headers=T(), json={}))
            r = await c.post("/api/auth/login", headers=T(), json={"password": "studio-night-2026"})
            self.assertEqual((r.status, (await r.json()).get("needCode")), (401, True))
            r = await c.post("/api/auth/login", headers=T(), json={"password": "studio-night-2026", "code": "000000"})
            self.assertIn(r.status, (401, 429))
            A.auth.totp["lastStep"] = -1
            code = A.totp_code(A.auth.totp["secret"], int(__import__("time").time() // 30))
            r = keep(await c.post("/api/auth/login", headers=T(), json={"password": "studio-night-2026", "code": code}))
            self.assertEqual(r.status, 200)

            # security headers
            r = await c.get("/", headers=T())
            self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            self.assertIn("max-age", r.headers["Strict-Transport-Security"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")

            # at home it still just opens
            keep(await c.post("/api/auth/logout", headers=T(), json={}))
            self.assertEqual((await c.get("/api/live", headers=T())).status, 401)
            self.assertEqual((await c.get("/api/live")).status, 200)


if __name__ == "__main__":
    unittest.main()
