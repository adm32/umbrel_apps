"""Outage detection, notifications, device registry, settings and widgets."""
import asyncio
import json
import unittest

import tests  # noqa: F401  (sets up the fake host before vitals is imported)

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from vitals import notify, settings, widgets
from vitals.devices import Devices
from vitals.events import events
from vitals.network import LatencyMonitor


def kinds(since_index):
    return [e["kind"] for e in events.items[since_index:]]


class OutageDetection(unittest.TestCase):
    def setUp(self):
        self.m = LatencyMonitor()
        self.start = len(events.items)

    def test_single_lost_ping_is_not_an_outage(self):
        self.m._track(0, 10.0, True)
        self.m._track(5, None, True)
        self.m._track(10, None, True)
        self.assertTrue(self.m.online)  # 2 misses: still online
        self.m._track(15, 11.0, True)
        self.assertEqual(kinds(self.start), [])

    def test_three_misses_is_an_outage_with_duration(self):
        self.m._track(0, 10.0, True)
        for t in (5, 10, 15, 20):
            self.m._track(t, None, True)
        self.assertFalse(self.m.online)
        self.assertEqual(self.m.outage_since, 5)
        self.m._track(245, 12.0, True)
        self.assertTrue(self.m.online)
        ev = events.items[-1]
        self.assertEqual(ev["kind"], "outage")
        self.assertEqual(ev["data"]["seconds"], 240)
        self.assertIn("4 minutes", ev["text"])

    def test_router_also_down_is_a_lan_outage(self):
        for t in (5, 10, 15):
            self.m._track(t, None, False)
        self.m._track(30, 9.0, True)
        self.assertEqual(events.items[-1]["kind"], "lan_outage")

    def test_uptime_percentage(self):
        u = events.uptime(1000, ongoing_since=None, monitored_since=None)
        self.assertLessEqual(u["percent"], 100)
        import time
        now = time.time()
        events.add("outage", "test", start=now - 100, end=now - 40, seconds=60)
        u = events.uptime(600, monitored_since=now - 600)
        self.assertAlmostEqual(u["percent"], 90.0, delta=0.2)


class Notifications(unittest.TestCase):
    def setUp(self):
        settings.update({"notify": {"type": "ntfy", "topic": "t", "min_level": "warning", "resolved": True}})
        self.n = notify.Notifier()
        self.n.active.clear()
        self.start = len(events.items)

    def tearDown(self):
        settings.update({"notify": {"type": "none"}})

    def test_alert_lifecycle(self):
        a = {"id": "cpu_temp", "level": "warning", "page": "sensors", "text": "CPU is running hot (88°C)"}
        self.n.process([a], now=1000)
        self.assertEqual(len(self.n.queue), 1)
        self.n.process([dict(a, text="CPU is running hot (89°C)")], now=1002)
        self.assertEqual(len(self.n.queue), 1, "same alert must not be sent twice")
        self.n.process([a], now=1000 + notify.REMIND_AFTER + 10)
        self.assertEqual(len(self.n.queue), 2, "reminder after 12 hours")
        self.n.process([], now=50000)
        self.assertIn("cpu_temp", self.n.active, "not resolved until it's been gone a minute")
        self.n.process([], now=50000 + notify.RESOLVE_AFTER + 1)
        self.assertNotIn("cpu_temp", self.n.active)
        self.assertEqual(kinds(self.start), ["alert", "resolved"])
        self.assertIn("Back to normal", self.n.queue[-1]["message"])

    def test_min_level_filters_warnings(self):
        settings.update({"notify": {"type": "ntfy", "topic": "t", "min_level": "critical"}})
        self.n.process([{"id": "m", "level": "warning", "text": "Memory is almost full"}], now=1)
        self.assertEqual(self.n.queue, [])
        self.n.process([{"id": "o", "level": "critical", "text": "Offline"}], now=2)
        self.assertEqual(len(self.n.queue), 1)

    def test_active_alerts_survive_a_restart(self):
        self.n.process([{"id": "x", "level": "warning", "text": "Something"}], now=1)
        again = notify.Notifier()
        self.assertIn("x", again.active)
        again.process([{"id": "x", "level": "warning", "text": "Something"}], now=5)
        self.assertEqual(again.queue, [], "restarting Vitals must not re-announce old alerts")

    def test_events_trigger_pushes(self):
        self.n.on_event({"kind": "new_device", "text": "New device joined your network: X"})
        self.n.on_event({"kind": "speedtest", "text": "Speed test"})
        self.assertEqual(len(self.n.queue), 1)

    def test_send_formats(self):
        got = []

        async def handler(request):
            got.append({"path": request.path, "headers": dict(request.headers), "body": await request.read()})
            return web.Response(status=500 if request.path == "/fail" else 200, text="nope")

        async def run():
            app = web.Application()
            app.router.add_route("POST", "/{tail:.*}", handler)
            server = TestServer(app)
            await server.start_server()
            base = f"http://127.0.0.1:{server.port}"
            async with ClientSession() as s:
                await notify.send(s, "Title", "Body", "critical", {"type": "ntfy", "url": base, "topic": "mytopic"})
                await notify.send(s, "Title", "Body", "warning", {"type": "discord", "url": base + "/discord"})
                await notify.send(s, "Title", "Body", "warning", {"type": "webhook", "url": base + "/hook"})
                with self.assertRaises(notify.NotifyError):
                    await notify.send(s, "T", "B", "warning", {"type": "webhook", "url": base + "/fail"})
                with self.assertRaises(notify.NotifyError):
                    await notify.send(s, "T", "B", "warning", {"type": "webhook", "url": "ftp://x"})
                with self.assertRaises(notify.NotifyError):
                    await notify.send(s, "T", "B", "warning", {"type": "none"})
            await server.close()

        asyncio.run(run())
        self.assertEqual(got[0]["path"], "/mytopic")
        self.assertEqual(got[0]["headers"]["Priority"], "5")
        self.assertEqual(got[0]["body"], b"Body")
        self.assertIn("**Title**", json.loads(got[1]["body"])["content"])
        self.assertEqual(json.loads(got[2]["body"])["level"], "warning")


