"""Merging music from another drive or folder into the library.

Every file on the source is compared with the library:
  new       a song the library doesn't have (lossless)          -> imported with the sort template
  lossy     a new song, but MP3/AAC/…                           -> imported into <library>/Lossy, kept as is
  upgrade   a better copy of a song the library has             -> replaces it, keeping the library's tags
  version   another version (demo, live, …) of a song it has    -> imported as its own song if ticked
  same      identical audio already in the library              -> skipped
  worse     the library has it in equal or better quality       -> skipped
  dupe      a worse copy of something else on the same source   -> skipped
  broken    unreadable                                          -> skipped
The source is only ever read. Replaced library files go to the trash with their export copies.
"""

import hashlib
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import convert, health, library as lib_scan, playlists, tags
from .organize import plan_moves
from .util import LOSSLESS_EXTS, NO_WINDOW, export_path_for, find_tool

LOSSY_FOLDER = "Lossy"

ACTIONS = [
    ("new", "New songs"),
    ("upgrade", "Better copies of songs you have"),
    ("lossy", "New lossy songs (MP3, AAC, …)"),
    ("version", "Other versions of songs you have"),
    ("same", "Already in your library"),
    ("worse", "You have these in equal or better quality"),
    ("dupe", "Duplicates on the drive"),
    ("broken", "Can't be read"),
]
IMPORTABLE = {"new", "upgrade", "lossy", "version"}


@dataclass
class Candidate:
    src: object                     # Track on the source
    action: str
    reason: str = ""
    target: object = None           # the library Track it was compared with (replaced, for an upgrade)
    tick: bool = False              # suggested for import
    warnings: list = field(default_factory=list)


def is_lossless(t):
    return t.path.suffix.lower() in LOSSLESS_EXTS


def audio_quality(t):
    """Only the sound: lossless first, then bit depth and sample rate (or bitrate for lossy files)."""
    return (is_lossless(t), t.bits if is_lossless(t) else 0, t.sample_rate if is_lossless(t) else t.bitrate)


def describe(t):
    kind = t.path.suffix[1:].upper()
    if is_lossless(t):
        return f"{kind} {t.bits or '?'}-bit / {t.sample_rate / 1000:g} kHz" if t.sample_rate else kind
    return f"{kind} {t.bitrate} kbps" if t.bitrate else kind


def _key(t):
    return (health._norm(t.artist), health._norm(t.tag("title"))) if t.artist and t.tag("title") else None


def scan(ctx, folder):
    ctx.log(f"Reading {folder}…")
    tracks = lib_scan.scan(folder, ctx)
    ctx.log(f"{len(tracks)} audio files found.")
    return tracks


def plan(ctx, source, library_tracks, check_fakes=False):
    """Decide what to do with every source track. Nothing is changed."""
    from .netsync.library import flac_md5

    lib_md5 = {}
    lib_by_key = {}
    for t in library_tracks:
        if t.error:
            continue
        if t.path.suffix.lower() == ".flac" and (md5 := flac_md5(t.path)):
            lib_md5[md5] = t
        if k := _key(t):
            lib_by_key.setdefault(k, []).append(t)

    # The same song more than once on the source: only its best copy is considered
    seen_md5 = {}
    out = []
    readable = []
    for t in source:
        if t.error:
            out.append(Candidate(t, "broken", t.error))
            continue
        md5 = flac_md5(t.path) if t.path.suffix.lower() == ".flac" else ""
        if md5 and md5 in seen_md5:
            out.append(Candidate(t, "dupe", f"Same audio as {seen_md5[md5].path.name} on the drive", seen_md5[md5]))
            continue
        if md5:
            seen_md5[md5] = t
        readable.append((t, md5))
    clusters = {}  # song key -> lists of copies of the same recording on the source
    for t, _ in readable:
        if k := _key(t):
            groups = clusters.setdefault(k, [])
            home = next((g for g in groups if health._same_recording(g[0], t)), None)
            if home:
                home.append(t)
            else:
                groups.append([t])
    best_copy = {id(t): max(g, key=health.quality_key) for gs in clusters.values() for g in gs for t in g}

    for i, (t, md5) in enumerate(readable, 1):
        ctx.check()
        if (rival := best_copy.get(id(t))) is not None and rival is not t:
            out.append(Candidate(t, "dupe", f"A better copy is also on the drive: {rival.path.name}", rival))
            continue
        if md5 and md5 in lib_md5:
            out.append(Candidate(t, "same", "Identical audio", lib_md5[md5]))
            continue
        cands = lib_by_key.get(_key(t), []) if _key(t) else []
        same = [c for c in cands if health._same_recording(c, t)]
        if same:
            best = max(same, key=health.quality_key)
            if audio_quality(t) > audio_quality(best):
                out.append(Candidate(t, "upgrade", f"{describe(best)} → {describe(t)}", best, tick=True))
            else:
                out.append(Candidate(t, "worse", f"You have {describe(best)}", best))
        elif cands:
            diff = health._versions(t.tag("title")) ^ health._versions(cands[0].tag("title"))
            what = ", ".join(sorted(diff)) if diff else "different length"
            out.append(Candidate(t, "version", f"Other version ({what}) of {cands[0].path.name}", cands[0]))
        else:
            c = Candidate(t, "new" if is_lossless(t) else "lossy", describe(t), tick=True)
            if not _key(t):
                c.warnings.append("No artist/title tags: named after the file")
            out.append(c)
        ctx.progress(i, len(readable), f"Comparing {i} / {len(readable)}")

    if check_fakes and (ffmpeg := find_tool("ffmpeg")):
        ups = [c for c in out if c.action in ("upgrade", "new") and is_lossless(c.src)]
        if ups:
            ctx.log(f"Checking {len(ups)} lossless file(s) for signs of an MP3 source…")
        for i, c in enumerate(ups, 1):
            ctx.check()
            if res := health.spectrum_cutoff(ctx, c.src.path, ffmpeg, c.src.duration):
                c.warnings.append(f"Looks like it was made from a lossy file ({health.lossy_label(res[0])})")
                if c.action == "upgrade":
                    c.tick = False  # a fake must not replace a real lossless file
            ctx.progress(i, len(ups), f"Checking {i} / {len(ups)}")

    order = {a: i for i, (a, _) in enumerate(ACTIONS)}
    out.sort(key=lambda c: (order[c.action], c.src.artist.lower(), c.src.title.lower()))
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _copy_verified(src, dst):
    shutil.copy2(src, dst)
    if _sha256(src) != _sha256(dst):
        dst.unlink(missing_ok=True)
        raise OSError("the copy doesn't match the original (drive read error?)")


