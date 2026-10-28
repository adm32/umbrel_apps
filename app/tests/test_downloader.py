"""Downloader: safe links, the right yt-dlp options, progress, and only touching its own files."""
import asyncio
import os
import sys
import unittest

from tests import DATA, write

from vitals import downloader as D
from vitals.util import Job

FAKE = os.path.join(DATA, "fake_ytdlp.py")


class Links(unittest.TestCase):
    def test_accepts_web_links(self):
        for u in ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "https://youtu.be/jNQXAC9IVRw",
                  "https://music.youtube.com/watch?v=abc", "https://soundcloud.com/artist/track", "http://vimeo.com/1"):
            self.assertEqual(D.check_url(u), u)

    def test_rejects_everything_else(self):
        for u in ("", "youtube.com/watch?v=x", "ftp://a.com/x", "file:///etc/passwd", "--exec=rm -rf /",
                  "javascript:alert(1)", "http://localhost:3000/api/live", "http://192.168.1.1/admin",
                  "http://10.21.0.1:3000/", "http://[::1]/", "http://umbrel.local:7468/", "https://x.com/" + "a" * 2100):
            with self.assertRaises(ValueError, msg=u):
                D.check_url(u)


class Options(unittest.TestCase):
    def test_mp3_is_320k_with_cover(self):
        a = D.build_args("mp3", "", "/dl")
        self.assertIn("--audio-quality", a)
        self.assertEqual(a[a.index("--audio-quality") + 1], "320K")
        self.assertEqual(a[a.index("--audio-format") + 1], "mp3")
        self.assertIn("--embed-thumbnail", a)
        self.assertIn("--no-playlist", a)

    def test_wav_has_no_cover(self):
        a = D.build_args("wav", "", "/dl")
        self.assertEqual(a[a.index("--audio-format") + 1], "wav")
        self.assertNotIn("--embed-thumbnail", a)

    def test_mp4_prefers_h264_at_the_chosen_height(self):
        a = D.build_args("mp4", "720", "/dl")
        self.assertEqual(a[a.index("-S") + 1], "res:720,vcodec:h264,acodec:aac")
        self.assertEqual(a[a.index("--merge-output-format") + 1], "mp4")
        self.assertTrue(D.build_args("mp4", "best", "/dl")[D.build_args("mp4", "best", "/dl").index("-S") + 1].startswith("res,"))
        with self.assertRaises(ValueError):
            D.build_args("exe", "", "/dl")

    def test_progress_and_errors(self):
        p = D.parse_progress("VPROG 500 1000 NA 250000.5 2")
        self.assertEqual((p["fraction"], p["speed"], p["eta"]), (0.5, 250000.5, 2))
        p = D.parse_progress("VPROG 300 NA 1200 NA NA")  # only an estimate of the total
        self.assertEqual(p["fraction"], 0.25)
        self.assertIsNone(D.parse_progress("[download] 50%"))
        self.assertIn("Update downloader", D.friendly_error("ERROR: nsig extraction failed"))
        self.assertIn("isn't available", D.friendly_error("ERROR: [youtube] x: This video is unavailable"))
        self.assertIn("sign-in check", D.friendly_error("Sign in to confirm you're not a bot"))


class Pipeline(unittest.TestCase):
    """The whole download path, with a stand-in for yt-dlp so it runs without internet."""

    @classmethod
    def setUpClass(cls):
        write(FAKE, '''
import os, sys, time
args = sys.argv[1:]
url = args[args.index("--") + 1]
if "fail" in url:
    print("ERROR: [youtube] abc: Private video. Sign in if you've been granted access"); sys.exit(1)
dest = args[args.index("-P") + 1]
ext = args[args.index("--audio-format") + 1] if "--audio-format" in args else "mp4"
print("[youtube] Extracting URL: " + url, flush=True)
for done in (0, 500, 1000):
    print(f"VPROG {done} 1000 NA 1000 1", flush=True)
print("[ExtractAudio] Destination: x", flush=True)
path = os.path.join(dest, f"Test Song [abc123defgh].{ext}")
open(path, "wb").write(b"x" * 1234)
print("VFILE " + path, flush=True)
''')
        cls._cmd = D.ytdlp_cmd
        D.ytdlp_cmd = lambda *a: [sys.executable, FAKE, *a]

    @classmethod
    def tearDownClass(cls):
        D.ytdlp_cmd = cls._cmd

    def test_download_saves_tracks_and_deletes_safely(self):
        d = D.Downloader()
        job = Job("ytdl")
        r = asyncio.run(d.download(job, "https://youtu.be/abc", "mp3", ""))
        self.assertEqual((r["title"], r["format"], r["size"]), ("Test Song", "mp3", 1234))
        self.assertNotIn("path", r, "the server path is never sent to the browser")
        self.assertEqual(job.phase, "Converting to MP3")
        p = d.safe_path(r["id"])
        self.assertTrue(p and p.startswith(D.dest_dir()))
        self.assertEqual(d.items()[0]["exists"], True)

        # entries pointing outside the downloads folder are never served or deleted
        victim = write(os.path.join(DATA, "victim.mp3"), "keep")
        d.history.append({"id": "evil", "path": victim, "title": "x", "format": "mp3", "size": 1, "at": 0, "name": "x"})
        self.assertIsNone(d.safe_path("evil"))
        d.delete("evil")
        self.assertTrue(os.path.exists(victim))

        self.assertTrue(d.delete(r["id"]))
        self.assertFalse(os.path.exists(p))

    def test_failures_become_plain_english(self):
        d = D.Downloader()
        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(d.download(Job("ytdl"), "https://youtu.be/fail", "mp4", "720"))
        self.assertEqual(str(cm.exception), "That video is private")


if __name__ == "__main__":
    unittest.main()
