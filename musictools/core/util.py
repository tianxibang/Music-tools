"""Settings, external tool lookup and small helpers shared by all tools."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Music Tools"
APP_VERSION = "2.0"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MUSIC = Path.home() / "Music"

AUDIO_EXTS = {".flac", ".opus", ".ogg", ".mp3", ".m4a", ".aac", ".wav", ".wv", ".ape", ".aiff", ".aif", ".wma"}
LOSSLESS_EXTS = {".flac", ".wav", ".wv", ".ape", ".aiff", ".aif"}


def config_dir():
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "music-tools"


DEFAULTS = {
    "library": str(MUSIC / "lossless_music"),
    "opus": str(MUSIC / "opus_export"),
    "bitrate": 128,
    "jobs": os.cpu_count() or 4,
    "remote": "/sdcard/Music/opus_export",
    "keep": False,
    "serial": "",
    "wifi_addr": "",
    "wifi_autoconnect": True,
    "sync_confirm": "deleting",  # always | deleting | never
    "last_sync": 0,
    "tool_opusenc": "",
    "tool_ffmpeg": "",
    "tool_adb": "",
    "watch": False,
    "watch_sync": False,
    "pipeline_steps": ["convert", "art", "loudness", "playlists", "sync"],
    "rename_template": "{artist} - {title}",
    "sort_template": "{albumartist}/{album}/{track:02} - {title}",
    "flatten": "",
    "net_enabled": False,
    "net_port": 47321,
    "net_require_pairing": True,
    "net_usb": True,
}


class Settings(dict):
    path = config_dir() / "settings.json"

    @classmethod
    def load(cls):
        s = cls(DEFAULTS)
        try:
            s.update(json.loads(cls.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        return s

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self, indent=2), encoding="utf-8")
        except OSError:
            pass

    @property
    def library(self):
        return Path(self["library"])

    @property
    def opus(self):
        return Path(self["opus"])


SETTINGS = Settings.load()


def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def find_tool(name):
    """Settings override, then next to the app (or its bin/ folder), then PATH."""
    override = SETTINGS.get(f"tool_{name}", "")
    if override and Path(override).is_file():
        return override
    for d in (app_dir(), app_dir() / "bin"):
        for cand in (d / name, d / f"{name}.exe"):
            if cand.is_file():
                return str(cand)
    return shutil.which(name)


def run(cmd, timeout=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 1, e.stdout or "", "timed out")


# Characters Android rejects in filenames: ? * : < > | "
# (same rules as the original fish scripts, so existing exports keep matching)
_ANDROID = [("?", ""), ("*", ""), (":", " -"), ("|", "-"), ("<", ""), (">", ""), ('"', "")]


def sanitize_android(name):
    for bad, good in _ANDROID:
        name = name.replace(bad, good)
    return name


def safe_filename(name, fallback="Unknown"):
    """A single path component that is valid on Windows, Linux and Android."""
    name = sanitize_android(name).replace("/", "-").replace("\\", "-")
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return name[:180] or fallback


def opus_path_for(flac, library=None, opus=None):
    """Where flac2opus puts the Opus version of a file in the library."""
    library = Path(library or SETTINGS.library)
    opus = Path(opus or SETTINGS.opus)
    rel = Path(flac).relative_to(library).with_suffix(".opus")
    return opus.joinpath(*(sanitize_android(p) for p in rel.parts))


def audio_files(folder, exts=AUDIO_EXTS):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in exts and p.is_file())


def remove_empty_dirs(root):
    root = Path(root)
    for dirpath, _, _ in sorted(os.walk(root), key=lambda w: -len(w[0])):
        if Path(dirpath) != root:
            try:
                os.rmdir(dirpath)
            except OSError:
                pass


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def human_duration(secs, long=False):
    secs = int(secs or 0)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if long:
        d, h = divmod(h, 24)
        parts = [f"{d}d"] * bool(d) + [f"{h}h"] * bool(h) + [f"{m}m"]
        return " ".join(parts)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def reveal(path):
    path = Path(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
