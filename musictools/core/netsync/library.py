"""What the server offers (the Opus export as a manifest) and what it takes in (the inbox).

Inbox flow for a FLAC sent from a phone:
  received -> (already in the library?) duplicate | imported -> converting -> ready | failed
The phone downloads the Opus once it's ready, deletes its own FLAC, then acknowledges the item.
"""

import hashlib
import hmac
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import convert, tags
from ..jobs import Context
from ..organize import plan_moves
from ..util import opus_path_for, safe_filename
from .security import state_dir

ACCEPTED = {".flac"}
MAX_UPLOAD = 2 * 1024 ** 3


def safe_join(root, rel):
    """root/rel, or None if rel tries to leave root (.., absolute paths, symlinks out)."""
    root = Path(root).resolve()
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel:
        return None
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) and path != root else None


def manifest(opus_root):
    """Every file in the Opus export (tracks, playlists, …) with size and mtime, like the adb sync mirrors."""
    root = Path(opus_root)
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.endswith(".part"):
                st = p.stat()
                files.append({"path": p.relative_to(root).as_posix(), "size": st.st_size, "mtime": int(st.st_mtime)})
    return files


def flac_md5(path):
    """The audio checksum stored in the FLAC header: same music = same value, whatever the tags."""
    try:
        from mutagen.flac import FLAC
        md5 = FLAC(path).info.md5_signature
        return f"{md5:032x}" if md5 else ""
    except Exception:
        return ""


class LibraryIndex:
    """FLAC audio checksums for the library, cached by size and mtime so re-scans are cheap."""

    def __init__(self, library, cache=None):
        self.library = Path(library)
        self.cache_path = Path(cache or state_dir() / "index.json")
        self._lock = threading.Lock()
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def find(self, md5):
        """Library FLAC with this audio checksum, or None."""
        if not md5:
            return None
        with self._lock:
            fresh = {}
            hit = None
            for p in self.library.rglob("*.flac"):
                st = p.stat()
                key = p.as_posix()
                old = self.cache.get(key)
                if old and old[0] == st.st_size and old[1] == st.st_mtime:
                    fresh[key] = old
                else:
                    fresh[key] = [st.st_size, st.st_mtime, flac_md5(p)]
                if fresh[key][2] == md5:
                    hit = p
            self.cache = fresh
            try:
                self.cache_path.write_text(json.dumps(self.cache), encoding="utf-8")
            except OSError:
                pass
            return hit


@dataclass
class Item:
    id: str
    device: str
    name: str
    sha256: str
    state: str = "receiving"
    detail: str = ""
    library_path: str = ""     # relative to the library
    opus_path: str = ""        # relative to the Opus export: what the phone should download
    created: float = 0


