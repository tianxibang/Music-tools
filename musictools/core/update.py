"""Updates from GitHub releases: find a newer version, download the file that matches this install, install it.

How the app was installed decides what is downloaded and how it is put in place:
  installer  Windows setup (per user): run the new setup, which closes and restarts the app
  rpm / deb  Linux package in /opt: hand the package to the system installer (it asks for the password)
  portable   unpacked .zip / .tar.gz: swap the files in place once the app has quit, then start it again
  source     running from a checkout: nothing to install, only point to the release page
The packaging writes the kind into an "install-kind" file next to the app; a build without one is portable.
"""

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .util import APP_NAME, APP_VERSION, NO_WINDOW, app_dir, config_dir

REPO = "tianxibang/Music-tools"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = f"{APP_NAME.replace(' ', '')}/{APP_VERSION} (updater)"
KINDS = ("installer", "rpm", "deb", "portable", "source")

ASSETS = {
    ("installer", "win32"): "MusicTools-setup-windows-x64.exe",
    ("portable", "win32"): "MusicTools-windows-x64.zip",
    ("portable", "linux"): "MusicTools-linux-x64.tar.gz",
    ("rpm", "linux"): "MusicTools-linux-x64.rpm",
    ("deb", "linux"): "MusicTools-linux-x64.deb",
}
STAGING = ".update"  # inside a portable app folder: the unpacked new version and the script that swaps it in


class UpdateError(Exception):
    pass