class DeviceRegistry(unittest.TestCase):
    def test_first_scan_is_baseline_then_new_devices_are_reported(self):
        d = Devices()
        d.known.clear()
        d.baseline_done = False
        start = len(events.items)
        self.assertEqual(d.merge([{"ip": "192.168.1.2", "mac": "aa:bb:cc:00:00:01", "vendor": "Apple"}]), [])
        new = d.merge([{"ip": "192.168.1.2", "mac": "AA:BB:CC:00:00:01"},
                       {"ip": "192.168.1.9", "mac": "AA:BB:CC:00:00:09", "vendor": "Espressif"}])
        self.assertEqual([x["ip"] for x in new], ["192.168.1.9"])
        self.assertEqual(kinds(start), ["new_device"])
        d.rename("aa:bb:cc:00:00:09", "  Kitchen plug  ")
        devs = d.annotate([{"ip": "192.168.1.9", "mac": "AA:BB:CC:00:00:09"}])
        self.assertEqual(devs[0]["nickname"], "Kitchen plug")
        self.assertEqual(Devices().known["AA:BB:CC:00:00:09"]["nickname"], "Kitchen plug", "persisted")


class SettingsValidation(unittest.TestCase):
    def test_bad_values_are_coerced(self):
        s = settings.update({"temp_unit": "K", "price_per_kwh": "lots", "net_reset_day": 99, "theme": "neon",
                             "custom_widget": ["cpu", "nope"], "notify": {"type": "carrier-pigeon"},
                             "lan_scan_minutes": 7, "unknown_key": 1})
        self.assertEqual(s["temp_unit"], "C")
        self.assertEqual(s["price_per_kwh"], settings.DEFAULTS["price_per_kwh"])
        self.assertEqual(s["net_reset_day"], 28)
        self.assertEqual(s["theme"], "light")
        self.assertEqual(len(s["custom_widget"]), 4)
        self.assertEqual(s["notify"]["type"], "none")
        self.assertEqual(s["lan_scan_minutes"], 0)
        self.assertNotIn("unknown_key", s)
        s = settings.update({"theme": "home", "temp_unit": "F"})
        self.assertEqual((s["theme"], s["temp_unit"]), ("home", "F"))
        settings.update({"temp_unit": "C", "theme": "light"})


