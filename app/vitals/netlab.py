"""Network Lab: ping, traceroute, DNS, port scan, LAN discovery, listening ports.

Tools run inside the host's network namespace (via nsenter) so results match
what the Umbrel itself sees, not the Docker bridge. Hostnames are resolved
here first because DNS doesn't work from inside that namespace.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import shutil
import socket
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import dns.flags
import dns.rdatatype
import dns.resolver
import dns.reversename

from . import config, docker_api
from .network import default_route, interfaces, local_subnet, resolve
from .util import Job, clean_host, host_net, run, stream

COMMON_PORTS = [
    20, 21, 22, 23, 25, 53, 67, 80, 110, 111, 123, 135, 137, 139, 143, 161, 389, 443, 445, 465, 514,
    548, 587, 631, 853, 873, 993, 995, 1194, 1433, 1883, 1900, 2049, 2375, 3000, 3306, 3389, 5000,
    5353, 5432, 5900, 6379, 7000, 8000, 8008, 8080, 8081, 8123, 8443, 8883, 8888, 9000, 9090, 9100,
    10000, 32400, 51820,
]

SERVICE_HINTS = {
    20: "FTP data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 80: "HTTP",
    110: "POP3", 111: "RPC", 123: "NTP", 135: "MS RPC", 137: "NetBIOS", 139: "NetBIOS/SMB", 143: "IMAP",
    161: "SNMP", 389: "LDAP", 443: "HTTPS", 445: "SMB (file sharing)", 465: "SMTPS", 514: "Syslog",
    548: "AFP (Apple file sharing)", 587: "SMTP submission", 631: "IPP (printing)", 853: "DNS over TLS",
    873: "rsync", 993: "IMAPS", 995: "POP3S", 1194: "OpenVPN", 1433: "MS SQL", 1883: "MQTT",
    1900: "UPnP/SSDP", 2049: "NFS", 2375: "Docker API (unencrypted!)", 3000: "Web app", 3306: "MySQL",
    3389: "Remote Desktop", 5000: "Web app / UPnP", 5353: "mDNS (Bonjour)", 5432: "PostgreSQL",
    5900: "VNC", 6379: "Redis", 7000: "AirPlay", 8000: "Web app", 8008: "HTTP alt", 8080: "HTTP alt",
    8081: "HTTP alt", 8123: "Home Assistant", 8443: "HTTPS alt", 8883: "MQTT over TLS",
    8888: "Web app", 9000: "Web app", 9090: "Web app", 9100: "Printer (JetDirect)", 10000: "Webmin",
    32400: "Plex", 51820: "WireGuard",
}

PUBLIC_RESOLVERS = {
    "system": None,
    "cloudflare": "1.1.1.1",
    "google": "8.8.8.8",
    "quad9": "9.9.9.9",
}


def _cmd_text(cmd: List[str]) -> str:
    return " ".join(c for c in cmd if c not in ("nsenter", "-t", "1", "-n", "--"))


# ---------------------------------------------------------------- ping

async def ping(job: Job, host: str, count: int) -> Dict[str, Any]:
    host = clean_host(host)
    ip = await resolve(host)
    count = max(1, min(int(count), 50))
    wait = "2" if config.IS_LINUX else "2000"  # BSD ping takes milliseconds
    cmd = host_net(["ping", "-n", "-c", str(count), "-i", "0.5", "-W", wait, ip])
    job.data["command"] = f"ping -c {count} {host}"
    job.log(f"PING {host} ({ip})")
    times: List[float] = []

    def on_line(line: str) -> None:
        if line.startswith("PING "):
            return
        m = re.search(r"time[=<]([\d.]+)", line)
        if m:
            times.append(float(m.group(1)))
            job.progress = min(0.99, len(times) / count)
            job.data["times"] = times
        job.log(line)

    await stream(cmd, on_line, job, timeout=count * 3 + 10)
    text = "\n".join(job.lines)
    received = len(times)
    m = re.search(r"(\d+) (?:packets )?received", text)
    if m:
        received = int(m.group(1))
    lo = avg = hi = None
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", text)  # "rtt min/avg/max/mdev = a/b/c/d ms"
    if m:
        lo, avg, hi = (float(x) for x in m.groups())
    elif times:
        lo, avg, hi = min(times), round(sum(times) / len(times), 2), max(times)
    return {
        "host": host, "ip": ip, "sent": count, "received": received,
        "loss": round(100 * (count - received) / count, 1),
        "min": lo, "avg": avg, "max": hi, "times": times,
    }


# ---------------------------------------------------------------- traceroute

_HOP_RE = re.compile(r"^\s*(\d+)\s+(.*)$")


async def _rdns(ip: str) -> Optional[str]:
    loop = asyncio.get_running_loop()
    try:
        name = await asyncio.wait_for(loop.run_in_executor(None, socket.gethostbyaddr, ip), 2)
        return name[0]
    except Exception:
        return None


async def traceroute(job: Job, host: str, mode: str) -> Dict[str, Any]:
    host = clean_host(host)
    ip = await resolve(host)
    if shutil.which("traceroute"):
        args = ["traceroute", "-n", "-q", "3", "-w", "2", "-m", "30"]
        if mode == "icmp":
            args.append("-I")
        elif mode == "tcp":
            args += ["-T", "-p", "443"]
        cmd = host_net(args + [ip])
    else:
        cmd = host_net(["tracepath", "-n", ip])
    job.data["command"] = _cmd_text(cmd).replace(ip, host)
    hops: List[Dict[str, Any]] = []
    job.data["hops"] = hops

    def on_line(line: str) -> None:
        job.log(line)
        m = _HOP_RE.match(line)
        if not m or line.startswith("traceroute"):
            return
        rest = m.group(2)
        addrs = re.findall(r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]+:[0-9a-f:]+)", rest)
        rtts = [float(x) for x in re.findall(r"([\d.]+)\s*ms", rest)]
        hops.append({
            "hop": int(m.group(1)),
            "ip": addrs[0] if addrs else None,
            "rtts": rtts,
            "avg": round(sum(rtts) / len(rtts), 2) if rtts else None,
            "timeout": not rtts,
        })
        job.progress = min(0.95, len(hops) / 18)

    await stream(cmd, on_line, job, timeout=150)
    names = await asyncio.gather(*(_rdns(h["ip"]) if h["ip"] else asyncio.sleep(0, None) for h in hops))
    for h, n in zip(hops, names):
        h["name"] = n
        try:
            h["private"] = ipaddress.ip_address(h["ip"]).is_private if h["ip"] else None
        except ValueError:
            h["private"] = None
    return {"host": host, "ip": ip, "hops": hops}


# ---------------------------------------------------------------- DNS

async def dns_lookup(name: str, rtype: str, server: str) -> Dict[str, Any]:
    name = name.strip().rstrip(".")
    if not name or len(name) > 253:
        raise ValueError("Enter a domain name")
    rtype = rtype.upper()
    if rtype not in ("A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "PTR", "CAA", "SRV"):
        raise ValueError("Unsupported record type")
    servers = list(PUBLIC_RESOLVERS.keys()) if server == "compare" else [server]
    if rtype == "PTR":
        try:
            name = dns.reversename.from_address(name).to_text()
        except Exception:
            pass

    async def one(srv: str) -> Dict[str, Any]:
        r = dns.resolver.Resolver(configure=True)
        r.lifetime = 4
        r.timeout = 2
        if srv in PUBLIC_RESOLVERS and PUBLIC_RESOLVERS[srv]:
            r.nameservers = [PUBLIC_RESOLVERS[srv]]
        elif srv not in PUBLIC_RESOLVERS:
            r.nameservers = [clean_host(srv)]
        t0 = time.perf_counter()
        loop = asyncio.get_running_loop()
        try:
            ans = await loop.run_in_executor(None, lambda: r.resolve(name, rtype, raise_on_no_answer=False))
            ms = round((time.perf_counter() - t0) * 1000, 1)
            records = [{"value": rd.to_text(), "ttl": ans.rrset.ttl if ans.rrset else None} for rd in (ans.rrset or [])]
            return {"server": srv, "resolver": r.nameservers[0] if r.nameservers else "", "ms": ms,
                    "records": records, "dnssec": bool(ans.response.flags & dns.flags.AD),
                    "cname": ans.canonical_name.to_text() if ans.canonical_name.to_text().rstrip(".") != name else None}
        except dns.resolver.NXDOMAIN:
            return {"server": srv, "resolver": r.nameservers[0], "error": "Domain doesn't exist (NXDOMAIN)",
                    "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as e:
            return {"server": srv, "resolver": r.nameservers[0] if r.nameservers else "",
                    "error": str(e) or e.__class__.__name__}

    results = await asyncio.gather(*(one(s) for s in servers))
    return {"name": name, "type": rtype, "results": results}


# ---------------------------------------------------------------- port scan

def parse_ports(spec: str) -> List[int]:
    spec = (spec or "").strip().lower()
    if spec in ("", "common"):
        return COMMON_PORTS
    if spec == "top1000":
        return list(range(1, 1025))
    ports: List[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                lo, hi = hi, lo
            ports.extend(range(max(1, lo), min(65535, hi) + 1))
        else:
            ports.append(int(part))
    ports = sorted({p for p in ports if 0 < p < 65536})
    if len(ports) > 4096:
        raise ValueError("Scan at most 4096 ports at a time")
    return ports


async def port_scan(job: Job, host: str, spec: str) -> Dict[str, Any]:
    host = clean_host(host)
    ip = await resolve(host)
    ports = parse_ports(spec)
    job.data["command"] = f"nmap -sT -Pn -p {spec or 'common'} {host}"
    open_ports: List[Dict[str, Any]] = []
    job.data["open"] = open_ports

    if shutil.which("nmap"):
        cmd = host_net(["nmap", "-sT", "-Pn", "-n", "-T4", "--max-retries", "1", "-oX", "-",
                        "-p", ",".join(map(str, ports)), ip])
        job.log(f"Scanning {len(ports)} TCP ports on {host} ({ip}) with nmap…")
        rc, out, err = await run(cmd, timeout=300)
        try:
            root = ET.fromstring(out)
            for p in root.iter("port"):
                st = p.find("state")
                if st is not None and st.get("state") == "open":
                    num = int(p.get("portid", "0"))
                    svc = p.find("service")
                    name = svc.get("name") if svc is not None else ""
                    open_ports.append({"port": num, "service": SERVICE_HINTS.get(num) or name or ""})
                    job.log(f"  {num}/tcp open  {SERVICE_HINTS.get(num) or name}")
        except ET.ParseError:
            raise RuntimeError((err or out or "nmap failed").strip()[:300])
    else:
        job.log(f"Scanning {len(ports)} TCP ports on {host} ({ip})…")
        sem = asyncio.Semaphore(200)
        done = 0

        async def probe(port: int) -> None:
            nonlocal done
            async with sem:
                if job.cancelled:
                    return
                try:
                    _, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 1.0)
                    w.close()
                    open_ports.append({"port": port, "service": SERVICE_HINTS.get(port, "")})
                    job.log(f"  {port}/tcp open  {SERVICE_HINTS.get(port, '')}")
                except Exception:
                    pass
                done += 1
                job.progress = done / len(ports)

        await asyncio.gather(*(probe(p) for p in ports))
    open_ports.sort(key=lambda p: p["port"])
    job.log(f"Done: {len(open_ports)} open of {len(ports)} scanned.")
    return {"host": host, "ip": ip, "scanned": len(ports), "open": open_ports}


# ---------------------------------------------------------------- LAN discovery

async def lan_scan(job: Job, subnet: Optional[str]) -> Dict[str, Any]:
    net = subnet or local_subnet()
    if not net:
        raise RuntimeError("Couldn't work out your local network range")
    n = ipaddress.ip_network(net, strict=False)
    if n.version != 4 or n.num_addresses > 1024:
        raise ValueError("Pick an IPv4 range of /22 or smaller")
    job.data["command"] = f"nmap -sn {n}"
    job.data["subnet"] = str(n)
    devices: Dict[str, Dict[str, Any]] = {}
    if shutil.which("nmap"):
        job.phase = "Discovering devices"
        rc, out, err = await run(host_net(["nmap", "-sn", "-n", "-oX", "-", str(n)]), timeout=180)
        try:
            root = ET.fromstring(out)
        except ET.ParseError:
            raise RuntimeError((err or "nmap failed").strip()[:300])
        for h in root.iter("host"):
            st = h.find("status")
            if st is None or st.get("state") != "up":
                continue
            d: Dict[str, Any] = {"ip": None, "mac": None, "vendor": None, "latency": None}
            for a in h.findall("address"):
                if a.get("addrtype") == "ipv4":
                    d["ip"] = a.get("addr")
                elif a.get("addrtype") == "mac":
                    d["mac"] = a.get("addr")
                    d["vendor"] = a.get("vendor")
            times = h.find("times")
            if times is not None and times.get("srtt"):
                d["latency"] = round(int(times.get("srtt", "0")) / 1000, 2)
            if d["ip"]:
                devices[d["ip"]] = d
    # merge in the kernel ARP table (catches devices that ignore pings)
    from .util import read_text
    arp = read_text(config.host_proc_net("arp")) or ""
    for line in arp.splitlines()[1:]:
        f = line.split()
        if len(f) >= 4 and f[3] != "00:00:00:00:00:00":
            try:
                if ipaddress.ip_address(f[0]) in n and f[0] not in devices:
                    devices[f[0]] = {"ip": f[0], "mac": f[3].upper(), "vendor": None, "latency": None}
            except ValueError:
                pass
    job.phase = "Looking up names"
    job.progress = 0.8
    ips = list(devices.keys())
    names = await asyncio.gather(*(_rdns(ip) for ip in ips))
    _, gw = default_route()
    self_ips = set()
    try:
        for i in await interfaces():
            for a in i.get("ipv4", []):
                self_ips.add(a.split("/")[0])
    except Exception:
        pass
    for ip, name in zip(ips, names):
        d = devices[ip]
        d["name"] = name
        d["router"] = ip == gw
        d["self"] = ip in self_ips
        if d["self"] and not d.get("vendor"):
            d["vendor"] = "This Umbrel"
    out = sorted(devices.values(), key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
    return {"subnet": str(n), "devices": out, "at": time.time()}


# ---------------------------------------------------------------- listening ports

_SS_PROC = re.compile(r'\("([^"]+)",pid=(\d+)')


async def listening() -> List[Dict[str, Any]]:
    rc, out, err = await run(host_net(["ss", "-H", "-tulnp"]), timeout=10)
    if rc != 0:
        raise RuntimeError(err.strip() or "ss failed")
    conts = await docker_api.containers()
    published = docker_api.manifest_ports()
    for c in conts.values():
        for p in c.get("ports", []):
            published[p] = c["appName"]
    rows: Dict[Any, Dict[str, Any]] = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        proto, local = f[0], f[4]
        addr, _, port = local.rpartition(":")
        addr = addr.strip("[]").split("%")[0]
        try:
            portn = int(port)
        except ValueError:
            continue
        procs = _SS_PROC.findall(line)
        pname = procs[0][0] if procs else ""
        pid = int(procs[0][1]) if procs else None
        app = None
        if pid:
            cid = _container_of(pid)
            if cid and cid in conts:
                app = conts[cid]["appName"]
        if not app and portn in published:
            app = published[portn]
        key = (proto, portn, pname)
        scope = "local only" if addr in ("127.0.0.1", "::1") or addr.startswith("127.") else "network"
        if key in rows:
            if scope == "network":
                rows[key]["scope"] = "network"
            rows[key]["addresses"].append(addr)
            continue
        rows[key] = {
            "proto": proto, "port": portn, "addresses": [addr], "process": pname, "pid": pid,
            "app": app, "scope": scope, "service": SERVICE_HINTS.get(portn, ""),
        }
    return sorted(rows.values(), key=lambda r: (r["scope"] != "network", r["port"]))


def _container_of(pid: int) -> Optional[str]:
    from .util import read_text
    raw = read_text(f"{config.PROC}/{pid}/cgroup") or ""
    m = re.search(r"([0-9a-f]{64})", raw)
    return m.group(1) if m else None
