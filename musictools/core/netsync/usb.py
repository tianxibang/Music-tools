"""Sync over a USB cable when there's no network: `adb reverse` makes the phone's localhost:PORT reach
this PC's sync server. The app connects to 127.0.0.1 and everything else (TLS, pinning, pairing) is
the same as over Wi-Fi. Needs USB debugging on the phone, like the adb sync."""

from .. import sync
from ..util import run


def link_usb_phones(port):
    """Set up the reverse tunnel for every phone plugged in by USB. Returns the labels of linked phones."""
    exe = sync.adb()
    if not exe:
        return []
    linked = []
    want = f"tcp:{port} tcp:{port}"
    for dev in sync.devices():
        if not dev.ready or dev.wireless:
            continue
        existing = run([exe, "-s", dev.serial, "reverse", "--list"], timeout=10).stdout
        if want not in existing:
            r = run([exe, "-s", dev.serial, "reverse", f"tcp:{port}", f"tcp:{port}"], timeout=10)
            if r.returncode != 0:
                continue
        linked.append(dev.model.replace("_", " ") or dev.serial)
    return linked


def unlink_usb_phones(port):
    exe = sync.adb()
    if not exe:
        return
    for dev in sync.devices():
        if dev.ready and not dev.wireless:
            run([exe, "-s", dev.serial, "reverse", "--remove", f"tcp:{port}"], timeout=10)