SNAP = {
    "cpu": {"total": 12.3, "temp": 48.0, "load": [0.5, 0.4, 0.3]},
    "memory": {"percent": 41.0},
    "net": {"rx": 3_000_000, "tx": 400_000},
    "power": {"wall": 9.8, "package": 3.8, "monthlyCost": 2.15, "currency": "€", "source": "estimate"},
    "storage": {"total": 1000, "used": 600},
    "disks": {"sda": {"read": 1e6, "write": 2e6}},
    "uptime": 90000, "ping": 12.0,
}
REQUIRED = {
    "four-stats": lambda w: len(w["items"]) == 4 and all({"title", "text", "subtext"} <= set(i) for i in w["items"]),
    "three-stats": lambda w: len(w["items"]) == 3 and all({"icon", "text", "subtext"} <= set(i) for i in w["items"]),
    "two-stats-with-guage": lambda w: len(w["items"]) == 2 and all(0 <= i["progress"] <= 1 for i in w["items"]),
    "text-with-progress": lambda w: {"title", "text", "progressLabel", "progress"} <= set(w),
    "list": lambda w: isinstance(w["items"], list) and "noItemsText" in w,
}


class Widgets(unittest.TestCase):
    def test_every_widget_matches_its_umbrel_type(self):
        ctx = {"today": [100, 50], "drives": [], "driveList": [
            {"name": "sda", "model": "Disk", "kind": "SSD", "used": 5, "fsTotal": 10}],
            "speedtest": {"download": 942, "upload": 41, "ping": 11}, "apps": [
            {"app": "bitcoin", "name": "Bitcoin Node", "cpuSystem": 3.2, "mem": 1e9}]}
        for wid in ("system", "custom", "network", "thermals", "power", "drives", "speedtest", "apps"):
            w = widgets.build(wid, SNAP, ctx)
            self.assertIn(w["type"], REQUIRED, wid)
            self.assertRegex(w["refresh"], r"^\d+[sm]$", wid)  # umbreld parses this with ms()
            self.assertTrue(REQUIRED[w["type"]](w), wid)
        self.assertEqual(widgets.build("network", SNAP, ctx)["items"][0]["subtext"], "Mb/s")
        self.assertIsNone(widgets.build("nope", SNAP, ctx))

    def test_widgets_cope_with_missing_sensors(self):
        empty = {"cpu": {"total": 0, "temp": None, "load": [0]}, "memory": {"percent": 0}, "net": {}, "power": {}}
        for wid in ("system", "thermals", "power"):
            widgets.build(wid, empty, {})


if __name__ == "__main__":
    unittest.main()


class CloudflareLookup(unittest.TestCase):
    """speed.cloudflare.com/meta started returning 403 without a Referer; both paths must work."""

    def test_meta_with_referer_and_header_fallback(self):
        from vitals import network
        seen = {}

        async def meta(request):
            seen["referer"] = request.headers.get("Referer")
            if request.app["mode"] == "ok" and request.headers.get("Referer"):
                return web.json_response({"clientIp": "1.2.3.4", "asOrganization": "ISP", "colo": {"iata": "BUD", "city": "Budapest"}})
            return web.json_response({}, status=403)

        async def down(request):
            return web.Response(headers={"cf-meta-ip": "5.6.7.8", "cf-meta-colo": "AMS"})

        async def run():
            results = []
            for mode in ("ok", "blocked"):
                app = web.Application()
                app["mode"] = mode
                app.router.add_get("/meta", meta)
                app.router.add_get("/__down", down)
                server = TestServer(app)
                await server.start_server()
                old = network.CF
                network.CF = f"http://127.0.0.1:{server.port}"
                try:
                    async with ClientSession() as s:
                        results.append(await network.cf_meta(s))
                finally:
                    network.CF = old
                    await server.close()
            return results

        ok, fallback = asyncio.run(run())
        self.assertEqual(seen["referer"], "https://speed.cloudflare.com/")
        self.assertEqual((ok["clientIp"], ok["colo"]), ("1.2.3.4", "BUD (Budapest)"))
        self.assertEqual((fallback["clientIp"], fallback["colo"]), ("5.6.7.8", "AMS"))
