"""FLAC -> Opus conversion (was flac2opus.fish)."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import tags
from .util import audio_files, find_tool, opus_path_for


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


def status(library, opus):
    pairs = plan(library, opus)
    expected = {o for _, o in pairs}
    orphans = [p for p in audio_files(opus, {".opus"}) if p not in expected]
    pending = [f for f, o in pairs if not o.exists()]
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

    # Leftovers from an interrupted run would otherwise look like finished files
    if opus.is_dir() and not dry:
        for part in opus.rglob("*.opus.part"):
            part.unlink(missing_ok=True)

    pairs = plan(library, opus)
    todo = [(f, o) for f, o in pairs if not o.exists()]
    skipped = len(pairs) - len(todo)
    ctx.log(f"{len(todo)} to convert, {skipped} already done.")
    if not todo:
        return []
    if dry:
        for f, _ in todo:
            ctx.log(f"  would convert: {f.name}")
        return []

    for _, o in todo:
        o.parent.mkdir(parents=True, exist_ok=True)
    ctx.log(f"Encoding at {bitrate} kbps with {jobs} parallel jobs using {kind}…")
    start = time.time()

    def encode(f, out):
        if ctx.cancelled:
            return False
        # Write to .part first and only rename once the encoder succeeds, so a cancel or
        # a bad file never leaves a broken .opus that would be skipped next time.
        tmp = out.with_name(out.name + ".part")
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
            ctx.log(f"  ok    {out.name}", "ok")
            return True
        tmp.unlink(missing_ok=True)
        if not ctx.cancelled:
            ctx.log(f"  FAIL  {f.name}" + (f" — {err.strip().splitlines()[-1]}" if err.strip() else ""), "error")
        return False

    done, failed = [], []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(encode, f, o): (f, o) for f, o in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            (done if fut.result() else failed).append(futures[fut])
            ctx.progress(i, len(todo), f"Converting {i} / {len(todo)}")
    ctx.check()

    ctx.log(f"Done in {round(time.time() - start)} s: {len(done)} converted, {skipped} skipped, "
            f"{len(failed)} failed.", "ok" if not failed else "warn")
    return [o for _, o in done]


def find_orphans(ctx, library, opus):
    orphans = status(library, opus)["orphans"]
    if not orphans:
        ctx.log("Nothing to prune — every Opus file has a matching FLAC.", "ok")
    else:
        ctx.log(f"{len(orphans)} .opus files have no matching .flac:")
        for o in orphans:
            ctx.log(f"  {o.relative_to(opus)}")
    return orphans
