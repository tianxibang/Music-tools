"""M3U8 playlists. They live in <library>/Playlists (pointing at the FLACs) and are exported
to <opus>/Playlists pointing at the Opus files, which then sync to the phone like any other file."""

import os
from pathlib import Path

from .util import SETTINGS, export_path_for, safe_filename

def folder(library):
    return Path(library) / SETTINGS["playlists_folder"]


def list_playlists(library):
    d = folder(library)
    return sorted((p for p in d.glob("*.m3u*") if p.is_file()), key=lambda p: p.stem.lower()) if d.is_dir() else []


def load(path):
    path = Path(path)
    entries = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line.replace("\\", "/"))
        entries.append(p if p.is_absolute() else (path.parent / p).resolve())
    return entries


def save(path, tracks, info=None):
    """tracks: list of Paths. info: optional {path: (seconds, 'Artist - Title')} for #EXTINF lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for t in tracks:
        if info and t in info:
            secs, label = info[t]
            lines.append(f"#EXTINF:{round(secs)},{label}")
        lines.append(Path(os.path.relpath(t, path.parent)).as_posix())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def new_path(library, name):
    return folder(library) / f"{safe_filename(name, 'Playlist')}.m3u8"


def remap(library, mapping):
    """After files were moved/renamed: rewrite playlist entries. mapping: {old Path: new Path}."""
    mapping = {Path(k).resolve(): Path(v) for k, v in mapping.items()}
    changed = 0
    for pl in list_playlists(library):
        entries = load(pl)
        new = [mapping.get(e, e) for e in entries]
        if new != entries:
            save(pl, new)
            changed += 1
    return changed


def drop(library, paths):
    """Take deleted files out of every playlist. Returns how many playlists changed."""
    gone = {Path(p).resolve() for p in paths}
    changed = 0
    for pl in list_playlists(library):
        entries = load(pl)
        kept = [e for e in entries if e not in gone]
        if len(kept) != len(entries):
            save(pl, kept)
            changed += 1
    return changed


def export(ctx, library, opus):
    """Write Opus versions of every playlist into <opus>/Playlists."""
    library, opus = Path(library), Path(opus)
    out_dir = opus / SETTINGS["playlists_folder"]
    pls = list_playlists(library)
    written = set()
    for pl in pls:
        tracks, missing = [], 0
        for e in load(pl):
            try:
                o = export_path_for(e, library, opus)
            except ValueError:  # outside the library
                o = e
            if o.exists():
                tracks.append(o)
            else:
                missing += 1
        target = out_dir / f"{pl.stem}.m3u8"
        save(target, tracks)
        written.add(target)
        ctx.log(f"  {pl.stem}: {len(tracks)} track(s)" + (f", {missing} not converted yet" if missing else ""),
                "ok" if not missing else "warn")
    if out_dir.is_dir():
        for old in out_dir.glob("*.m3u8"):
            if old not in written:
                old.unlink()
                ctx.log(f"  removed old export {old.name}", "warn")
    ctx.log(f"Exported {len(pls)} playlist(s) to {out_dir}." if pls else "No playlists to export.", "ok")
    return len(pls)
