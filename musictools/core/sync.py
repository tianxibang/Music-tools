"""Pushing the Opus library to an Android phone over adb, by USB or Wi-Fi (was sync-music.fish)."""

import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from .util import find_tool, run, sanitize_android


@dataclass
class Device:
    serial: str
    state: str
    model: str = ""

    @property
    def wireless(self):
        return ":" in self.serial or "._adb-tls-connect." in self.serial

    @property
    def ready(self):
        return self.state == "device"

    @property
    def label(self):
        name = self.model.replace("_", " ") or self.serial
        return f"{name}  ·  {'Wi-Fi' if self.wireless else 'USB'}"


def adb():
    return find_tool("adb")


def devices():
    exe = adb()
    if not exe:
        return []
    out = run([exe, "devices", "-l"], timeout=15).stdout
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
            devs.append(Device(parts[0], parts[1], model))
    return devs


def pair(hostport, code):
    r = run([adb(), "pair", hostport, code], timeout=30)
    out = (r.stdout + r.stderr).strip()
    return "Successfully paired" in out, out


def connect(hostport):
    r = run([adb(), "connect", hostport], timeout=20)
    out = (r.stdout + r.stderr).strip()
    return out.startswith(("connected to", "already connected")), out


def disconnect(serial):
    run([adb(), "disconnect", serial], timeout=10)


def mdns_services():
    """Phones advertising wireless debugging on the local network: [(name, host:port)]."""
    r = run([adb(), "mdns", "services"], timeout=10)
    found = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S+)\s+_adb-tls-connect\._tcp\.?\s+(\S+:\d+)", line.strip())
        if m:
            found.append((m.group(1), m.group(2)))
    return found


def storage(serial):
    out = run([adb(), "-s", serial, "shell", "df -h /sdcard"], timeout=15).stdout.strip().splitlines()
    if out and len(out[-1].split()) >= 5:
        cols = out[-1].split()
        return cols[3], cols[1]  # free, total
    return None


def pick_device(ctx, serial=""):
    """Return a ready Device or None (with the reason logged)."""
    devs = devices()
    ready = [d for d in devs if d.ready]
    if serial:
        match = next((d for d in ready if d.serial == serial), None)
        if match:
            return match
        if ready:
            ctx.log(f"Device '{serial}' isn't connected; using {ready[0].label} instead.", "warn")
            return ready[0]
    if ready:
        if len(ready) > 1:
            ctx.log(f"Several devices connected, using {ready[0].label}. Pick one on the Sync page.", "warn")
        return ready[0]
    states = {d.state for d in devs}
    if "unauthorized" in states:
        ctx.log("Phone found but not authorized: unlock it and tap 'Allow' on the USB debugging prompt.", "error")
    elif "offline" in states:
        ctx.log("Phone is listed as offline. Try re-plugging it, or reconnect over Wi-Fi.", "error")
    else:
        ctx.log("No Android device detected. Plug it in with USB debugging on, or connect over Wi-Fi.", "error")
    return None


@dataclass
class SyncPlan:
    serial: str
    local: Path
    remote: str
    to_copy: list
    to_delete: list
    local_files: dict


