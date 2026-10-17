"""Tiny entry point run inside the host's network namespace: python -m vitals.wol MAC BROADCAST..."""
import sys

from .devices import send_now

if __name__ == "__main__":
    send_now(sys.argv[1], sys.argv[2:] or ["255.255.255.255"])
    print("sent")
