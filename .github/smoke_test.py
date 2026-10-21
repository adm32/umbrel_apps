"""End-to-end check of the real container on a real Linux host (run by GitHub Actions).

The container runs with the same flags as on umbrelOS (privileged, host PID namespace,
host / at /host, Docker socket), so this exercises the Linux-only code paths:
/proc and /sys parsing, nsenter into the host network, ss, nmap, docker API, du, benchmark.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:7468"
failures = []
notes = []


def req(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        return json.loads(raw) if "json" in ctype else raw.decode()


def get(path):
    return req("GET", path)


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def job(path, body=None, timeout=240):
    j = req("POST", path, body or {})
    t0 = time.time()
    while j["status"] == "running" and time.time() - t0 < timeout:
        time.sleep(1)
        j = get(f"/api/jobs/{j['id']}")
    return j


# wait for the server
for _ in range(90):
    try:
        get("/api/system")
        break
    except Exception:
        time.sleep(1)
else:
    print("server never came up")
    sys.exit(1)
time.sleep(8)  # a few samples

sysinfo = get("/api/system")
check("system: CPU model read from /proc", bool(sysinfo["cpuModel"]), sysinfo["cpuModel"])
check("system: host OS read through /host", "ubuntu" in sysinfo["os"].lower(), sysinfo["os"])
notes.append(f"power sensor: {sysinfo['powerAvailable']} ({sysinfo.get('powerError')})")

live = get("/api/live")
check("live: CPU cores", len(live["cpu"]["cores"]) >= 2, f"{len(live['cpu']['cores'])} cores, {live['cpu']['total']}%")
check("live: memory", live["memory"]["total"] > 0, f"{live['memory']['total'] / 1e9:.1f} GB")
ifaces = live["net"]["ifaces"]
check("live: host network interfaces (via /proc/1/net/dev)", any(v["kind"] == "ethernet" for v in ifaces.values()), ", ".join(ifaces))
check("live: disk I/O per drive", len(live["disks"]) > 0, ", ".join(live["disks"]))
notes.append(f"temperature sensors: {len(live['temps'])}, fans: {len(live['fans'])}")

st = get("/api/storage")
check("storage: drives found", len(st["drives"]) > 0, ", ".join(f"{d['name']} {d['model']} ({d['kind']})" for d in st["drives"]))
check("storage: partition usage", any(p["usage"] for d in st["drives"] for p in d["partitions"]))
check("storage: writable benchmark targets", len(st["targets"]) > 0, ", ".join(t["mountpoint"] for t in st["targets"]))
for d in st["drives"]:
    sm = d["smart"]
    notes.append(f"SMART {d['name']}: {sm.get('health') or sm.get('error')} {sm.get('issues') or ''}")

net = get("/api/network")
default = [i for i in net["interfaces"] if i["default"]]
check("network: default interface via nsenter + ip", bool(default and default[0]["ipv4"]),
      default[0]["name"] + " " + ",".join(default[0]["ipv4"]) if default else "none")
check("network: gateway from /proc/1/net/route", bool(net["gateway"]), str(net["gateway"]))
check("network: DNS servers", len(net["dns"]) > 0, ", ".join(net["dns"]))
check("network: public IP lookup", bool(net["public"] and net["public"].get("ip")))

pr = get("/api/processes")
time.sleep(3)
pr = get("/api/processes")
check("activity: host processes (pid: host)", pr["count"] > 30, f"{pr['count']} processes")
own = [p for p in pr["processes"] if p["container"] == "vitals-smoke"]
check("activity: processes mapped to containers (cgroups + docker API)", bool(own), f"{len(own)} in vitals-smoke")

ls = get("/api/tools/listening")
check("netlab: listening ports via ss in host netns", any(r["port"] == 7468 for r in ls),
      ", ".join(str(r["port"]) for r in ls[:12]))

dns = req("POST", "/api/tools/dns", {"name": "github.com", "type": "A", "server": "system"})
check("netlab: DNS lookup", bool(dns["results"][0].get("records")), str(dns["results"][0].get("records", [])[:2]))

j = job("/api/tools/lanscan")
check("netlab: LAN scan (nmap in host netns)", j["status"] == "done", f"{len(j['result']['devices']) if j['result'] else 0} devices; {j.get('error')}")

j = job("/api/tools/ping", {"host": net["gateway"] or "1.1.1.1", "count": 3})
check("netlab: ping job runs", j["status"] == "done", f"{j.get('result', {}) and j['result'].get('received')} replies (ICMP may be blocked on CI)")

j = job("/api/tools/portscan", {"host": "127.0.0.1", "ports": "22,7468,9"})
check("netlab: port scan (nmap)", j["status"] == "done", str(j.get("result", {}) and j["result"].get("open")))

w = req("POST", "/api/tools/wol", {"mac": "AA:BB:CC:DD:EE:FF"})
check("netlab: Wake-on-LAN from host network", "sent" in w.get("message", ""), w.get("message", ""))

j = job("/api/cleaner/scan", timeout=400)
cats = {c["id"]: c for c in (j.get("result") or {}).get("categories", [])}
check("cleaner: scan finishes", j["status"] == "done", j.get("error") or "")
check("cleaner: Docker categories (docker socket)", "images" in cats and "logs" in cats, ", ".join(cats))
notes.append("cleaner found: " + ", ".join(f"{k} {v['size'] / 1e6:.0f} MB" for k, v in cats.items()))

target = st["targets"][0]["mountpoint"]
j = job("/api/storage/benchmark", {"mountpoint": target, "sizeMb": 256})
r = j.get("result") or {}
check("storage: benchmark", j["status"] == "done" and r.get("writeMBps", 0) > 0,
      f"{target}: write {r.get('writeMBps')} MB/s, read {r.get('readMBps')} MB/s, {r.get('randIops')} IOPS; {j.get('error')}")

j = job("/api/storage/analyze")
check("storage: space by app (du)", j["status"] == "done", j.get("error") or "")

for wid in ("system", "custom", "network", "thermals", "power", "drives", "speedtest", "apps"):
    data = get(f"/widgets/{wid}")
    check(f"widget {wid}", "type" in data and "refresh" in data)

check("events timeline", "events" in get("/api/events"))
check("CSV export", get("/api/export/history.csv").startswith("time,"))
try:
    req("POST", "/api/cleaner/scan", None)
    check("CSRF guard rejects non-JSON writes", False)
except urllib.error.HTTPError as e:
    check("CSRF guard rejects non-JSON writes", e.code == 415)

# ---------------------------------------------------------------- sign-in through a public domain
TUN = {"Host": "vitals.example.com", "Cf-Connecting-Ip": "81.2.69.160", "X-Forwarded-For": "81.2.69.160"}


def tun(method, path, body=None, cookie=None):
    h = dict(TUN, **({"Cookie": cookie} if cookie else {}))
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


code, _, _ = tun("GET", "/api/live")
check("auth: tunnel visitors are blocked before a password exists", code == 401, str(code))
code, _, _ = tun("GET", "/widgets/system")
check("auth: widgets not reachable through the tunnel", code == 401, str(code))
code, _, _ = tun("GET", "/login")
check("auth: login page reachable through the tunnel", code == 200, str(code))
st = req("POST", "/api/auth/setup", {"password": "smoke-test-passw0rd"})
check("auth: password can be set from the home network", st.get("ok") is True)
code, h, _ = tun("POST", "/api/auth/login", {"password": "wrong-password-1"})
check("auth: wrong password refused", code == 401, str(code))
code, h, _ = tun("POST", "/api/auth/login", {"password": "smoke-test-passw0rd"})
cookie = (h.get("Set-Cookie") or "").split(";")[0]
check("auth: right password signs in", code == 200 and cookie.startswith("vitals_session="), str(code))
code, h, _ = tun("GET", "/api/live", cookie=cookie)
check("auth: signed-in tunnel visitor gets data", code == 200, str(code))
check("auth: security headers", "script-src 'self'" in (h.get("Content-Security-Policy") or "") and h.get("X-Frame-Options") == "DENY")
check("auth: home network still needs no login", "cpu" in get("/api/live"))

print("\nNotes:\n  " + "\n  ".join(notes))
if failures:
    print(f"\n{len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("\nAll checks passed.")
