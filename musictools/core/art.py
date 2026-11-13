"""Cover art: fetching missing covers from MusicBrainz / Cover Art Archive and copying art to Opus."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import tags
from .util import APP_NAME, APP_VERSION, SETTINGS, converts_to_opus, opus_path_for

USER_AGENT = f"{APP_NAME.replace(' ', '')}/{APP_VERSION} (personal music library tool)"
_last_mb_request = 0.0


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _mb(path, **params):
    """MusicBrainz asks clients to stay under one request per second."""
    global _last_mb_request
    wait = 1.1 - (time.time() - _last_mb_request)
    if wait > 0:
        time.sleep(wait)
    _last_mb_request = time.time()
    url = f"https://musicbrainz.org/ws/2/{path}?{urllib.parse.urlencode({**params, 'fmt': 'json'})}"
    return json.loads(_get(url))


def _q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fetch_cover(artist, album, title=""):
    """Return (bytes, mime) for the best front cover found, or None."""
    queries = []
    if album:
        queries.append(f"release:{_q(album)} AND artist:{_q(artist)}")
    if title:
        queries.append(f"recording:{_q(title)} AND artist:{_q(artist)}")
    for query in queries:
        try:
            if query.startswith("release:"):
                releases = _mb("release/", query=query, limit=5).get("releases", [])
            else:
                recs = _mb("recording/", query=query, limit=3).get("recordings", [])
                releases = [r for rec in recs for r in rec.get("releases", [])][:5]
        except (urllib.error.URLError, OSError, ValueError):
            continue
        tried_groups = set()
        for rel in releases:
            size = SETTINGS["cover_size"]
            urls = [f"https://coverartarchive.org/release/{rel['id']}/front-{size}"]
            rg = (rel.get("release-group") or {}).get("id")
            if rg and rg not in tried_groups:
                tried_groups.add(rg)
                urls.append(f"https://coverartarchive.org/release-group/{rg}/front-{size}")
            for url in urls:
                try:
                    data = _get(url, timeout=30)
                except (urllib.error.URLError, OSError):
                    continue
                if data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n":
                    return data, tags.image_mime(data)
    return None


def fetch_missing(ctx, tracks, library, opus):
    """Fetch covers for tracks without art; one lookup per album. Also embeds into the Opus copy."""
    missing = [t for t in tracks if not t.has_art and t.codec in ("flac", "opus", "ogg", "mp3", "m4a")]
    if not missing:
        ctx.log("Every track already has cover art.", "ok")
        return 0
    albums = {}
    for t in missing:
        albums.setdefault((t.tag("albumartist") or t.artist, t.album or t.title), []).append(t)
    ctx.log(f"{len(missing)} track(s) without art across {len(albums)} album(s). Looking them up on MusicBrainz…")
    found = 0
    for i, ((artist, album), group) in enumerate(albums.items(), 1):
        ctx.check()
        ctx.progress(i - 1, len(albums), f"Looking up {i} / {len(albums)}")
        if not artist:
            ctx.log(f"  skipped (no artist tag): {group[0].path.name}", "warn")
            continue
        pic = fetch_cover(artist, group[0].album, group[0].title)
        if not pic:
            ctx.log(f"  not found: {artist} – {album}", "warn")
            continue
        for t in group:
            try:
                tags.set_picture(t.path, *pic)
                t.has_art = True
                if converts_to_opus(t.path):
                    o = opus_path_for(t.path, library, opus)
                    if o.exists():
                        tags.set_picture(o, *pic)
                found += 1
            except Exception as e:
                ctx.log(f"  couldn't embed into {t.path.name}: {e}", "error")
        ctx.log(f"  ✓ {artist} – {album}  ({len(group)} track(s))", "ok")
    ctx.progress(len(albums), len(albums))
    ctx.log(f"Added cover art to {found} track(s).", "ok")
    return found


def copy_to_opus(ctx, tracks, library, opus):
    """Embed each converted file's cover into its Opus version when the Opus file has none."""
    pairs = [(t, opus_path_for(t.path, library, opus)) for t in tracks if t.has_art and converts_to_opus(t.path)]
    pairs = [(t, o) for t, o in pairs if o.exists()]
    copied = 0
    for i, (t, o) in enumerate(pairs, 1):
        ctx.check()
        if i % 20 == 0:
            ctx.progress(i, len(pairs), f"Checking Opus art {i} / {len(pairs)}")
        try:
            if tags.get_picture(o) is None and (pic := tags.get_picture(t.path)):
                tags.set_picture(o, *pic)
                copied += 1
                ctx.log(f"  art → {o.name}", "ok")
        except Exception as e:
            ctx.log(f"  {o.name}: {e}", "error")
    ctx.progress(len(pairs), len(pairs))
    ctx.log(f"Copied cover art into {copied} Opus file(s)." if copied else "All Opus files already have cover art.",
            "ok")
    return copied