def make_plan(ctx, local, remote, keep=False, serial="", dry=False):
    """Check everything, tidy local names, and work out what to copy/delete. None if it can't sync."""
    exe = adb()
    if not exe:
        ctx.log("adb not found. Install Android platform-tools, or set its path in Settings.", "error")
        return None
    local = Path(local)
    remote = remote.strip().rstrip("/")

    # With deletion on, syncing an empty or wrong folder would wipe the phone folder.
    if not local.is_dir():
        ctx.log(f"Local folder '{local}' doesn't exist.", "error")
        return None
    n_local = sum(1 for p in local.rglob("*.opus") if p.is_file())
    if n_local == 0:
        ctx.log(f"No .opus files in '{local}'. Refusing to sync (it could empty the phone folder).", "error")
        return None
    if not remote.startswith("/") or remote.count("/") < 2:
        ctx.log(f"Phone folder '{remote}' looks wrong; use something like /sdcard/Music/opus_export.", "error")
        return None

    dev = pick_device(ctx, serial)
    if not dev:
        return None
    if space := storage(dev.serial):
        ctx.log(f"Phone: {dev.label} — {space[0]} free of {space[1]}")

    # Sanitize local filenames, deepest first, never overwriting
    clashes = 0
    for dirpath, dirnames, filenames in os.walk(local, topdown=False):
        for name in filenames + dirnames:
            clean = sanitize_android(name)
            if clean == name:
                continue
            target = Path(dirpath) / clean
            if not clean or target.exists():
                ctx.log(f"  skipped rename (name would clash): {name}", "warn")
                clashes += 1
            elif dry:
                ctx.log(f"  would rename: {name} → {clean}")
            else:
                ctx.log(f"  renamed: {name} → {clean}")
                os.rename(Path(dirpath) / name, target)

    ctx.log(f"Local:  {local}  ({n_local} tracks)")
    ctx.log(f"Phone:  {remote}")
    ctx.log("Mode:   copy only (extra files on the phone are kept)" if keep
            else "Mode:   mirror (extra files on the phone get deleted)")
    ctx.log("Comparing with the phone…")

    local_files = {p.relative_to(local).as_posix(): p.stat().st_size for p in local.rglob("*") if p.is_file()}
    r = run([exe, "-s", dev.serial, "shell",
             f"find {shlex.quote(remote)} -type f -exec stat -c '%s %n' {{}} + 2>/dev/null"], timeout=120)
    remote_files = {}
    prefix = remote + "/"
    for line in r.stdout.splitlines():
        size, _, path = line.partition(" ")
        if path.startswith(prefix) and size.isdigit():
            remote_files[path[len(prefix):]] = int(size)

    to_copy = sorted(rel for rel, size in local_files.items() if remote_files.get(rel) != size)
    to_delete = [] if keep else sorted(rel for rel in remote_files if rel not in local_files)
    ctx.log(f"To copy:   {len(to_copy)}")
    ctx.log(f"To delete: {len(to_delete)}")
    for rel in to_delete[:15]:
        ctx.log(f"  − {rel}", "warn")
    if len(to_delete) > 15:
        ctx.log(f"  …and {len(to_delete) - 15} more", "warn")
    return SyncPlan(dev.serial, local, remote, to_copy, to_delete, local_files)


def execute(ctx, plan):
    exe = adb()
    base = [exe, "-s", plan.serial]
    prefix = plan.remote + "/"
    start = time.time()
    total = len(plan.to_copy) + len(plan.to_delete)
    done = failed = 0

    def shell(cmd):
        return run(base + ["shell", cmd], timeout=120)

    dirs = sorted({prefix + rel.rpartition("/")[0] for rel in plan.to_copy if "/" in rel} | {plan.remote})
    for i in range(0, len(dirs), 50):
        shell("mkdir -p " + " ".join(shlex.quote(d) for d in dirs[i:i + 50]))

    for rel in plan.to_copy:
        ctx.check()
        code, _, err = ctx.popen(base + ["push", str(plan.local / rel), prefix + rel])
        if code != 0:
            failed += 1
            if not ctx.cancelled:
                ctx.log(f"  FAIL  {rel}: {err.strip()}", "error")
        else:
            ctx.log(f"  copied   {rel}", "ok")
        done += 1
        ctx.progress(done, total, f"Copying {done} / {len(plan.to_copy)}")

    for i in range(0, len(plan.to_delete), 50):
        ctx.check()
        chunk = plan.to_delete[i:i + 50]
        shell("rm -f " + " ".join(shlex.quote(prefix + rel) for rel in chunk))
        for rel in chunk:
            ctx.log(f"  deleted  {rel}", "warn")
        done += len(chunk)
        ctx.progress(done, total, f"Deleting {done - len(plan.to_copy)} / {len(plan.to_delete)}")
    if plan.to_delete:
        shell(f"find {shlex.quote(plan.remote)} -mindepth 1 -type d -empty -delete")

    # Best effort: ask Android to index the new files so music apps see them right away.
    shell("content call --uri content://media/ --method scan_volume --arg external_primary")
    ctx.log(f"Sync complete in {round(time.time() - start)} s: {len(plan.to_copy) - failed} copied, "
            f"{len(plan.to_delete)} deleted" + (f", {failed} failed." if failed else "."),
            "ok" if not failed else "warn")
    return failed == 0