def _to_flac(path):
    """WAV, AIFF, WavPack, APE → FLAC (lossless, keeps tags where ffmpeg can)."""
    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is needed to convert it to FLAC")
    out = path.with_suffix(".flac")
    r = subprocess.run([ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-map", "0:a",
                        "-map_metadata", "0", "-c:a", "flac", str(out)], capture_output=True, text=True,
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "conversion failed")
    path.unlink()
    return out


def _keep_library_tags(new_file, old):
    """After an upgrade: the library's tags and cover win; the new file only fills gaps."""
    changes = {k: v for k, v in old.tags.items() if v and k in tags.FIELDS}
    if changes:
        tags.write_tags(new_file, changes)
    if old.has_art and (pic := tags.get_picture(old.path)):
        tags.set_picture(new_file, *pic)


def apply(ctx, candidates, settings, trash):
    """Import the ticked candidates. trash(path) -> bool moves a replaced file to the trash."""
    library, opus = settings.library, settings.opus
    template = settings["sort_template"]
    chosen = [c for c in candidates if c.tick and c.action in IMPORTABLE]
    staging = library / ".import-tmp"
    staging.mkdir(parents=True, exist_ok=True)
    moved, counts, failed = {}, {}, 0
    try:
        for i, c in enumerate(chosen, 1):
            ctx.check()
            ctx.progress(i, len(chosen), f"Importing {i} / {len(chosen)}")
            src = c.src.path
            box = staging / uuid.uuid4().hex
            box.mkdir()
            tmp = box / src.name  # keeps the original name, used when the tags can't name the file
            try:
                _copy_verified(src, tmp)
                if tmp.suffix.lower() in LOSSLESS_EXTS and tmp.suffix.lower() != ".flac":
                    tmp = _to_flac(tmp)
                lossy = tmp.suffix.lower() not in LOSSLESS_EXTS
                if c.action == "upgrade":
                    old = c.target.path
                    _keep_library_tags(tmp, c.target)
                    if old.suffix.lower() == tmp.suffix.lower():
                        dest = old
                    elif lossy:
                        dest = old.with_suffix(tmp.suffix.lower())
                    else:  # lossy → lossless: it leaves the Lossy folder for the main library
                        dest = plan_moves([tags.read_track(tmp)], library, template)[0][1]
                    copy = export_path_for(old, library, opus)
                    if not trash(old):
                        raise OSError(f"couldn't move the old file to the trash: {old}")
                    if copy.exists():
                        trash(copy)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tmp), str(dest))
                    if dest != old:
                        moved[old] = dest
                    ctx.log(f"  upgraded  {dest.name}  ({c.reason})", "ok")
                else:
                    root = library / LOSSY_FOLDER if lossy else library
                    dest = plan_moves([tags.read_track(tmp)], root, template)[0][1]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tmp), str(dest))
                    ctx.log(f"  added     {dest.relative_to(library)}", "ok")
                counts[c.action] = counts.get(c.action, 0) + 1
            except Exception as e:
                failed += 1
                ctx.log(f"  FAIL      {src.name}: {e}", "error")
            finally:
                shutil.rmtree(box, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    if moved:
        playlists.remap(library, moved)
    if counts:
        ctx.log("Converting the new songs for the phone…")
        convert.convert(ctx, library, opus, settings["bitrate"], settings["jobs"])
        playlists.export(ctx, library, opus)
    labels = {"new": "new", "upgrade": "upgraded", "lossy": "lossy", "version": "other versions"}
    summary = ", ".join(f"{n} {labels[a]}" for a, n in counts.items()) or "nothing"
    ctx.log(f"Import finished: {summary}" + (f", {failed} failed." if failed else "."), "warn" if failed else "ok")
    return counts, failed
