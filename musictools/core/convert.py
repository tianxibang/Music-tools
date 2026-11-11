"""FLAC -> Opus conversion (was flac2opus.fish)."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import lyrics, tags
from .util import AUDIO_EXTS, LOSSLESS_EXTS, audio_files, export_path_for, find_tool, opus_path_for

# Lossy files in the library (MP3, AAC, …: e.g. imported in the Lossy folder) aren't converted again,
# which would only lose more quality: they're copied into the export as they are.
LOSSY_EXTS = AUDIO_EXTS - LOSSLESS_EXTS


def encoder():
    if exe := find_tool("opusenc"):
        return "opusenc", exe
    if exe := find_tool("ffmpeg"):
        return "ffmpeg", exe
    return None, None


def plan(library, opus):
    """(flac, opus) pairs for every FLAC in the library; subfolders are mirrored."""
    library, opus = Path(library), Path(opus)
    return [(f, opus_path_for(f, library, opus)) for f in audio_files(library, {".flac"})]


def lossy_plan(library, opus):
    """(source, copy) pairs for the lossy files in the library; the copy keeps its format."""
    library, opus = Path(library), Path(opus)
    return [(f, export_path_for(f, library, opus)) for f in audio_files(library, LOSSY_EXTS)]


def copy_lossy(ctx, library, opus):
    """Copy new or changed lossy files into the export. Returns the copies made."""
    import shutil
    done = []
    for src, dst in lossy_plan(library, opus):
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            continue
        ctx.check()
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        done.append(dst)
    if done:
        ctx.log(f"Copied {len(done)} lossy file(s) into the export as they are.", "ok")
    return done


def status(library, opus):
    pairs = plan(library, opus)
    lossy = lossy_plan(library, opus)
    expected = {o for _, o in pairs} | {c for _, c in lossy}
    orphans = [p for p in audio_files(opus, {".opus"}) if p not in expected]
    pending = [f for f, o in pairs if not o.exists()] + [f for f, c in lossy if not c.exists()]
    return {"flac": len(pairs), "pending": pending, "orphans": orphans}


def convert(ctx, library, opus, bitrate=128, jobs=4, dry=False):
    """Convert every FLAC without an Opus counterpart. Returns the list of new Opus files."""
    library, opus = Path(library), Path(opus)
    kind, exe = encoder()
    if not kind:
        ctx.log("No encoder found. Install opus-tools (opusenc) or ffmpeg.", "error")
        return []
    if not library.is_dir():
        ctx.log(f"Library folder '{library}' not found.", "error")
        return []
    if not dry:
        copy_lossy(ctx, library, opus)

    # Leftovers from an interrupted run would otherwise look like finished files
    if opus.is_dir() and not dry:
        for part in opus.rglob("*.opus.part"):
            part.unlink(missing_ok=True)

    pairs = plan(library, opus)
    todo = [(f, o) for f, o in pairs if not o.exists()]
    skipped = len(pairs) - len(todo)
    ctx.log(f"{len(todo)} to convert, {skipped} already done.")
    if not todo:
        if not dry:
            copy_lyrics(ctx, library, opus)
        return []
    if dry:
        for f, _ in todo:
            ctx.log(f"  would convert: {f.name}")
        return []

    for _, o in todo:
        o.parent.mkdir(parents=True, exist_ok=True)
    ctx.log(f"Encoding at {bitrate} kbps with {jobs} parallel jobs using {kind}…")
    start = time.time()

    def one(f, out):
        if ctx.cancelled:
            return False
        ok, err = encode(ctx, kind, exe, f, out, bitrate)
        if ok:
            ctx.log(f"  ok    {out.name}", "ok")
        elif not ctx.cancelled:
            ctx.log(f"  FAIL  {f.name}" + (f" — {err}" if err else ""), "error")
        return ok

    done, failed = [], []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(one, f, o): (f, o) for f, o in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            (done if fut.result() else failed).append(futures[fut])
            ctx.progress(i, len(todo), f"Converting {i} / {len(todo)}")
    ctx.check()

    ctx.log(f"Done in {round(time.time() - start)} s: {len(done)} converted, {skipped} skipped, "
            f"{len(failed)} failed.", "ok" if not failed else "warn")
    copy_lyrics(ctx, library, opus)
    return [o for _, o in done]


def copy_lyrics(ctx, library, opus):
    """.lrc files next to library songs go next to their copies in the export as well."""
    if n := lyrics.copy_sidecars(library, opus, plan(library, opus) + lossy_plan(library, opus)):
        ctx.log(f"Copied {n} lyrics file(s) (.lrc) into the export.", "ok")


def encode(ctx, kind, exe, f, out, bitrate=128, part=".part"):
    """Encode one file to Opus. Returns (ok, last error line)."""
    # Write to a temp file first and only rename once the encoder succeeds, so a cancel or
    # a bad file never leaves a broken .opus that would be skipped next time.
    tmp = out.with_name(out.name + part)
    if kind == "opusenc":
        cmd = [exe, "--quiet", "--bitrate", str(bitrate), str(f), str(tmp)]
    else:
        cmd = [exe, "-nostdin", "-loglevel", "error", "-y", "-i", str(f), "-map", "0:a",
               "-map_metadata", "0", "-c:a", "libopus", "-b:a", f"{bitrate}k", "-f", "ogg", str(tmp)]
    code, _, err = ctx.popen(cmd)
    if code == 0 and not ctx.cancelled:
        os.replace(tmp, out)
        if kind == "ffmpeg":
            # ffmpeg drops embedded cover art in Ogg; copy it over ourselves
            try:
                if pic := tags.get_picture(f):
                    tags.set_picture(out, *pic)
            except Exception:
                pass
        return True, ""
    tmp.unlink(missing_ok=True)
    return False, err.strip().splitlines()[-1] if err.strip() else ""


def find_orphans(ctx, library, opus):
    orphans = status(library, opus)["orphans"]
    if not orphans:
        ctx.log("Nothing to prune — every Opus file has a matching FLAC.", "ok")
    else:
        ctx.log(f"{len(orphans)} .opus files have no matching .flac:")
        for o in orphans:
            ctx.log(f"  {o.relative_to(opus)}")
    return orphans
