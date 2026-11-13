"""Follower mode: this computer mirrors the library of a main PC, and can send changes back to it.

The main PC holds the truth. A follower
- downloads the main PC's FLAC library and/or Opus export (what it gets is a setting) and removes what the main
  PC removed, but only files a sync put there itself;
- keeps an outbox: new music dropped in its "New music" folder, and tag edits made in its tag editor. Sending
  them (by hand, or automatically) hands them to the main PC, which files them like any other import and
  passes them on to every device.
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path

from ..tags import FIELD_LABELS
from ..util import AUDIO_EXTS, human_size as _size, remove_empty_dirs, trash as util_trash
from .client import Client, SyncError
from .security import Identity, state_dir

ROOTS = {"flac": ["library"], "opus": ["opus"], "both": ["library", "opus"]}
_NAMES = {"library": "Library", "opus": "Opus export"}


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
_NAMES = {"library": "Library", "opus": "Opus export"}


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


class Follower:
    def __init__(self, settings, path=None, trash=None):
        self.settings = settings
        self.path = Path(path or state_dir() / "follow.json")
        self.trash = trash or util_trash
        self._lock = threading.Lock()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("main", None)
        self.state.setdefault("synced", {"library": {}, "opus": {}})
        self.state.setdefault("edits", [])
        self.state.setdefault("errors", {})  # new music that couldn't be sent: {path: error}
        self.state.setdefault("last", {})

    def save(self):
        with self._lock:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    # --- pairing

    @property
    def main(self):
        return self.state["main"]

    def pair(self, host, port, code, short_fp=""):
        me = Identity()
        c = Client(host, port, me.id, name=me.name, kind="pc")
        res = c.pair(code)
        if short_fp and not c.fingerprint.startswith(short_fp):
            raise SyncError(0, "This PC doesn't match the one announced on the network")
        self.state["main"] = {"id": res["id"], "name": res["name"], "hosts": [host], "port": port,
                              "fingerprint": c.fingerprint, "token": c.token}
        self.state["synced"] = {"library": {}, "opus": {}}
        self.save()
        return self.main

    def unpair(self):
        if self.main:
            try:
                self.client().unpair()
            except (OSError, SyncError):
                pass
        self.state["main"] = None
        self.save()

    def client(self):
        """A connected client: last known address first, then the network announcement for the main PC."""
        m = self.main
        if not m:
            raise SyncError(0, "Not paired with a main PC yet")
        me = Identity()
        last = None
        for host in m["hosts"]:
            c = Client(host, m["port"], me.id, kind="pc", fingerprint=m["fingerprint"], token=m["token"])
            try:
                c.info()
                return c
            except SyncError:
                raise  # it answered but refused, e.g. a different certificate: don't look elsewhere
            except OSError as e:
                last = e
        try:
            from .discovery import browse
            for pc in browse(3):
                if pc["id"] == m["id"]:
                    c = Client(pc["host"], pc["port"], me.id, kind="pc", fingerprint=m["fingerprint"], token=m["token"])
                    c.info()
                    m["hosts"] = [pc["host"]] + [h for h in m["hosts"] if h != pc["host"]][:4]
                    m["port"] = pc["port"]
                    self.save()
                    return c
        except SyncError:
            raise
        except Exception:
            pass
        raise OSError(f"Couldn't reach {m['name']}. Is Music Tools running there with sync turned on?"
                      + (f" ({last})" if last else ""))

    # --- mirror the main PC

    def _local(self, root):
        return self.settings.library if root == "library" else self.settings.opus

    def protected(self):
        """Files with tag edits not sent yet (and their .lrc files): a sync must not overwrite them."""
        out = {(e["root"], e["path"]) for e in self.state["edits"]}
        out |= {(e["root"], Path(e["path"]).with_suffix(".lrc").as_posix()) for e in self.state["edits"]
                if "lyrics" in e["changes"]}
        return out

    def plan(self, ctx, client):
        """What a sync would change: {root: (downloads [(file info, dest, why)], removals [Path])}."""
        out = {}
        guarded = self.protected()
        for root in ROOTS[self.settings["follow_get"]]:
            files = client.manifest(root)
            local = Path(self._local(root))
            if not files:
                ctx.log(f"The main PC's {_NAMES[root].lower()} is empty: "
                        f"not touching this computer's copy.", "warn")
                continue
            synced = self.state["synced"][root]
            remote = {f["path"]: f for f in files}
            downloads = []
            for f in files:
                if (root, f["path"]) in guarded:
                    continue
                dest = local / f["path"]
                why = "new"
                if dest.exists():
                    st = dest.stat()
                    if st.st_size == f["size"] and (int(st.st_mtime) == f["mtime"] or f["path"] not in synced):
                        # Same size and the main PC's time, or a copy that was here already (e.g. from a
                        # USB stick): no need to download it again
                        synced[f["path"]] = [f["size"], f["mtime"]]
                        continue
                    if f["path"] not in synced:
                        why = f"the copy here differs ({_size(st.st_size)} here, {_size(f['size'])} there)"
                    elif st.st_size != f["size"]:
                        why = f"changed on the main PC ({_size(st.st_size)} → {_size(f['size'])})"
                    else:
                        why = (f"changed on the main PC (modified {_when(f['mtime'])}, "
                               f"the copy here {_when(st.st_mtime)})")
                downloads.append((f, dest, why))
            removals = [local / rel for rel in synced if rel not in remote and (root, rel) not in guarded
                        and (local / rel).exists()]
            out[root] = (downloads, removals)
            new = sum(why == "new" for _, _, why in downloads)
            ctx.log(f"{_NAMES[root]}: {len(files)} files on the main PC, {len(downloads)} to download "
                    f"({new} new, {len(downloads) - new} changed), {len(removals)} to remove.")
        return out

    def sync(self, ctx, send=None):
        """Optionally send the outbox first, then mirror. Returns a summary dict."""
        client = self.client()
        ctx.log(f"Connected to {self.main['name']}.", "ok")
        sent = {}
        if send is None:
            send = self.settings["follow_send_auto"]
        if send:
            sent = self.send(ctx, client)
        plans = self.plan(ctx, client)
        got = removed = 0
        for root, (downloads, removals) in plans.items():
            synced = self.state["synced"][root]
            if len(removals) > self.settings["follow_big_delete"] and len(removals) > len(synced) / 10:
                if not ctx.ask("Remove songs?", f"The main PC no longer has {len(removals)} files that are on this "
                                               f"computer. Remove them here too (they go to the trash)?"):
                    ctx.log(f"Kept {len(removals)} files the main PC no longer has.", "warn")
                    removals = []
            for i, (f, dest, why) in enumerate(downloads, 1):
                ctx.check()
                ctx.progress(i, len(downloads), f"Downloading {i} / {len(downloads)}: {Path(f['path']).name}")
                ctx.log(f"  ↓ {f['path']} ({why})")
                try:
                    client.download(f["path"], dest, f["size"], f["mtime"], root=root)
                    synced[f["path"]] = [f["size"], f["mtime"]]
                    got += 1
                except (OSError, SyncError) as e:
                    ctx.log(f"  couldn't download {f['path']}: {e}", "error")
                if i % 25 == 0:
                    self.save()
            for p in removals:
                ctx.check()
                rel = p.relative_to(self._local(root)).as_posix()
                if self.trash(p):
                    synced.pop(rel, None)
                    removed += 1
                    ctx.log(f"  removed {rel}", "warn")
            if removals:
                remove_empty_dirs(self._local(root))
        self.state["last"] = {"time": time.time(), "got": got, "removed": removed, **sent}
        self.save()
        ctx.log(f"Sync finished: {got} downloaded, {removed} removed"
                + (f", {sent.get('sent', 0)} sent" if sent else "") + ".", "ok")
        return self.state["last"]

    # --- the outbox

    def new_music(self):
        folder = Path(self.settings["follow_new_music"])
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                      and not any(x.startswith(".") for x in p.relative_to(folder).parts))

    def record_tag_edit(self, path, old_tags, changes):
        """Remember a tag edit made here. Edits of the same file merge, keeping the first old value."""
        path = Path(path)
        for root in ("library", "opus"):
            base = Path(self._local(root))
            if path.is_relative_to(base):
                rel = path.relative_to(base).as_posix()
                break
        else:
            return
        with self._lock:
            edit = next((e for e in self.state["edits"] if e["root"] == root and e["path"] == rel), None)
            if edit is None:
                edit = {"id": uuid.uuid4().hex, "root": root, "path": rel, "changes": {}, "conflicts": {},
                        "time": time.time()}
                self.state["edits"].append(edit)
            for field, new in changes.items():
                old = edit["changes"].get(field, [old_tags.get(field, ""), None])[0]
                if old == new:
                    edit["changes"].pop(field, None)  # changed back: nothing to send
                    edit["conflicts"].pop(field, None)
                else:
                    edit["changes"][field] = [old, new]
            if not edit["changes"]:
                self.state["edits"].remove(edit)
        self.save()

    def discard_edits(self, ids, fields=None):
        """Drop edits (or some of their fields); the next sync brings back the main PC's version."""
        for e in list(self.state["edits"]):
            if e["id"] in ids:
                for f in list(fields or e["changes"]):
                    e["changes"].pop(f, None)
                    e["conflicts"].pop(f, None)
                if not e["changes"]:
                    self.state["edits"].remove(e)
        self.save()

    def send(self, ctx, client=None, files=None, edit_ids=None, force=()):
        """Send new music and tag edits (all, or the chosen ones). force: edit ids whose conflicts we overrule."""
        client = client or self.client()
        files = self.new_music() if files is None else files
        edits = [e for e in self.state["edits"] if edit_ids is None or e["id"] in edit_ids]
        if edit_ids is None:
            edits = [e for e in edits if not e["conflicts"]]  # automatic sends leave conflicts to you
        sent = conflicts = 0
        for e in edits:
            ctx.check()
            try:
                res = client.edit_tags(e["path"], {k: tuple(v) for k, v in e["changes"].items()}, e["root"],
                                       force=e["id"] in force)
            except SyncError as err:
                ctx.log(f"  tags of {e['path']}: {err}", "error")
                continue
            for f in res["applied"]:
                e["changes"].pop(f, None)
                e["conflicts"].pop(f, None)
            e["conflicts"].update(res["conflicts"])
            # neither applied nor in conflict: the main PC doesn't know the field (an older Music Tools there)
            e["ignored"] = [f for f in e["changes"] if f not in res["conflicts"]]
            if e["ignored"]:
                names = ", ".join(FIELD_LABELS.get(f, f).lower() for f in e["ignored"])
                ctx.log(f"  {e['path']}: the main PC didn't take the {names} change. Update Music Tools there "
                        f"and send it again.", "warn")
            if res["conflicts"]:
                conflicts += 1
                ctx.log(f"  {e['path']}: changed on the main PC too ({', '.join(res['conflicts'])})", "warn")
            if not e["changes"]:
                self.state["edits"].remove(e)
                sent += 1
            self.save()
        for i, p in enumerate(files, 1):
            ctx.check()
            ctx.progress(i, len(files), f"Sending {p.name}")
            try:
                item = client.upload(p)
                end = time.time() + 600
                while item["state"] not in ("ready", "duplicate", "failed") and time.time() < end:
                    time.sleep(1)
                    item = client.inbox(item["id"])
                if item["state"] == "failed":
                    raise SyncError(0, item["detail"] or "The main PC couldn't import it")
                if item["state"] not in ("ready", "duplicate"):
                    raise SyncError(0, "The main PC is still working on it; try again later")
                client.acknowledge(item["id"])
                self.trash(p)  # it's in the main library now and comes back with the next sync
                self.state["errors"].pop(str(p), None)
                sent += 1
                ctx.log(f"  sent {p.name}" + (" (already there)" if item["state"] == "duplicate" else ""), "ok")
            except (OSError, SyncError) as err:
                self.state["errors"][str(p)] = str(err)
                ctx.log(f"  couldn't send {p.name}: {err}", "error")
            self.save()
        if files:
            remove_empty_dirs(Path(self.settings["follow_new_music"]))
        return {"sent": sent, "conflicts": conflicts}
