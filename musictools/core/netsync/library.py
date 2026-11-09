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
from ..util import AUDIO_EXTS, export_path_for, safe_filename, trash as util_trash
from .security import state_dir

ACCEPTED = AUDIO_EXTS
MAX_UPLOAD = 2 * 1024 ** 3


def safe_join(root, rel):
    """root/rel, or None if rel tries to leave root (.., absolute paths, symlinks out)."""
    root = Path(root).resolve()
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel or "\x00" in rel:
        return None
    path = (root / rel).resolve()
    return path if path.is_relative_to(root) and path != root else None


def manifest(root):
    """Every file under root (the Opus export, or the library for computers that mirror it) with size and
    mtime. Hidden files and folders and unfinished files are left out."""
    root = Path(root)
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_file() and not p.name.endswith(".part") and not any(x.startswith(".") for x in rel.parts):
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


class _InboxContext(Context):
    """Quiet while importing an upload; errors go to the server log."""
    def __init__(self, log):
        super().__init__()
        self._log, self.error = log, ""

    def log(self, msg, level="info"):
        if level == "error":
            self.error = msg.strip()
            self._log(msg.strip(), "error")


class Inbox:
    """Receives uploads into a staging folder, then imports and converts them one at a time."""

    def __init__(self, settings, log=None, folder=None, trash=None):
        self.settings = settings
        self.trash = trash or util_trash
        self.folder = Path(folder or state_dir() / "inbox")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.log = log or (lambda msg, level="info": None)
        self.items = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        for f in self.folder.glob("*/item.json"):
            try:
                item = Item(**json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
            if item.state == "receiving" or (item.state == "failed" and not item.library_path
                                              and not (f.parent / "upload").exists()):
                shutil.rmtree(f.parent, ignore_errors=True)  # half an upload: the phone sends it again
                continue
            if item.state in ("converting", "imported"):
                item.state = "received"  # interrupted while processing: do it again
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
            raise ValueError("Only music files can be sent to the library")
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
            # A broken upload leaves nothing to show or import: forget it, the phone sends the file again
            with self._lock:
                self.items.pop(item.id, None)
            shutil.rmtree(self.folder / item.id, ignore_errors=True)
            self.log(f"Upload of {name} failed: {e}", "warn")
            raise ValueError(f"Upload failed: {e}") from None
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
        """Same rules as importing from a drive: duplicates are recognised, better copies replace worse
        ones (keeping the library's tags), lossy files go to the Lossy folder."""
        from .. import importer, library as lib_scan
        library, opus = self.settings.library, self.settings.opus
        upload = self.folder / item.id / "upload"
        if item.library_path:  # imported before a restart; only the conversion is left
            dst = library / item.library_path
        else:
            staged = upload.with_name(item.name)
            if upload.exists():
                upload.rename(staged)
            [c] = importer.plan(Context(), [tags.read_track(staged)], lib_scan.scan(library))
            if c.action == "broken":
                raise RuntimeError(f"Can't read the file: {c.reason}")
            if c.action in ("same", "worse", "dupe"):
                dst = c.target.path
                item.state = "duplicate"
                item.detail = "Already in the library" + (f" ({c.reason})" if c.action == "worse" else "")
                self.log(f"{item.name} is already in the library as {dst.relative_to(library)}")
            else:
                c.tick = True
                ctx = _InboxContext(self.log)
                importer.apply(ctx, [c], self.settings, self.trash)
                if c.dest is None:
                    raise RuntimeError(ctx.error or "Import failed")
                dst = c.dest
                item.state = "imported"
                item.detail = {"upgrade": f"Replaced a worse copy ({c.reason})", "lossy": "Added to the Lossy folder",
                               "version": "Added as another version"}.get(c.action, "")
                self.log(f"Added {dst.relative_to(library)} to the library", "ok")
            staged.unlink(missing_ok=True)
            item.library_path = dst.relative_to(library).as_posix()
            self._save(item)

        out = export_path_for(dst, library, opus)
        if not out.exists() and dst.suffix.lower() != ".flac":
            convert.copy_lossy(Context(), library, opus)
        elif not out.exists():
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
