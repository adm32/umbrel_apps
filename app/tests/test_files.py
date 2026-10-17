"""The parts that delete things: they must only ever remove what they found."""
import asyncio
import os
import shutil
import time
import unittest

from tests import DATA, UMBREL, host, write

from vitals import config
from vitals.cleaner import Cleaner
from vitals.studio import Studio
from vitals.util import Job

HOME = host(UMBREL, "home")
APPLE_MAGIC = b"\x00\x05\x16\x07" + b"\0" * 60


def old(path, days=30):
    t = time.time() - days * 86400
    os.utime(path, (t, t))
    return path


class CleanerSafety(unittest.TestCase):
    def setUp(self):
        for d in (HOME, host(UMBREL, "app-data"), host("/var/log"), host("/tmp")):
            shutil.rmtree(d, ignore_errors=True)
        # junk the scan should find
        write(os.path.join(HOME, "Music", ".DS_Store"), "x")
        with open(os.path.join(HOME, "Music", "._Kick.wav"), "wb") as f:
            f.write(APPLE_MAGIC)
        write(os.path.join(HOME, "Docs", "Thumbs.db"), "x")
        os.makedirs(os.path.join(HOME, ".Spotlight-V100", "Store"), exist_ok=True)
        write(os.path.join(HOME, ".Spotlight-V100", "Store", "index"), "x" * 5000)
        # things it must leave alone
        self.keep = [
            write(os.path.join(HOME, "Music", "Kick.wav"), "audio"),
            write(os.path.join(HOME, "Music", "._my-notes.txt"), "not AppleDouble, a real file"),
        ]
        outside = write(os.path.join(DATA, "outside", ".DS_Store"), "precious")
        self.keep.append(outside)
        link = os.path.join(HOME, "Music", "link", ".DS_Store")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(outside, link)  # a symlink named like junk, pointing outside
        # logs and temp files
        self.rotated = write(host("/var/log/syslog.2.gz"), "z")
        self.keep.append(write(host("/var/log/syslog"), "live log"))
        self.old_tmp = old(write(host("/tmp/update-cache.tar"), "t" * 10000))
        self.keep.append(old(write(host("/tmp/service.pid"), "123")))         # lock-like name
        self.keep.append(old(write(host("/tmp/tiny"), "x")))                  # under 4 KB
        self.keep.append(write(host("/tmp/fresh.bin"), "f" * 10000))          # recent
        # leftover app data
        write(host(UMBREL, "umbrel.yaml"), "apps:\n  - bitcoin\n")
        self.keep.append(write(host(UMBREL, "app-data", "bitcoin", "data", "blocks"), "b"))
        write(host(UMBREL, "app-data", "old-app", "data", "db"), "o" * 3000)

    def scan(self, c):
        return asyncio.run(c.scan(Job("cleanscan")))

    def test_scan_finds_only_junk(self):
        res = self.scan(Cleaner())
        cats = {c["id"]: c for c in res["categories"]}
        junk = " ".join(i["label"] for i in cats["junk"]["items"])
        self.assertIn(".DS_Store files (1)", junk)
        self.assertIn("AppleDouble ._ files (1)", junk)
        self.assertIn("Thumbs.db & desktop.ini (1)", junk)
        self.assertIn("Spotlight & fsevents folders (1)", junk)
        self.assertEqual(cats["rotated"]["count"], 1)
        self.assertEqual(cats["tmp"]["items"][0]["label"], "1 temporary files")
        self.assertEqual([i["label"] for i in cats["appdata"]["items"]], ["old-app"])
        self.assertFalse(cats["appdata"]["default"], "permanent deletions are never pre-selected")

    def test_clean_removes_selected_and_nothing_else(self):
        c = Cleaner()
        res = self.scan(c)
        selection = {cat["id"]: [i["id"] for i in cat["items"]] for cat in res["categories"] if not cat["infoOnly"]}
        out = asyncio.run(c.clean(Job("clean"), res["id"], selection))
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["freed"], 0)
        for gone in (os.path.join(HOME, "Music", ".DS_Store"), os.path.join(HOME, "Music", "._Kick.wav"),
                     os.path.join(HOME, ".Spotlight-V100"), self.rotated, self.old_tmp,
                     host(UMBREL, "app-data", "old-app")):
            self.assertFalse(os.path.exists(gone), gone)
        for kept in self.keep:
            self.assertTrue(os.path.exists(kept), kept)
        with open(os.path.join(DATA, "outside", ".DS_Store")) as f:
            self.assertEqual(f.read(), "precious")

    def test_stale_scan_ids_are_refused(self):
        c = Cleaner()
        self.scan(c)
        with self.assertRaises(RuntimeError):
            asyncio.run(c.clean(Job("clean"), "not-the-scan", {"junk": ["x"]}))

    def test_safe_remove_refuses_paths_outside_its_roots(self):
        c = Cleaner()
        victim = write(os.path.join(DATA, "victim.txt"), "keep me")
        with self.assertRaises(PermissionError):
            c._safe_remove(victim, [HOME])
        with self.assertRaises(PermissionError):
            c._safe_remove(os.path.join(HOME, "..", "..", "..", "..", "..", "victim.txt"), [HOME])
        self.assertTrue(os.path.exists(victim))

    def test_safe_remove_refuses_a_symlinked_parent(self):
        c = Cleaner()
        target_dir = os.path.join(DATA, "elsewhere")
        write(os.path.join(target_dir, "f.txt"), "keep")
        link = os.path.join(HOME, "sneaky")
        os.symlink(target_dir, link)
        with self.assertRaises(PermissionError):
            c._safe_remove(os.path.join(link, "f.txt"), [HOME])
        self.assertTrue(os.path.exists(os.path.join(target_dir, "f.txt")))