class Inbox:
    """Receives uploads into a staging folder, then imports and converts them one at a time."""

    def __init__(self, settings, log=None, folder=None):
        self.settings = settings
        self.folder = Path(folder or state_dir() / "inbox")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log = log or (lambda msg, level="info": None)
        self.items = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self.index = LibraryIndex(settings.library)
        for f in self.folder.glob("*/item.json"):
            try:
                item = Item(**json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if item.state in ("receiving", "converting", "imported"):
                # interrupted: received files get processed again, half uploads are dropped
                item.state = "received" if (f.parent / "upload").exists() or item.library_path else "failed"
                if item.state == "failed":
                    item.detail = "Upload was interrupted"
            self.items[item.id] = item
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def _save(self, item):
        d = self.folder / item.id
        d.mkdir(exist_ok=True)
        (d / "item.json").write_text(json.dumps(asdict(item), indent=2), encoding="utf-8")

    def get(self, item_id, device):
        item = self.items.get(item_id)
        return item if item and item.device == device else None

    def list(self, device=None):
        return [i for i in self.items.values() if device is None or i.device == device]

    def receive(self, device, name, sha256, size, stream):
        """Store an upload. Raises ValueError with a message the client can show."""
        name = safe_filename(Path(name).name)
        if Path(name).suffix.lower() not in ACCEPTED:
            raise ValueError("Only FLAC files can be sent to the library")
        if not 0 < size <= MAX_UPLOAD:
            raise ValueError("File is empty or too large")
        if len(sha256) != 64:
            raise ValueError("Missing sha256")
        # Same file sent again (e.g. the phone retried after a lost answer): reuse that item
        with self._lock:
            for item in self.items.values():
                if item.device == device and item.sha256 == sha256.lower() and item.state != "failed":
                    remaining = size
                    while remaining > 0:  # still drain the body so the connection stays usable
                        chunk = stream.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                    return item
        item = Item(uuid.uuid4().hex, device, name, sha256.lower(), created=time.time())
        self._save(item)
        with self._lock:
            self.items[item.id] = item
        target = self.folder / item.id / "upload"
        h = hashlib.sha256()
        remaining = size
        try:
            with open(target, "wb") as f:
                while remaining > 0:
                    chunk = stream.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ValueError("Upload was cut off")
                    h.update(chunk)
                    f.write(chunk)
                    remaining -= len(chunk)
            if not hmac.compare_digest(h.hexdigest(), item.sha256):
                raise ValueError("Checksum mismatch: the file got damaged on the way")
        except Exception as e:
            target.unlink(missing_ok=True)
            item.state, item.detail = "failed", str(e)
            self._save(item)
            raise ValueError(str(e)) from None
        item.state = "received"
        self._save(item)
        self.log(f"Received {name} from {device[:8]}")
        self._wake.set()
        return item

    def acknowledge(self, item_id, device):
        """The phone has the Opus (or gave up): forget the item and its staging folder."""
        item = self.get(item_id, device)
        if not item:
            return False
        with self._lock:
            self.items.pop(item_id, None)
        shutil.rmtree(self.folder / item_id, ignore_errors=True)
        return True

    # --- worker

    def _worker(self):
        while not self._stop:
            self._wake.wait(30)
            self._wake.clear()
            while not self._stop:
                todo = next((i for i in list(self.items.values()) if i.state == "received"), None)
                if not todo:
                    break
                try:
                    self._process(todo)
                except Exception as e:
                    todo.state, todo.detail = "failed", str(e)
                    self.log(f"Import of {todo.name} failed: {e}", "error")
                self._save(todo)

    def _process(self, item):
        library, opus = self.settings.library, self.settings.opus
        upload = self.folder / item.id / "upload"
        if item.library_path:  # imported before a restart; only the conversion is left
            dst = library / item.library_path
        else:
            existing = self.index.find(flac_md5(upload))
            if existing:
                dst = existing
                item.state, item.detail = "duplicate", "Already in the library"
                self.log(f"{item.name} is already in the library as {existing.relative_to(library)}")
            else:
                staged = upload.with_name(item.name)
                upload.rename(staged)
                track = tags.read_track(staged)
                dst = plan_moves([track], library, self.settings["sort_template"])[0][1]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged), str(dst))
                item.state = "imported"
                self.log(f"Added {dst.relative_to(library)} to the library", "ok")
            item.library_path = dst.relative_to(library).as_posix()
            self._save(item)

        out = opus_path_for(dst, library, opus)
        if not out.exists():
            kind, exe = convert.encoder()
            if not kind:
                raise RuntimeError("No Opus encoder found (install opus-tools or ffmpeg)")
            item.state = "converting"
            self._save(item)
            out.parent.mkdir(parents=True, exist_ok=True)
            ok, err = convert.encode(Context(), kind, exe, dst, out, self.settings["bitrate"], part=".inbox.part")
            if not ok:
                raise RuntimeError(err or "Encoding failed")
            self.log(f"Converted {out.name}", "ok")
        item.opus_path = out.relative_to(opus).as_posix()
        if item.state != "duplicate":
            item.state = "ready"
        (self.folder / item.id / "upload").unlink(missing_ok=True)
