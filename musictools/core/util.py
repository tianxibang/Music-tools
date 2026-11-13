"""Settings, external tool lookup and small helpers shared by all tools."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "Music Tools"
APP_VERSION = "4.1.1"
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
    # Codecs phones play well as they are. Anything else that lands in the library (Dolby Atmos / E-AC-3 or
    # AC-3 in an .m4a, WMA, ALAC, …) is converted to Opus for the phone.
    "phone_codecs": ["mp3", "aac", "opus", "vorbis"],
    # A lossy file whose bitrate is more than this many times the Opus bitrate is converted too: the copy gets
    # much smaller and little is lost. Below that it is copied as it is (another lossy pass only costs quality).
    "keep_lossy_below": 1.5,
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
    "watch_interval": 15,            # seconds between looks at the library
    "pipeline_steps": ["convert", "art", "loudness", "playlists", "sync"],
    "rename_template": "{artist} - {title}",
    "sort_template": "{albumartist}/{album}/{track:02} - {title}",
    "flatten": "",
    "net_enabled": False,
    "net_port": 47321,
    "net_require_pairing": True,
    "net_usb": True,
    "phone_mode": "app",  # app | adb
    "import_source": "",
    "role": "main",                  # main | follower (mirrors another PC's library)
    "follow_get": "flac",            # flac | opus | both
    "follow_auto": True,
    "follow_interval": 30,           # minutes
    "follow_send_auto": False,
    "follow_new_music": str(MUSIC / "New music"),
    "follow_big_delete": 50,         # more removals than this (and over 10 % of the files) need a confirmation
    "lossy_folder": "Lossy",         # library subfolder for MP3/AAC imports
    "playlists_folder": "Playlists",
    "health_min_rate": 44100,        # Hz: lossless files below this are "low quality"
    "health_min_bitrate": 128,       # kbps: lossy files below this are "low quality"
    "lossy_cliff_db": 25,            # dB drop in the highs that marks a FLAC made from a lossy file
    # Words that make a recording a different version of a song, not a copy of it. Remasters, deluxe editions
    # and featured artists don't count: those are the same recording.
    "version_words": "demo, live, acoustic, unplugged, remix, rmx, mix, instrumental, karaoke, a cappella, "
                     "acapella, edit, version, cover, orchestral, piano, stripped, session, sped up, slowed, "
                     "reprise, extended, rework, bootleg",
    "cover_size": 500,               # px, from the Cover Art Archive: 250 | 500 | 1200
    "loudness_target": -18.0,        # LUFS that ReplayGain / R128 tags aim for (ReplayGain 2 standard: -18)
    "lrclib_gap": 1.0,               # seconds between requests to LRCLIB: it's a free service, so go easy on it
    "lyrics_recheck_days": 14,       # songs found nowhere (or only unsure) aren't asked about again sooner
    "timeline_snap": 7,              # px: lyric edges snap to the playhead this close
    "import_check_fakes": True,
    "lyrics_save": "embed",          # embed | file | both (lyrics.SAVE_MODES)
    "lyrics_tap_offset": 0.0,        # seconds taken off each tap, for reaction time
    "lyrics_filter": "all",
    "update_check": True,            # look for a new version on GitHub at startup
    "update_skip": "",               # release tag the user chose to skip
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
        s.validate()
        return s

    def validate(self):
        """Put back the default of any option whose value is unusable (edited by hand, say), and keep the rest
        inside its limits."""
        from .options import BY_KEY
        for key, opt in BY_KEY.items():
            try:
                self[key] = opt.check(self[key])
            except (TypeError, ValueError):
                self[key] = DEFAULTS[key]

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


def trash(path):
    """Move a file to the system trash. (PySide6 returns a bool, not the (bool, path) its stubs promise.)"""
    from PySide6.QtCore import QFile
    res = QFile.moveToTrash(str(path))
    return bool(res[0] if isinstance(res, tuple) else res)


def copy_path_for(src, library=None, opus=None):
    """Where a library file that is copied as it is (a small MP3, say) lives in the export."""
    library = Path(library or SETTINGS.library)
    opus = Path(opus or SETTINGS.opus)
    return opus.joinpath(*(sanitize_android(p) for p in Path(src).relative_to(library).parts))


def export_path_for(src, library=None, opus=None):
    """Where a library file lives in the export: as .opus when it is converted, else under its own name."""
    if converts_to_opus(src):
        return opus_path_for(src, library, opus)
    return copy_path_for(src, library, opus)


_MUTAGEN_CODECS = {"FLAC": "flac", "MP3": "mp3", "AAC": "aac", "OggOpus": "opus", "OggVorbis": "vorbis",
                   "OggFLAC": "flac", "ASF": "wma", "WAVE": "pcm", "AIFF": "pcm", "WavPack": "wavpack",
                   "MonkeysAudio": "ape", "TrueAudio": "tta"}
_MP4_CODECS = {"ec-3": "eac3", "ac-3": "ac3"}
LOSSLESS_CODECS = {"flac", "alac", "pcm", "wavpack", "ape", "tta"}


def stream_info(path):
    """(codec, channels, kbps, lossless) of an audio file. codec is '' when mutagen doesn't say."""
    import mutagen
    path = Path(path)
    m = mutagen.File(path)
    if m is None:
        raise ValueError("Not a recognised audio file")
    info = m.info
    codec = _MUTAGEN_CODECS.get(type(m).__name__, "")
    if type(m).__name__ == "MP4":
        c = (getattr(info, "codec", "") or "").lower()  # "mp4a.40.2" (AAC), "alac", "ec-3", …
        codec = "aac" if c.startswith("mp4a") else "alac" if c.startswith("alac") else _MP4_CODECS.get(c, c)
    channels = getattr(info, "channels", 0) or 0
    kbps = (getattr(info, "bitrate", 0) or 0) / 1000
    length = getattr(info, "length", 0) or 0
    if not kbps and length:  # mutagen reports 0 for some codecs (E-AC-3): the whole file's rate will do
        kbps = path.stat().st_size * 8 / length / 1000
    return codec, channels, round(kbps), codec in LOSSLESS_CODECS


_VERDICTS = {}  # path -> ((size, mtime, bitrate), converts)


def converts_to_opus(path, bitrate=None):
    """Whether a library file gets an Opus version in the export (True), or is copied as it is (False).

    Lossless files always do. So do lossy files phones play badly (Dolby Atmos / E-AC-3, AC-3, WMA, …),
    multichannel files (downmixed to stereo) and lossy files far above the Opus bitrate (keep_lossy_below).
    Smaller files in the phone_codecs formats are copied as they are. Files mutagen can't read are copied too.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext in LOSSLESS_EXTS:
        return True
    target = bitrate or SETTINGS["bitrate"]
    try:
        st = path.stat()
    except OSError:  # gone (just renamed, say): the last verdict, else the extension alone
        cached = _VERDICTS.get(path)
        return cached[1] if cached else False
    phone, keep = frozenset(SETTINGS["phone_codecs"]), SETTINGS["keep_lossy_below"]
    key = (st.st_size, st.st_mtime_ns, target, phone, keep)
    cached = _VERDICTS.get(path)
    if cached and cached[0] == key:
        return cached[1]
    try:
        codec, channels, kbps, lossless = stream_info(path)
        verdict = (lossless or channels > 2 or bool(codec) and codec not in phone
                   or codec != "opus" and kbps > target * keep)
    except Exception:
        verdict = False
    _VERDICTS[path] = (key, verdict)
    return verdict


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