class StudioSafety(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(HOME, ignore_errors=True)
        sample = b"RIFF" + os.urandom(20000)
        for p in ("Samples/Kick 01.wav", "Downloads/kit/Kick 01.wav"):
            write(os.path.join(HOME, p), "")
            with open(os.path.join(HOME, p), "wb") as f:
                f.write(sample)
        write(os.path.join(HOME, "Samples", "Snare.wav"), "")
        with open(os.path.join(HOME, "Samples", "Snare.wav"), "wb") as f:
            f.write(b"RIFF" + os.urandom(20000))
        # same size, different content: must not count as a duplicate
        with open(os.path.join(HOME, "Samples", "Other.wav"), "wb") as f:
            f.write(b"RIFF" + os.urandom(20000))
        write(os.path.join(HOME, "Projects", "Night Drive.als"), "project")
        write(os.path.join(HOME, "Projects", "Backup", "Night Drive [2026-01-01].als"), "old")

    def test_duplicates_backups_and_projects(self):
        s = Studio()
        res = asyncio.run(s.scan(Job("studioscan"), HOME))
        self.assertEqual(len(res["duplicates"]), 1)
        self.assertEqual(len(res["duplicates"][0]["copies"]), 2)
        self.assertEqual(res["projectCount"], 1)
        self.assertEqual(len(res["backups"]), 1)
        self.assertEqual(res["audioFiles"], 4)

    def test_never_deletes_every_copy(self):
        s = Studio()
        res = asyncio.run(s.scan(Job("studioscan"), HOME))
        ids = [c["id"] for c in res["duplicates"][0]["copies"]]
        with self.assertRaises(ValueError):
            s.delete(ids)
        self.assertEqual(sum(os.path.exists(os.path.join(HOME, p)) for p in ("Samples/Kick 01.wav", "Downloads/kit/Kick 01.wav")), 2)
        out = s.delete(ids[1:] + [res["backups"][0]["id"]])
        self.assertEqual(out["removed"], 2)
        self.assertTrue(os.path.exists(os.path.join(HOME, "Samples", "Kick 01.wav")), "shortest path kept")
        self.assertTrue(os.path.exists(os.path.join(HOME, "Projects", "Night Drive.als")))

    def test_unknown_ids_and_outside_folders(self):
        s = Studio()
        self.assertEqual(s.delete(["made-up-id"])["removed"], 0)
        with self.assertRaises(ValueError):
            asyncio.run(s.scan(Job("studioscan"), "/etc"))
        with self.assertRaises(ValueError):
            asyncio.run(s.scan(Job("studioscan"), os.path.join(HOME, "..", "..")))


class Benchmark(unittest.TestCase):
    def test_benchmark_cleans_up_its_test_file(self):
        from vitals import storage
        target = host("/mnt/bench")
        os.makedirs(target, exist_ok=True)
        r = storage._benchmark_sync(Job("benchmark"), "/mnt/bench", 8)
        self.assertGreater(r["writeMBps"], 0)
        self.assertGreater(r["readMBps"], 0)
        self.assertEqual([f for f in os.listdir(target) if f.startswith(".vitals")], [])
        self.assertEqual([f for f in os.listdir(os.path.join(str(config.DATA)))
                          if f.startswith(".vitals-benchmark")], [])


if __name__ == "__main__":
    unittest.main()
