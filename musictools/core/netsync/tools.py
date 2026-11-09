"""What the phone app can see and do besides syncing: the track list (for stats and choosing what to
sync), playlists, and running the one-click pipeline on the PC."""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import convert, pipeline, playlists
from ..jobs import EXCLUSIVE, Cancelled, Context
from ..tags import read_track
from ..util import AUDIO_EXTS, LOSSLESS_EXTS, export_path_for, opus_path_for, safe_filename
from .library import safe_join
from .security import state_dir

TRACK_FIELDS = ("artist", "albumartist", "album", "title", "genre", "date", "tracknumber", "discnumber")


class TrackIndex:
    """Tags of every Opus file in the export, cached by size and mtime (reading ~1000 files takes a while)."""

    def __init__(self, cache=None):
        self.cache_path = Path(cache or state_dir() / "tracks.json")
        self._lock = threading.Lock()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    @staticmethod
    def _entry(path, rel, st, flac):
        t = read_track(path)
        tags = {k: t.tag(k) for k in TRACK_FIELDS if t.tag(k)}
        source = ""
        if flac and flac.is_file():  # quality of the original: "16-bit / 44.1 kHz", or "MP3 · 320 kbps" if lossy
            f = read_track(flac)
            if flac.suffix.lower() not in LOSSLESS_EXTS:
                source = f"{flac.suffix[1:].upper()} · {f.bitrate} kbps" if f.bitrate else flac.suffix[1:].upper()
            elif f.sample_rate:
                source = f"{f.bits or '?'}-bit / {f.sample_rate / 1000:g} kHz"
        return {"path": rel, "size": st.st_size, "mtime": st.st_mtime, "duration": round(t.duration, 1),
                "bitrate": t.bitrate, "art": t.has_art, "source": source, "tags": tags}

    def tracks(self, opus_root, library=None):
        root = Path(opus_root)
        to_flac = ({o: f for f, o in convert.plan(library, root) + convert.lossy_plan(library, root)}
                   if library and Path(library).is_dir() else {})
        with self._lock:
            files = []
            if root.is_dir():
                for p in root.rglob("*"):
                    if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                        files.append((p, p.relative_to(root).as_posix(), p.stat()))
            fresh, todo = {}, []
            for p, rel, st in files:
                old = self.cache.get(rel)
                if old and old["size"] == st.st_size and old["mtime"] == st.st_mtime and "source" in old:
                    fresh[rel] = old
                else:
                    todo.append((p, rel, st, to_flac.get(p)))
            with ThreadPoolExecutor(max_workers=8) as pool:
                for entry in pool.map(lambda a: self._entry(*a), todo):
                    fresh[entry["path"]] = entry
            self.cache = fresh
            if todo:
                try:
                    self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
                except OSError:
                    pass
            return sorted(fresh.values(), key=lambda e: e["path"].lower())


def summary(library, opus):
    """Totals for the stats screen."""
    def walk(root, ext):
        count = size = 0
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.lower().endswith(ext):
                    count += 1
                    try:
                        size += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        return count, size

    flac_count, flac_bytes = walk(library, ".flac")
    opus_count, opus_bytes = walk(opus, ".opus")
    pending = len(convert.status(library, opus)["pending"]) if Path(library).is_dir() else 0
    return {"flac_count": flac_count, "flac_bytes": flac_bytes, "opus_count": opus_count,
            "opus_bytes": opus_bytes, "pending": pending}


# ------------------------------------------------------------------------ tag edits from followers


def library_file(library, opus, rel, root="library"):
    """The library file for a path as a device knows it: in the library, or in the Opus export."""
    library, opus = Path(library), Path(opus)
    if root == "opus":
        export = safe_join(opus, rel)
        mapping = {o: f for f, o in convert.plan(library, opus) + convert.lossy_plan(library, opus)}
        return mapping.get(export)
    path = safe_join(library, rel)
    return path if path and path.is_file() and path.suffix.lower() in AUDIO_EXTS else None


def apply_tag_edits(library, opus, path, changes, force=False):
    """Apply {field: (old, new)} to a library file and its export copy.

    A field whose value on this PC is no longer `old` (it was changed here too) isn't touched unless forced:
    it comes back in conflicts as {field: value here}.
    """
    from ..organize import save_tags
    from ..tags import FIELDS
    current = read_track(path).tags
    write, applied, conflicts = {}, [], {}
    for field, (old, new) in changes.items():
        if field not in FIELDS:
            continue
        here = current.get(field, "")
        if here == new:
            applied.append(field)
        elif force or here == old:
            write[field] = new
            applied.append(field)
        else:
            conflicts[field] = here
    if write:
        save_tags(path, write, library, opus)
    return {"applied": applied, "conflicts": conflicts}


# ------------------------------------------------------------------------ playlists


def _playlist_file(library, name):
    """The playlist file for a name, or None if the name is unusable (it never leaves the Playlists folder)."""
    clean = safe_filename(name or "", "")
    if not clean or clean != name.strip():
        return None
    return safe_join(playlists.folder(library), f"{clean}.m3u8")


