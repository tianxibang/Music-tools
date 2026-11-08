"""Scanning folders into Track lists, and summary statistics for the folder info view."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .tags import read_track
from .util import AUDIO_EXTS, audio_files


def scan(folder, ctx=None, exts=AUDIO_EXTS):
    files = audio_files(folder, exts)
    tracks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, t in enumerate(pool.map(read_track, files), 1):
            tracks.append(t)
            if ctx:
                if i % 25 == 0 or i == len(files):
                    ctx.progress(i, len(files), f"Reading tags {i} / {len(files)}")
                ctx.check()
    return tracks


def folder_stats(ctx, folder):
    folder = Path(folder)
    tracks = scan(folder, ctx)
    other_size = 0
    other_count = 0
    for p in folder.rglob("*"):
        if p.is_file() and p.suffix.lower() not in AUDIO_EXTS:
            other_size += p.stat().st_size
            other_count += 1

    def top(counter, n=8):
        return counter.most_common(n)

    decades = Counter()
    for t in tracks:
        if t.year:
            decades[f"{t.year[:3]}0s"] += 1
    quality = Counter()
    for t in tracks:
        if t.bits:
            quality[f"{t.bits}-bit / {t.sample_rate / 1000:g} kHz"] += 1
        elif t.bitrate:
            quality[f"~{round(t.bitrate, -1)} kbps {t.codec}"] += 1
    return {
        "tracks": len(tracks),
        "size": sum(t.size for t in tracks),
        "duration": sum(t.duration for t in tracks),
        "artists": len({t.artist.lower() for t in tracks if t.artist}),
        "albums": len({(t.tag('albumartist') or t.artist, t.album) for t in tracks if t.album}),
        "avg_bitrate": round(sum(t.bitrate for t in tracks) / len(tracks)) if tracks else 0,
        "formats": top(Counter(t.codec.upper() for t in tracks)),
        "top_artists": top(Counter(t.artist for t in tracks if t.artist)),
        "genres": top(Counter(t.tag("genre") for t in tracks if t.tag("genre"))),
        "decades": sorted(decades.items()),
        "quality": top(quality),
        "no_art": sum(1 for t in tracks if not t.has_art),
        "other_files": other_count,
        "other_size": other_size,
    }