def parse_version(text):
    """'v4.1.1' -> (4, 1, 1); anything unparsable sorts lowest."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", text.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def newer(tag, current=APP_VERSION):
    a, b = list(parse_version(tag)), list(parse_version(current))
    n = max(len(a), len(b))  # 4.2 == 4.2.0
    return a + [0] * (n - len(a)) > b + [0] * (n - len(b))


def install_kind(folder=None):
    if folder is None:
        if not getattr(sys, "frozen", False):
            return "source"
        folder = app_dir()
    try:
        kind = (Path(folder) / "install-kind").read_text(encoding="utf-8").strip()
    except OSError:
        return "portable"
    return kind if kind in KINDS else "portable"


def platform_key():
    return "win32" if os.name == "nt" else "linux"


def latest(timeout=15):
    """The latest published release: {tag, version, name, notes, url, assets: {name: (url, size, digest)}}."""
    req = urllib.request.Request(LATEST_API, headers={"User-Agent": USER_AGENT,
                                                      "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return {
        "tag": data["tag_name"],
        "version": data["tag_name"].lstrip("v"),
        "name": data.get("name") or data["tag_name"],
        "notes": data.get("body") or "",
        "url": data.get("html_url") or RELEASES_PAGE,
        "assets": {a["name"]: (a["browser_download_url"], a.get("size", 0), a.get("digest") or "")
                   for a in data.get("assets", [])},
    }


def check(timeout=15):
    """The latest release if it is newer than this version, else None. Network errors raise."""
    rel = latest(timeout)
    return rel if newer(rel["tag"]) else None


def asset_for(release, kind=None, plat=None):
    """(name, url, size, digest) of the file this install needs, or None (source run, or not in the release)."""
    name = ASSETS.get((kind or install_kind(), plat or platform_key()))
    if not name or name not in release["assets"]:
        return None
    return (name, *release["assets"][name])


def can_install(kind=None, folder=None):
    """Whether this install can update itself (a portable copy needs a folder it may write to)."""
    kind = kind or install_kind()
    if kind == "source":
        return False
    if kind == "portable":
        return os.access(folder or app_dir(), os.W_OK)
    return True


def download(ctx, asset, dest_dir=None):
    """Download the asset with progress, check its SHA-256 against the release's, return the file's path."""
    name, url, size, digest = asset
    dest_dir = Path(dest_dir or config_dir() / "updates")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    part = dest.with_name(name + ".part")
    sha = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            done = 0
            while chunk := r.read(1 << 16):
                ctx.check()
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                ctx.progress(done, total, f"Downloading {name}: {done >> 20} / {total >> 20} MB")
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    algo, _, want = digest.partition(":")
    if algo == "sha256" and want.lower() != sha.hexdigest():
        part.unlink(missing_ok=True)
        raise UpdateError(f"{name} doesn't match the release's checksum; it was not installed. Try again later.")
    os.replace(part, dest)
    return dest


# --- installing ----------------------------------------------------------------------------------------------------

def install(ctx, file, kind=None, folder=None, pid=None):
    """Put the downloaded update in place. Returns a command to start once this process has quit (or None
    when the installer restarts the app itself); the caller then quits."""
    kind = kind or install_kind()
    folder = Path(folder or app_dir())
    pid = pid or os.getpid()
    if kind == "installer":
        # Inno Setup: /SILENT shows only its progress; it closes this app if it is still running and,
        # with /relaunch=1, starts it again when done
        subprocess.Popen([str(file), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                          "/relaunch=1"], creationflags=NO_WINDOW)
        return None
    if kind in ("rpm", "deb"):
        _install_package(ctx, Path(file), kind)
        return _after_exit(pid, [shutil.which("music-tools") or "/usr/bin/music-tools"])
    if kind == "portable":
        new = _unpack(Path(file), folder / STAGING)
        return _swap_script(pid, new, folder)
    raise UpdateError("This copy runs from source: update it with git pull.")


def _install_package(ctx, file, kind):
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise UpdateError(f"pkexec is missing, so the package can't be installed from here. Install "
                          f"{file} with your software center or package manager.")
    if kind == "rpm":
        tool = shutil.which("dnf5") or shutil.which("dnf") or shutil.which("zypper") or "rpm"
        cmd = [pkexec, tool, "install", "-y", str(file)] if tool != "rpm" else [pkexec, "rpm", "-U", str(file)]
        if tool.endswith("zypper"):
            cmd = [pkexec, tool, "--non-interactive", "install", "--allow-unsigned-rpm", str(file)]
    else:
        cmd = [pkexec, "apt-get", "install", "-y", str(file.resolve())]
    ctx.log("Installing the package (your system asks for your password)…")
    code, out, err = ctx.popen(cmd)
    if code in (126, 127):  # pkexec: password dialog dismissed / not authorized
        raise UpdateError("The update wasn't installed: the password prompt was cancelled.")
    if code != 0:
        raise UpdateError(f"The package manager failed:\n{(err or out).strip()[-600:]}")


def _unpack(archive, staging):
    """Unpack a release archive into staging/new and return the folder holding the app (its MusicTools/)."""
    shutil.rmtree(staging, ignore_errors=True)
    target = staging / "new"
    target.mkdir(parents=True)
    root = target.resolve()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if not (root / n).resolve().is_relative_to(root):
                    raise UpdateError(f"Unsafe path in {archive.name}: {n}")
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            t.extractall(target, filter="data")
    inner = target / "MusicTools"
    if not (inner / ("MusicTools.exe" if os.name == "nt" else "MusicTools")).is_file():
        raise UpdateError(f"{archive.name} doesn't contain the app.")
    return inner


def _swap_script(pid, new, folder):
    """A script that waits for this process to quit, copies the new version over the old, and starts it."""
    staging = folder / STAGING
    if os.name == "nt":
        script = staging / "apply.cmd"
        script.write_text("\r\n".join([
            "@echo off",
            ":wait",
            f'tasklist /FI "PID eq {pid}" /NH | find "{pid}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)',
            # The bundled libraries are replaced as a whole so no stale ones stay behind; the rest is copied
            # over, which keeps a "bin" folder of tools the user put there
            f'rmdir /s /q "{folder}\\_internal"',
            f'robocopy "{new}" "{folder}" /E /NFL /NDL /NJH /NJS /NP >nul',
            f'start "" "{folder}\\MusicTools.exe"',
        ]) + "\r\n", encoding="utf-8")
        return ["cmd", "/c", str(script)]
    script = staging / "apply.sh"
    q = shlex.quote
    script.write_text("\n".join([
        "#!/bin/sh",
        f"while kill -0 {pid} 2>/dev/null; do sleep 0.3; done",
        f"rm -rf {q(str(folder / '_internal'))}",
        f"cp -a {q(str(new))}/. {q(str(folder))}/",
        f"cd {q(str(folder))} && exec ./MusicTools",
    ]) + "\n", encoding="utf-8")
    return ["/bin/sh", str(script)]


def _after_exit(pid, cmd):
    """A command that waits for this process to quit and then runs cmd (so two copies never run at once)."""
    return ["/bin/sh", "-c", f"while kill -0 {pid} 2>/dev/null; do sleep 0.3; done; exec {shlex.join(cmd)}"]


def launch_detached(cmd):
    """Start the command so it outlives this process."""
    if os.name == "nt":
        subprocess.Popen(cmd, creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
    else:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)


def clean_leftovers(folder=None):
    """Remove the staging folder and downloads of a finished update (called at startup)."""
    if folder or getattr(sys, "frozen", False):
        shutil.rmtree(Path(folder or app_dir()) / STAGING, ignore_errors=True)
    shutil.rmtree(config_dir() / "updates", ignore_errors=True)
