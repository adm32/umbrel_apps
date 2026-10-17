"""Test suite. Run from the app/ folder:  python -m unittest discover -s tests -t . -v

A fake host filesystem (/proc, /sys, umbrel data) is built in a temp folder before
vitals is imported, so every test runs on any OS without touching the real machine.
"""
import os
import tempfile

ROOT = tempfile.mkdtemp(prefix="vitals-test-")
HOST = os.path.join(ROOT, "host")
PROC = os.path.join(ROOT, "proc")
SYS = os.path.join(ROOT, "sys")
DATA = os.path.join(ROOT, "data")
UMBREL = "/home/umbrel/umbrel"

for d in (HOST, PROC, SYS, DATA):
    os.makedirs(d, exist_ok=True)

os.environ.update({
    "VITALS_HOST_ROOT": HOST,
    "VITALS_PROC": PROC,
    "VITALS_SYS": SYS,
    "VITALS_DATA": DATA,
    "UMBREL_ROOT": UMBREL,
    "VITALS_DOCKER_SOCK": os.path.join(ROOT, "no-docker.sock"),
})
os.environ.pop("VITALS_DEMO", None)


def write(path: str, text: str = "", mode: str = "w") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)
    return path


def host(*parts: str) -> str:
    return os.path.join(HOST, *[p.lstrip("/") for p in parts])
