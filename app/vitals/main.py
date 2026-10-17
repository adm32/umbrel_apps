"""HTTP server: JSON API for the web UI, widget endpoints and static files."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict

import aiohttp
from aiohttp import web

from . import config, docker_api, settings, smart, storage
from .util import clean_host, jobs

if config.DEMO:
    from . import demo

    demo.apply()

from . import netlab, network, studio as studio_mod, widgets  # noqa: E402
from .devices import devices, norm_mac, wake  # noqa: E402
from .events import events  # noqa: E402
from .notify import NotifyError, notifier, send as notify_send  # noqa: E402
from .cleaner import Cleaner  # noqa: E402
from .network import SpeedTest  # noqa: E402
from .processes import ProcessMonitor  # noqa: E402
from .sampler import Sampler  # noqa: E402

log = logging.getLogger("vitals")
VERSION = os.environ.get("APP_VERSION", "1.1.0")


class State:
    sampler: Sampler
    procs: ProcessMonitor
    speed: SpeedTest
    cleaner: Cleaner
    studio: studio_mod.Studio
    session: aiohttp.ClientSession
    flush: Any = None
    last_lan_scan: float = 0.0


S = State()
TASKS = web.AppKey("tasks", list)


def ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=_dumps)


def _dumps(o: Any) -> str:
    import json
    return json.dumps(o, default=lambda x: sorted(x) if isinstance(x, set) else str(x), separators=(",", ":"))


def err(msg: str, status: int = 400) -> web.Response:
    return ok({"error": msg}, status)


async def body(request: web.Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def start_job(kind: str, fn, single: bool = True) -> web.Response:
    if single:
        running = jobs.running(kind)
        if running:
            return ok(running.to_dict())
    job = jobs.start(kind, fn)
    return ok(job.to_dict())


# ---------------------------------------------------------------- background loops

async def sampler_loop(app: web.Application) -> None:
    last_save = time.monotonic()
    await asyncio.sleep(config.SAMPLE_INTERVAL)
    while True:
        t0 = time.monotonic()
        try:
            await S.sampler.sample()
        except Exception:
            log.exception("sample failed")
        try:
            notifier.process(S.sampler.snapshot.get("alerts", []))
            if notifier.queue and (S.flush is None or S.flush.done()):
                S.flush = asyncio.ensure_future(notifier.flush(S.session))  # never block sampling on a slow webhook
        except Exception:
            log.exception("notifications failed")
        if S.procs.active:
            try:
                await S.procs.sample(S.sampler.snapshot.get("memory", {}).get("total", 0))
            except Exception:
                log.exception("process sample failed")
        if time.monotonic() - last_save > 300:
            S.sampler.save()
            last_save = time.monotonic()
        await asyncio.sleep(max(0.2, config.SAMPLE_INTERVAL - (time.monotonic() - t0)))


async def slow_loop(app: web.Application) -> None:
    """SMART every 30 min, latency every 5 s, scheduled speed tests."""
    last_smart = float("-inf")
    await asyncio.sleep(1)
    while True:
        try:
            await S.sampler.latency.tick()
        except Exception:
            log.exception("latency tick failed")
        if time.monotonic() - last_smart > 1800:
            last_smart = time.monotonic()
            asyncio.ensure_future(S.sampler.refresh_smart())
        mins = settings.get()["lan_scan_minutes"]
        if mins and not jobs.running("lanscan") and time.time() - S.last_lan_scan > mins * 60:
            S.last_lan_scan = time.time()
            jobs.start("lanscan", lambda job: netlab.lan_scan(job, None))
        hours = settings.get()["auto_speedtest_hours"]
        last = S.speed.latest()
        if hours and not jobs.running("speedtest") and (not last or time.time() - last["at"] > hours * 3600):
            if time.time() - S.speed.last_auto > 600:
                S.speed.last_auto = time.time()
                jobs.start("speedtest", lambda job: S.speed.run(job, S.session))
        await asyncio.sleep(network.LatencyMonitor.INTERVAL)


async def on_startup(app: web.Application) -> None:
    settings.load()
    S.session = aiohttp.ClientSession(headers={"User-Agent": f"Vitals/{VERSION} (umbrelOS app)"})
    S.sampler = Sampler()
    S.sampler.session = S.session
    S.procs = ProcessMonitor()
    S.speed = SpeedTest()
    S.cleaner = Cleaner()
    S.studio = studio_mod.Studio()
    S.flush = None
    S.last_lan_scan = time.time() - 60 * 60 * 24 + 120  # first background scan ~2 min after start
    events.listeners.append(notifier.on_event)
    await S.sampler.sample()
    app[TASKS] = [asyncio.ensure_future(sampler_loop(app)), asyncio.ensure_future(slow_loop(app))]


async def on_cleanup(app: web.Application) -> None:
    for t in app.get(TASKS, []):
        t.cancel()
    try:
        S.sampler.save()
    except Exception:
        log.exception("save failed")
    await S.session.close()
    await docker_api.close()


# ---------------------------------------------------------------- routes

routes = web.RouteTableDef()


@routes.get("/api/live")
async def live(request: web.Request) -> web.Response:
    return ok(S.sampler.snapshot)


@routes.get("/api/history")
async def history(request: web.Request) -> web.Response:
    return ok({"range": request.query.get("range", "5m"), "points": S.sampler.history(request.query.get("range", "5m"))})


@routes.get("/api/system")
async def system(request: web.Request) -> web.Response:
    return ok({**S.sampler.static, "version": VERSION, "demo": config.DEMO,
               "dockerAvailable": docker_api.available() or config.DEMO,
               "smartAvailable": smart.available() or config.DEMO,
               "powerAvailable": bool(S.sampler.rapl.zones) or S.sampler.plug.watts is not None,
               "powerError": S.sampler.rapl.error})


@routes.get("/api/settings")
async def get_settings(request: web.Request) -> web.Response:
    return ok(settings.get())


@routes.put("/api/settings")
async def put_settings(request: web.Request) -> web.Response:
    return ok(settings.update(await body(request)))


# storage --------------------------------------------------------------------

@routes.get("/api/storage")
async def get_storage(request: web.Request) -> web.Response:
    drives = await S.sampler.update_drives(force=request.query.get("refresh") == "1")
    out = []
    for d in drives:
        sm = smart.cached(d["name"]) or await smart.read(d["name"], d["transport"])
        out.append({**d, "io": S.sampler.snapshot.get("disks", {}).get(d["name"]),
                    "ioHistory": list(S.sampler.disk_short.get(d["name"], [])),
                    "smart": {k: v for k, v in sm.items() if k != "attributes"}})
    bench = jobs.latest("benchmark")
    analysis = jobs.latest("analyze")
    return ok({"drives": out, "umbrel": S.sampler.snapshot.get("storage"),
               "targets": storage.writable_targets(),
               "lastBenchmark": bench.result if bench and bench.status == "done" else None,
               "analysis": analysis.result if analysis and analysis.status == "done" else None})


@routes.get("/api/storage/{dev}/smart")
async def get_smart(request: web.Request) -> web.Response:
    dev = request.match_info["dev"]
    d = next((x for x in S.sampler.refresh_drives() if x["name"] == dev), None)
    if not d:
        return err("Unknown drive", 404)
    return ok(await smart.read(dev, d["transport"], force=request.query.get("refresh") == "1"))


@routes.post("/api/storage/{dev}/selftest")
async def selftest(request: web.Request) -> web.Response:
    dev = request.match_info["dev"]
    if dev not in S.sampler.drive_names and not config.DEMO:
        return err("Unknown drive", 404)
    return ok({"message": await smart.self_test(dev)})


@routes.post("/api/storage/benchmark")
async def benchmark(request: web.Request) -> web.Response:
    b = await body(request)
    mount = str(b.get("mountpoint", ""))
    size = int(b.get("sizeMb", 512))
    if size not in (256, 512, 1024, 2048):
        size = 512
    return start_job("benchmark", lambda job: storage.benchmark(job, mount, size))


@routes.post("/api/storage/analyze")
async def analyze(request: web.Request) -> web.Response:
    return start_job("analyze", storage.analyze_apps)


# network --------------------------------------------------------------------

@routes.get("/api/network")
async def get_network(request: web.Request) -> web.Response:
    ifaces, public = await asyncio.gather(network.interfaces(), network.public_info(S.session))
    _, gw = network.default_route()
    return ok({
        "interfaces": ifaces, "gateway": gw, "dns": network.dns_servers(), "public": public,
        "usage": S.sampler.usage.summary(), "latency": S.sampler.latency.summary(),
        "speedtest": {"last": S.speed.latest(), "history": S.speed.history[-30:]},
        "subnet": network.local_subnet(),
    })


@routes.post("/api/speedtest")
async def speedtest(request: web.Request) -> web.Response:
    return start_job("speedtest", lambda job: S.speed.run(job, S.session))


@routes.get("/api/speedtest/history")
async def speedtest_history(request: web.Request) -> web.Response:
    return ok(S.speed.history)


# activity -------------------------------------------------------------------

@routes.get("/api/processes")
async def get_processes(request: web.Request) -> web.Response:
    first = not S.procs.active and not S.procs.rows
    S.procs.touch()
    if first:
        await S.procs.sample(S.sampler.snapshot.get("memory", {}).get("total", 0))
        await asyncio.sleep(1.0)
        await S.procs.sample(S.sampler.snapshot.get("memory", {}).get("total", 0))
    return ok(S.procs.snapshot())


# network lab ----------------------------------------------------------------

@routes.post("/api/tools/{tool}")
async def tool(request: web.Request) -> web.Response:
    name = request.match_info["tool"]
    b = await body(request)
    try:
        if name in ("ping", "traceroute", "portscan"):
            clean_host(str(b.get("host", "")))  # reject bad input before starting anything
        if name == "ping":
            host = str(b.get("host", ""))
            return start_job("ping", lambda job: netlab.ping(job, host, int(b.get("count", 10))), single=False)
        if name == "traceroute":
            host = str(b.get("host", ""))
            return start_job("traceroute", lambda job: netlab.traceroute(job, host, str(b.get("mode", "udp"))), single=False)
        if name == "portscan":
            host = str(b.get("host", ""))
            netlab.parse_ports(str(b.get("ports", "common")))  # validate early
            return start_job("portscan", lambda job: netlab.port_scan(job, host, str(b.get("ports", "common"))), single=False)
        if name == "lanscan":
            return start_job("lanscan", lambda job: netlab.lan_scan(job, b.get("subnet") or None))
        if name == "dns":
            return ok(await netlab.dns_lookup(str(b.get("name", "")), str(b.get("type", "A")), str(b.get("server", "system"))))
    except ValueError as e:
        return err(str(e))
    return err("Unknown tool", 404)


@routes.get("/api/tools/listening")
async def get_listening(request: web.Request) -> web.Response:
    try:
        return ok(await netlab.listening())
    except Exception as e:
        return err(str(e), 500)


@routes.get("/api/tools/lanscan/last")
async def lan_last(request: web.Request) -> web.Response:
    j = jobs.latest("lanscan")
    return ok(j.result if j and j.status == "done" else None)


# LAN transfer test (browser <-> this Umbrel) -------------------------------

_ZEROS = b"\x00" * (1 << 20)


@routes.get("/api/lantest/download")
async def lan_download(request: web.Request) -> web.StreamResponse:
    mb = max(1, min(int(request.query.get("mb", "100")), 2000))
    resp = web.StreamResponse(headers={"Content-Type": "application/octet-stream", "Cache-Control": "no-store",
                                       "Content-Length": str(mb << 20)})
    await resp.prepare(request)
    chunk = os.urandom(1 << 20)  # incompressible, so proxies can't cheat
    try:
        for _ in range(mb):
            await resp.write(chunk)
        await resp.write_eof()
    except (ConnectionError, asyncio.CancelledError):
        pass  # the browser stops reading once it has measured enough
    return resp


@routes.post("/api/lantest/upload")
async def lan_upload(request: web.Request) -> web.Response:
    n = 0
    t0 = time.perf_counter()
    async for chunk in request.content.iter_any():
        n += len(chunk)
    return ok({"bytes": n, "seconds": time.perf_counter() - t0})


# jobs -----------------------------------------------------------------------

@routes.get("/api/jobs/{id}")
async def get_job(request: web.Request) -> web.Response:
    j = jobs.get(request.match_info["id"])
    if not j:
        return err("Job not found", 404)
    return ok(j.to_dict(int(request.query.get("since", "0"))))


@routes.post("/api/jobs/{id}/cancel")
async def cancel_job(request: web.Request) -> web.Response:
    return ok({"cancelled": jobs.cancel(request.match_info["id"])})


# cleaner --------------------------------------------------------------------

@routes.post("/api/cleaner/scan")
async def cleaner_scan(request: web.Request) -> web.Response:
    return start_job("cleanscan", S.cleaner.scan)


@routes.get("/api/cleaner")
async def cleaner_state(request: web.Request) -> web.Response:
    return ok({"scan": S.cleaner.scan_result, "stats": S.cleaner.stats})


@routes.post("/api/cleaner/clean")
async def cleaner_clean(request: web.Request) -> web.Response:
    b = await body(request)
    sel = b.get("selection") or {}
    if not isinstance(sel, dict):
        return err("Bad selection")
    sel = {str(k): [str(i) for i in v] for k, v in sel.items() if isinstance(v, list)}
    scan_id = str(b.get("scanId", ""))
    return start_job("clean", lambda job: S.cleaner.clean(job, scan_id, sel))


# studio ---------------------------------------------------------------------

@routes.get("/api/studio/browse")
async def studio_browse(request: web.Request) -> web.Response:
    try:
        return ok(studio_mod.browse(request.query.get("path") or None))
    except ValueError as e:
        return err(str(e))


@routes.post("/api/studio/scan")
async def studio_scan(request: web.Request) -> web.Response:
    b = await body(request)
    path = str(b.get("path", ""))
    return start_job("studioscan", lambda job: S.studio.scan(job, path))


@routes.get("/api/studio")
async def studio_state(request: web.Request) -> web.Response:
    return ok({"last": S.studio.last, "drives": storage.writable_targets()})


@routes.post("/api/studio/delete")
async def studio_delete(request: web.Request) -> web.Response:
    b = await body(request)
    ids = [str(i) for i in (b.get("ids") or [])][:5000]
    try:
        return ok(S.studio.delete(ids))
    except ValueError as e:
        return err(str(e))


# timeline, notifications, devices, export -----------------------------------

@routes.get("/api/events")
async def get_events(request: web.Request) -> web.Response:
    limit = max(1, min(int(request.query.get("limit", "50")), 1000))
    return ok({"events": events.recent(limit, request.query.get("kind") or None),
               "uptime": S.sampler.latency.uptime()})


@routes.post("/api/notify/test")
async def notify_test(request: web.Request) -> web.Response:
    b = await body(request)
    cfg = {**settings.get()["notify"], **(b.get("notify") or {})}
    try:
        await notify_send(S.session, "Vitals test", "Notifications from your Umbrel are working.", "good", cfg)
    except NotifyError as e:
        return err(str(e))
    return ok({"sent": True})


@routes.get("/api/devices")
async def get_devices(request: web.Request) -> web.Response:
    j = jobs.latest("lanscan")
    last = j.result if j and j.status == "done" else None
    if last:
        devices.annotate(last["devices"])
    return ok({"last": last, "known": devices.known, "subnet": network.local_subnet()})


@routes.put("/api/devices/{mac}")
async def rename_device(request: web.Request) -> web.Response:
    b = await body(request)
    try:
        return ok(devices.rename(request.match_info["mac"], str(b.get("nickname", ""))))
    except ValueError as e:
        return err(str(e))


@routes.post("/api/tools/wol")
async def wol(request: web.Request) -> web.Response:
    b = await body(request)
    try:
        mac = norm_mac(str(b.get("mac", "")))
        return ok({"message": await wake(mac, network.local_subnet())})
    except ValueError as e:
        return err(str(e))
    except RuntimeError as e:
        return err(str(e), 500)


def _csv(name: str, header: list, rows: list) -> web.Response:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return web.Response(text=buf.getvalue(), content_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


@routes.get("/api/export/{what}")
async def export(request: web.Request) -> web.Response:
    what = request.match_info["what"]
    if what == "history.csv":
        pts = S.sampler.history(request.query.get("range", "24h"))
        return _csv("vitals-history.csv",
                    ["time", "cpu_percent", "memory_percent", "cpu_temp_c", "power_w", "net_down_bytes_s",
                     "net_up_bytes_s", "disk_read_bytes_s", "disk_write_bytes_s"],
                    [[_iso(p["t"]), p.get("cpu"), p.get("mem"), p.get("temp"), p.get("power"), p.get("rx"),
                      p.get("tx"), p.get("dr"), p.get("dw")] for p in pts])
    if what == "speedtests.csv":
        return _csv("vitals-speedtests.csv",
                    ["time", "download_mbps", "upload_mbps", "ping_ms", "jitter_ms", "bufferbloat", "isp", "server"],
                    [[_iso(r["at"]), r.get("download"), r.get("upload"), r.get("ping"), r.get("jitter"),
                      r.get("bufferbloat"), r.get("isp"), r.get("colo")] for r in S.speed.history])
    if what == "events.csv":
        return _csv("vitals-events.csv", ["time", "kind", "level", "text"],
                    [[_iso(e["at"]), e["kind"], e["level"], e["text"]] for e in reversed(events.recent(1000))])
    return err("Unknown export", 404)


# widgets (fetched by umbreld, not through the app proxy) ---------------------

@routes.get("/widgets/{id}")
async def widget(request: web.Request) -> web.Response:
    wid = request.match_info["id"]
    if wid == "apps":
        S.procs.touch()
        if not S.procs.rows:
            await S.procs.sample(S.sampler.snapshot.get("memory", {}).get("total", 0))
    ctx = {
        "today": S.sampler.usage.summary()["today"],
        "drives": S.sampler.drive_names,
        "driveList": S.sampler.drives,
        "speedtest": S.speed.latest(),
        "apps": S.procs.apps,
    }
    data = widgets.build(wid, S.sampler.snapshot, ctx)
    if data is None:
        return err("Unknown widget", 404)
    return ok(data)


# static ---------------------------------------------------------------------

@routes.get("/")
async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def errors_mw(request: web.Request, handler):
    if request.method in ("POST", "PUT", "DELETE") and request.path.startswith("/api/"):
        # Cross-site forms and "simple" requests can't send application/json without a
        # CORS preflight (which we never answer), so this blocks CSRF from other sites/apps.
        # (No Origin/Host comparison: Umbrel's proxy may rewrite Host.)
        if request.path != "/api/lantest/upload" and request.content_type != "application/json":
            return err("Expected a JSON request", 415)
    try:
        resp = await handler(request)
    except web.HTTPException:
        raise
    except ValueError as e:
        return err(str(e))
    except Exception as e:  # keep the UI informed instead of a bare 500
        log.exception("request failed: %s", request.path)
        return err(f"{e.__class__.__name__}: {e}", 500)
    if request.path.startswith(("/js/", "/css/", "/img/", "/fonts/")):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def make_app() -> web.Application:
    app = web.Application(middlewares=[errors_mw], client_max_size=4 * 1024 ** 3)
    app.add_routes(routes)
    for sub in ("js", "css", "img", "fonts"):
        p = config.WEB_DIR / sub
        if p.is_dir():
            app.router.add_static(f"/{sub}/", p)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    web.run_app(make_app(), host="0.0.0.0", port=config.PORT, access_log=None, print=None)


if __name__ == "__main__":
    main()
