"""The HTTP API end to end (real code, fake host)."""
import asyncio
import unittest

import tests  # noqa: F401

from aiohttp.test_utils import TestClient, TestServer

from vitals import main


class Api(unittest.TestCase):
    def test_endpoints(self):
        asyncio.run(self._run())

    async def _run(self):
        async with TestClient(TestServer(main.make_app())) as c:
            await asyncio.sleep(2.5)  # let the sampler take two samples

            for url in ("/", "/api/live", "/api/system", "/api/history?range=24h", "/api/storage", "/api/network",
                        "/api/processes", "/api/settings", "/api/cleaner", "/api/studio", "/api/events",
                        "/api/devices", "/api/studio/browse", "/api/yt", "/css/app.css", "/js/app.js"):
                r = await c.get(url)
                self.assertEqual(r.status, 200, url)

            live = await (await c.get("/api/live")).json()
            for key in ("cpu", "memory", "net", "disks", "power", "alerts"):
                self.assertIn(key, live)
            self.assertGreater(live["memory"]["total"], 0)

            for wid in ("system", "custom", "network", "thermals", "power", "drives", "speedtest", "apps"):
                w = await (await c.get(f"/widgets/{wid}")).json()
                self.assertIn("refresh", w, wid)
            self.assertEqual((await c.get("/widgets/nope")).status, 404)

            # CSRF guard: writes must be JSON
            r = await c.post("/api/cleaner/scan", data="x", headers={"Content-Type": "text/plain"})
            self.assertEqual(r.status, 415)
            r = await c.put("/api/settings", data="temp_unit=F",
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
            self.assertEqual(r.status, 415)

            # settings round trip
            s = await (await c.put("/api/settings", json={"temp_unit": "F"})).json()
            self.assertEqual(s["temp_unit"], "F")
            await c.put("/api/settings", json={"temp_unit": "C"})

            # bad tool input is rejected before anything runs
            for tool, payload in (("ping", {"host": "1.1.1.1;reboot"}), ("traceroute", {"host": "-x"}),
                                  ("portscan", {"host": "a b"}), ("wol", {"mac": "nope"})):
                r = await c.post(f"/api/tools/{tool}", json=payload)
                self.assertEqual(r.status, 400, tool)

            # downloader: only web links, only its own files, JSON-only writes
            self.assertEqual((await c.post("/api/yt/info", json={"url": "http://127.0.0.1:3000/"})).status, 400)
            self.assertEqual((await c.post("/api/yt/download", json={"url": "file:///etc/passwd", "format": "mp3"})).status, 400)
            self.assertEqual((await c.get("/api/yt/file/nope")).status, 404)
            self.assertEqual((await c.post("/api/yt/delete", data="id=x",
                                           headers={"Content-Type": "application/x-www-form-urlencoded"})).status, 415)

            # devices
            r = await c.put("/api/devices/AA:BB:CC:DD:EE:01", json={"nickname": "Studio Mac"})
            self.assertEqual((await r.json())["nickname"], "Studio Mac")
            self.assertEqual((await c.put("/api/devices/zz", json={"nickname": "x"})).status, 400)

            # notifications off -> friendly error
            r = await c.post("/api/notify/test", json={})
            self.assertEqual(r.status, 400)
            self.assertIn("off", (await r.json())["error"])

            # exports
            for name in ("history.csv", "speedtests.csv", "events.csv"):
                r = await c.get(f"/api/export/{name}")
                self.assertEqual(r.status, 200, name)
                self.assertTrue((await r.text()).startswith("time,"), name)

            # a background job runs and finishes
            job = await (await c.post("/api/cleaner/scan", json={})).json()
            for _ in range(80):
                job = await (await c.get(f"/api/jobs/{job['id']}")).json()
                if job["status"] != "running":
                    break
                await asyncio.sleep(0.25)
            self.assertEqual(job["status"], "done", job.get("error"))


if __name__ == "__main__":
    unittest.main()