def list_playlists(library, opus):
    """Playlists as the phone sees them: track paths in the Opus export."""
    library, opus = Path(library), Path(opus)
    out = []
    for pl in playlists.list_playlists(library):
        tracks, missing = [], 0
        for e in playlists.load(pl):
            try:
                o = export_path_for(e, library, opus)
            except ValueError:
                o = e
            if o.exists() and o.is_relative_to(opus):
                tracks.append(o.relative_to(opus).as_posix())
            else:
                missing += 1
        out.append({"name": pl.stem, "tracks": tracks, "missing": missing})
    return out


def save_playlist(library, opus, name, tracks):
    """Save a playlist edited on the phone. tracks are Opus export paths; they're stored as the FLACs.

    Entries the phone can't see (FLACs not converted yet) are kept at the end rather than dropped.
    Raises ValueError for a bad name or unknown tracks.
    """
    library, opus = Path(library), Path(opus)
    path = _playlist_file(library, name)
    if path is None:
        raise ValueError("Invalid playlist name")
    to_flac = {o: f for f, o in convert.plan(library, opus) + convert.lossy_plan(library, opus)}
    flacs = []
    for rel in tracks:
        o = safe_join(opus, rel) if isinstance(rel, str) else None
        if o is None or o not in to_flac:
            raise ValueError(f"Unknown track: {rel}")
        flacs.append(to_flac[o])
    kept = []
    for existing in [path] + [p for p in playlists.list_playlists(library) if p.stem == name and p != path]:
        if existing.is_file():
            kept += [e for e in playlists.load(existing)
                     if e.suffix.lower() == ".flac" and not opus_path_for(e, library, opus).exists()
                     and e not in flacs]
    playlists.save(path, flacs + kept)
    for other in playlists.list_playlists(library):  # e.g. an older "Name.m3u": now replaced by the .m3u8
        if other.stem == path.stem and other != path:
            other.unlink()
    playlists.export(Context(), library, opus)
    return path


def delete_playlist(library, opus, name):
    library = Path(library)
    found = [p for p in playlists.list_playlists(library) if p.stem == name]
    if not found or _playlist_file(library, name) is None:
        return False
    for p in found:
        p.unlink()
    playlists.export(Context(), library, opus)
    return True


# ------------------------------------------------------------------------ pipeline


class _LogContext(Context):
    def __init__(self):
        super().__init__()
        self.lines = []
        self.progress_state = (0, 0, "")

    def log(self, msg, level="info"):
        self.lines.append({"text": msg, "level": level})
        del self.lines[:-1000]

    def progress(self, done, total, text=""):
        self.progress_state = (done, total, text)


class RemotePipeline:
    """The desktop pipeline started from the phone. Shares one lock with the desktop's own jobs,
    so the two never convert or move the same files at once."""

    def __init__(self, settings, log=None):
        self.settings = settings
        self.log = log or (lambda msg, level="info": None)
        self.ctx = None
        self.running = False
        self.started_by = ""
        self.started = 0.0
        self.finished = 0.0
        self.error = ""

    def steps(self):
        # With the phone app, the pipeline's "sync" step does nothing (the app syncs itself): don't offer it
        app = self.settings.get("phone_mode", "app") == "app"
        return [{"key": k, "label": label, "default": k in self.settings["pipeline_steps"]}
                for k, label in pipeline.STEPS if not (app and k == "sync")]

    def start(self, steps, device_name):
        valid = {k for k, _ in pipeline.STEPS}
        steps = [s for s in steps if s in valid]
        if not steps:
            raise ValueError("No steps chosen")
        if not EXCLUSIVE.acquire(blocking=False):
            return False
        self.ctx = _LogContext()
        self.running, self.error = True, ""
        self.started_by, self.started, self.finished = device_name, time.time(), 0.0
        self.log(f"Pipeline started from {device_name}: {', '.join(steps)}")

        def work():
            try:
                pipeline.run(self.ctx, self.settings, steps)
            except Cancelled:
                self.ctx.log("Cancelled.", "warn")
            except Exception as e:
                self.error = str(e)
                self.ctx.log(f"Error: {e}", "error")
            finally:
                self.running, self.finished = False, time.time()
                EXCLUSIVE.release()
                self.log("Pipeline from the phone finished." if not self.error else
                         f"Pipeline from the phone failed: {self.error}", "ok" if not self.error else "error")

        threading.Thread(target=work, daemon=True).start()
        return True

    def cancel(self):
        if self.running and self.ctx:
            self.ctx.cancel()

    def status(self, since=0):
        lines = self.ctx.lines if self.ctx else []
        done, total, text = self.ctx.progress_state if self.ctx else (0, 0, "")
        return {"steps": self.steps(), "running": self.running, "busy": EXCLUSIVE.locked() and not self.running,
                "started_by": self.started_by, "started": self.started, "finished": self.finished,
                "error": self.error, "lines": lines[since:], "next": len(lines),
                "progress": {"done": done, "total": total, "text": text}}
