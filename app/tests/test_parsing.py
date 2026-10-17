"""Parsers for kernel files, smartctl JSON, audio headers and user input."""
import os
import struct
import unittest
import wave

from tests import DATA, HOST, PROC, SYS, UMBREL, host, write

from vitals import config, network, smart, storage, studio, util
from vitals.cleaner import _is_appledouble, _norm_ref
from vitals.devices import magic_packet, norm_mac
from vitals.netlab import parse_ports
from vitals.network import bufferbloat_grade, upload_mbps

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  123456     100    0    0    0     0          0         0   123456     100    0    0    0     0       0          0
enp1s0: 9876543210 7654321    0    0    0     0          0      1234 1234567890 2345678    0    0    0     0       0          0
docker0:  5000      50    0    0    0     0          0         0     6000      60    0    0    0     0       0          0
"""

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
enp1s0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
enp1s0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\t0\t0\t0
"""

MOUNTINFO = """22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
30 22 8:17 / /home/umbrel/umbrel/external/My\\040Samples rw,relatime shared:5 - exfat /dev/sdb1 rw
31 22 8:1 / /boot/efi rw,relatime shared:6 - vfat /dev/sda1 rw
40 22 0:5 / /proc rw - proc proc rw
"""


def nvme_json(**over):
    log = {"critical_warning": 0, "temperature": 41, "available_spare": 100, "available_spare_threshold": 10,
           "percentage_used": 6, "data_units_written": 1000, "data_units_read": 2000, "media_errors": 0,
           "unsafe_shutdowns": 3}
    log.update(over)
    return {"smartctl": {"exit_status": 0}, "model_name": "Samsung SSD 970 EVO Plus 1TB", "serial_number": "S1",
            "smart_status": {"passed": True}, "temperature": {"current": 41}, "power_on_time": {"hours": 1000},
            "power_cycle_count": 50, "device": {"protocol": "NVMe"}, "nvme_smart_health_information_log": log}


def ata_json(realloc=0, pending=0, passed=True, failing=False):
    def attr(i, name, raw, value=100, thresh=10, when=""):
        return {"id": i, "name": name, "value": value, "worst": value, "thresh": thresh,
                "raw": {"value": raw, "string": str(raw)}, "when_failed": when}
    return {"smartctl": {"exit_status": 0}, "model_name": "WDC WD40EFZX", "serial_number": "W1",
            "smart_status": {"passed": passed}, "temperature": {"current": 38}, "power_on_time": {"hours": 23000},
            "device": {"protocol": "ATA"}, "rotation_rate": 5400,
            "ata_smart_attributes": {"table": [
                attr(5, "Reallocated_Sector_Ct", realloc, when="now" if failing else ""),
                attr(197, "Current_Pending_Sector", pending), attr(199, "UDMA_CRC_Error_Count", 2)]}}


class SmartParsing(unittest.TestCase):
    def test_healthy_nvme(self):
        r = smart.parse(nvme_json())
        self.assertTrue(r["available"])
        self.assertEqual(r["health"], "good")
        self.assertEqual(r["lifeLeft"], 94)
        self.assertEqual(r["written"], 1000 * 512000)

    def test_nvme_critical_warning_and_worn_out(self):
        self.assertEqual(smart.parse(nvme_json(critical_warning=4))["health"], "critical")
        r = smart.parse(nvme_json(percentage_used=95))
        self.assertEqual(r["health"], "critical")
        self.assertIn("5%", r["issues"][0])

    def test_spare_threshold_zero_is_not_failing(self):
        # virtual NVMe drives (cloud VMs, some USB bridges) report spare 0 / threshold 0
        self.assertEqual(smart.parse(nvme_json(available_spare=0, available_spare_threshold=0))["health"], "good")
        self.assertEqual(smart.parse(nvme_json(available_spare=5, available_spare_threshold=10))["health"], "critical")

    def test_ata_reallocated_is_warning_and_crc_is_only_a_note(self):
        r = smart.parse(ata_json(realloc=8))
        self.assertEqual(r["health"], "warning")
        self.assertTrue(any("reallocated" in i for i in r["issues"]))
        self.assertTrue(any("cable" in n for n in r["notes"]))

    def test_ata_failed_self_assessment_is_critical(self):
        r = smart.parse(ata_json(passed=False))
        self.assertEqual(r["health"], "critical")
        self.assertIn("FAILED", r["issues"][0])
        self.assertEqual(smart.parse(ata_json(failing=True))["health"], "critical")

    def test_standby_and_errors(self):
        r = smart.parse({"smartctl": {"exit_status": 2, "messages": [{"string": "Device is in STANDBY mode, exit(2)"}]}})
        self.assertTrue(r["standby"])
        self.assertFalse(r["available"])
        r = smart.parse({"smartctl": {"exit_status": 2, "messages": [{"string": "Unknown USB bridge"}]}})
        self.assertFalse(r["available"])
        self.assertIn("USB", r["error"])


class KernelFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        write(os.path.join(PROC, "1", "net", "dev"), NET_DEV)
        write(os.path.join(PROC, "1", "net", "route"), ROUTE)
        write(os.path.join(PROC, "1", "mountinfo"), MOUNTINFO)
        cls._linux = config.IS_LINUX
        config.IS_LINUX = True

    @classmethod
    def tearDownClass(cls):
        config.IS_LINUX = cls._linux

    def test_net_counters(self):
        c = network.read_counters()
        self.assertEqual(c["enp1s0"][:2], (9876543210, 1234567890))
        self.assertIn("docker0", c)

    def test_interface_kinds(self):
        self.assertEqual(network.iface_kind("enp1s0"), "ethernet")
        self.assertEqual(network.iface_kind("docker0"), "virtual")
        self.assertEqual(network.iface_kind("veth12ab"), "virtual")
        self.assertEqual(network.iface_kind("tailscale0"), "vpn")
        self.assertEqual(network.iface_kind("wlp2s0"), "wifi")

    def test_default_route_and_subnet(self):
        self.assertEqual(network.default_route(), ("enp1s0", "192.168.1.1"))
        self.assertEqual(network.local_subnet(), "192.168.1.0/24")

    def test_mountinfo_unescapes_spaces(self):
        m = {x["mountpoint"]: x for x in storage.read_mounts()}
        self.assertIn("/home/umbrel/umbrel/external/My Samples", m)
        self.assertEqual(m["/"]["majmin"], "8:2")

    def test_absolute_symlink_resolves_inside_host(self):
        write(host("/run/systemd/resolve/stub-resolv.conf"), "nameserver 127.0.0.53\n")
        write(host("/run/systemd/resolve/resolv.conf"), "nameserver 192.168.1.1\nnameserver 1.1.1.1\n")
        os.makedirs(host("/etc"), exist_ok=True)
        link = host("/etc/resolv.conf")
        if os.path.lexists(link):
            os.remove(link)
        os.symlink("/run/systemd/resolve/stub-resolv.conf", link)  # absolute, like on Debian
        self.assertEqual(network.dns_servers(), ["192.168.1.1", "1.1.1.1"])


class DriveDiscovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        write(os.path.join(PROC, "1", "mountinfo"), MOUNTINFO)
        ROOT_SYS = SYS
        devroot = os.path.join(ROOT_SYS, "devices", "pci0000:00")
        # internal SATA disk with two partitions
        sda = os.path.join(devroot, "ata1", "host0", "block", "sda")
        write(os.path.join(sda, "size"), str(500107862016 // 512))
        write(os.path.join(sda, "removable"), "0")
        write(os.path.join(sda, "queue", "rotational"), "0")
        write(os.path.join(sda, "device", "model"), "CT500MX500SSD1")
        write(os.path.join(sda, "device", "vendor"), "ATA")
        for p, majmin, size in (("sda1", "8:1", 536870912), ("sda2", "8:2", 499570991104)):
            write(os.path.join(sda, p, "partition"), "1")
            write(os.path.join(sda, p, "dev"), majmin)
            write(os.path.join(sda, p, "size"), str(size // 512))
        # USB disk
        sdb = os.path.join(devroot, "usb2", "2-1", "host1", "block", "sdb")
        write(os.path.join(sdb, "size"), str(4000787030016 // 512))
        write(os.path.join(sdb, "removable"), "0")
        write(os.path.join(sdb, "queue", "rotational"), "1")
        write(os.path.join(sdb, "device", "model"), "Elements 25A3")
        write(os.path.join(sdb, "device", "vendor"), "WD")
        write(os.path.join(sdb, "sdb1", "partition"), "1")
        write(os.path.join(sdb, "sdb1", "dev"), "8:17")
        write(os.path.join(sdb, "sdb1", "size"), str(4000785104896 // 512))
        block = os.path.join(ROOT_SYS, "block")
        os.makedirs(block, exist_ok=True)
        for name, target in (("sda", sda), ("sdb", sdb)):
            link = os.path.join(block, name)
            if not os.path.lexists(link):
                os.symlink(target, link)
        write(os.path.join(block, "loop0", "size"), "100")  # must be skipped
        os.makedirs(host(UMBREL), exist_ok=True)
        os.makedirs(host("/home/umbrel/umbrel/external/My Samples"), exist_ok=True)
        os.makedirs(host("/boot/efi"), exist_ok=True)

    def test_lists_internal_and_usb_drives(self):
        drives = {d["name"]: d for d in storage.list_drives()}
        self.assertEqual(set(drives), {"sda", "sdb"})
        self.assertEqual(drives["sda"]["kind"], "SSD")
        self.assertEqual(drives["sdb"]["transport"], "usb")
        self.assertTrue(drives["sdb"]["external"])
        self.assertEqual(drives["sdb"]["model"], "WD Elements 25A3")
        root = next(p for p in drives["sda"]["partitions"] if p["name"] == "sda2")
        self.assertEqual(root["mountpoint"], "/")
        self.assertIsNotNone(root["usage"])
        self.assertTrue(drives["sda"]["system"])  # holds the Umbrel data folder
        usb = drives["sdb"]["partitions"][0]
        self.assertEqual(usb["mountpoint"], "/home/umbrel/umbrel/external/My Samples")


class AudioHeaders(unittest.TestCase):
    def test_wav(self):
        p = os.path.join(DATA, "t.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(3)
            w.setframerate(48000)
            w.writeframes(b"\0" * 48000 * 6)  # 1 second of 24-bit stereo
        sr, bits, ch, dur = studio.audio_info(p, ".wav")
        self.assertEqual((sr, bits, ch), (48000, 24, 2))
        self.assertAlmostEqual(dur, 1.0, places=2)

    def test_aiff(self):
        p = os.path.join(DATA, "t.aif")
        # 80-bit extended float for 44100 Hz
        rate = bytes.fromhex("400EAC44000000000000")
        comm = struct.pack(">hIh", 1, 44100 * 2, 16) + rate
        body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
        write(p, "", "w")
        with open(p, "wb") as f:
            f.write(b"FORM" + struct.pack(">I", len(body)) + body)
        sr, bits, ch, dur = studio.audio_info(p, ".aif")
        self.assertEqual((sr, bits, ch), (44100, 16, 1))
        self.assertAlmostEqual(dur, 2.0, places=2)

    def test_not_audio(self):
        p = write(os.path.join(DATA, "fake.wav"), "hello")
        self.assertIsNone(studio.audio_info(p, ".wav"))


class InputValidation(unittest.TestCase):
    def test_clean_host_rejects_injection(self):
        for bad in ("1.1.1.1; rm -rf /", "-oProxyCommand=x", "$(id)", "a b", "", "x" * 300, "`id`"):
            with self.assertRaises(ValueError, msg=bad):
                util.clean_host(bad)
        for good in ("1.1.1.1", "google.com", "fe80::1", "[2001:db8::1]", "my-nas.local"):
            util.clean_host(good)

    def test_parse_ports(self):
        self.assertEqual(parse_ports("22, 80,8000-8002"), [22, 80, 8000, 8001, 8002])
        self.assertEqual(parse_ports("100-98"), [98, 99, 100])
        self.assertEqual(len(parse_ports("top1000")), 1024)
        with self.assertRaises(ValueError):
            parse_ports("1-65535")

    def test_mac_and_magic_packet(self):
        self.assertEqual(norm_mac("aa-bb-cc-dd-ee-ff"), "AA:BB:CC:DD:EE:FF")
        with self.assertRaises(ValueError):
            norm_mac("not-a-mac")
        pkt = magic_packet("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        self.assertEqual(pkt[6:12], bytes.fromhex("AABBCCDDEEFF"))

    def test_image_refs(self):
        self.assertEqual(_norm_ref("docker.io/library/nginx:1.2@sha256:abc"), ("nginx:1.2", "nginx@sha256:abc"))
        self.assertEqual(_norm_ref("ghcr.io/a/b"), ("ghcr.io/a/b:latest", None))
        self.assertEqual(_norm_ref("postgres:15@sha256:x")[1], "postgres@sha256:x")

    def test_appledouble_magic(self):
        real = os.path.join(DATA, "._real")
        with open(real, "wb") as f:
            f.write(b"\x00\x05\x16\x07rest")
        fake = write(os.path.join(DATA, "._fake"), "my own file")
        self.assertTrue(_is_appledouble(real))
        self.assertFalse(_is_appledouble(fake))


class SpeedMaths(unittest.TestCase):
    def test_bufferbloat_grades(self):
        self.assertEqual(bufferbloat_grade(10, 12), "A+")
        self.assertEqual(bufferbloat_grade(10, 35), "A")
        self.assertEqual(bufferbloat_grade(10, 300), "D")
        self.assertEqual(bufferbloat_grade(10, 900), "F")
        self.assertIsNone(bufferbloat_grade(None, 10))

    def test_upload_counts_the_span_the_requests_covered(self):
        # two parallel 1 MB requests over exactly 1 second = 16 Mb/s
        self.assertEqual(upload_mbps([(1.0, 2.0, 1_000_000), (1.0, 2.0, 1_000_000)]), 16.0)
        self.assertEqual(upload_mbps([]), 0.0)


if __name__ == "__main__":
    unittest.main()


class FoundOnRealLinux(unittest.TestCase):
    """Regressions caught by the container smoke test on a real Linux host."""

    def test_big_networks_scan_our_own_slice(self):
        from vitals.netlab import scan_range
        n, note = scan_range("10.1.0.0/20", ["10.1.0.154"])
        self.assertEqual(str(n), "10.1.0.0/24")
        self.assertIn("10.1.0.0/20", note)
        n, note = scan_range("192.168.1.0/24", [])
        self.assertEqual((str(n), note), ("192.168.1.0/24", None))
        with self.assertRaises(ValueError):
            scan_range("10.0.0.0/8", ["192.168.1.5"])

    def test_cloudflare_server_timing(self):
        self.assertEqual(network.server_ms("cfSpeedEdge;dur=3, cfSpeedWorker;dur=37"), 37.0)
        self.assertEqual(network.server_ms(""), 0.0)

    def test_own_container_processes(self):
        from vitals.processes import ProcessMonitor
        pm = ProcessMonitor.__new__(ProcessMonitor)
        pm.cgroup = {}
        pm.self_id = "a" * 64
        other = "b" * 64
        write(os.path.join(PROC, "501", "cgroup"), "0::/\n")                                  # us
        write(os.path.join(PROC, "502", "cgroup"), f"0::/../docker-{other}.scope\n")         # another app
        write(os.path.join(PROC, "503", "cgroup"), "0::/../../init.scope\n")                 # the host
        self.assertEqual(pm._container(501), "a" * 64)
        self.assertEqual(pm._container(502), other)
        self.assertIsNone(pm._container(503))
