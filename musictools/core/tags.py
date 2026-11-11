"""Reading and writing tags and cover art (FLAC, Ogg Opus/Vorbis, MP3, M4A) plus name templates."""

import base64
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4, MP4Cover

from .util import AUDIO_EXTS, safe_filename

FIELDS = ["title", "artist", "album", "albumartist", "tracknumber", "discnumber", "date", "genre"]
FIELD_LABELS = {"title": "Title", "artist": "Artist", "album": "Album", "albumartist": "Album artist",
                "tracknumber": "Track", "discnumber": "Disc", "date": "Year / date", "genre": "Genre", "lyrics": "Lyrics"}


@dataclass
class Track:
    path: Path
    size: int = 0
    mtime: float = 0
    duration: float = 0
    bitrate: int = 0          # kbps
    sample_rate: int = 0
    bits: int = 0
    channels: int = 0
    codec: str = ""
    tags: dict = field(default_factory=dict)
    has_art: bool = False
    rg_gain: float | None = None
    r128: int | None = None
    lyrics: str = ""          # "" | plain | lines | words (see lyrics.KIND_LABELS)
    error: str = ""

    def tag(self, key):
        return self.tags.get(key, "")

    @property
    def title(self):
        return self.tag("title") or self.path.stem

    @property
    def artist(self):
        return self.tag("artist")

    @property
    def album(self):
        return self.tag("album")

    @property
    def track_no(self):
        return _int(self.tag("tracknumber"))

    @property
    def year(self):
        m = re.match(r"\d{4}", self.tag("date"))
        return m.group(0) if m else ""

    @property
    def display(self):
        return f"{self.artist} – {self.title}" if self.artist else self.title


def _int(v):
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def _first(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v)


def _open_easy(path):
    return mutagen.File(path, easy=True)


def read_track(path):
    path = Path(path)
    st = path.stat()
    t = Track(path=path, size=st.st_size, mtime=st.st_mtime, codec=path.suffix.lower().lstrip("."))
    try:
        m = _open_easy(path)
        if m is None:
            t.error = "Not a recognised audio file"
            return t
        info = m.info
        t.duration = getattr(info, "length", 0) or 0
        t.sample_rate = getattr(info, "sample_rate", 0) or 0
        t.bits = getattr(info, "bits_per_sample", 0) or 0
        t.channels = getattr(info, "channels", 0) or 0
        br = getattr(info, "bitrate", 0) or 0
        if not br and t.duration:
            br = int(t.size * 8 / t.duration)
        t.bitrate = round(br / 1000)
        tags = m.tags or {}
        for k in FIELDS:
            if k in tags:
                t.tags[k] = _first(tags[k])
        t.has_art = _has_art(path, m)
        t.lyrics = _lyrics_kind(path, tags)
        if "replaygain_track_gain" in tags:
            t.rg_gain = _float(_first(tags["replaygain_track_gain"]))
        if "r128_track_gain" in tags:
            try:
                t.r128 = int(_first(tags["r128_track_gain"]))
            except ValueError:
                pass
    except Exception as e:
        t.error = str(e) or type(e).__name__
    return t


def _lyrics_kind(path, tags):
    from . import lyrics
    if path.suffix.lower() in (".mp3", ".m4a", ".mp4", ".aac"):
        embedded = lyrics.read_embedded(path)
    else:
        embedded = next((_first(tags[k]) for k in ("lyrics", "unsyncedlyrics") if k in tags), "")
    side = lyrics.read_sidecar(path) if lyrics.sidecar(path).is_file() else ""
    return max(lyrics.kind_of(embedded), lyrics.kind_of(side), key=lyrics._RANK.__getitem__)


def _float(s):
    m = re.search(r"[-+]?\d+(\.\d+)?", s or "")
    return float(m.group(0)) if m else None


def _has_art(path, m):
    if isinstance(m, FLAC):
        return bool(m.pictures)
    ext = path.suffix.lower()
    if ext in (".opus", ".ogg"):
        return bool(m.tags and "metadata_block_picture" in m.tags)
    if ext == ".mp3":
        try:
            return bool(ID3(path).getall("APIC"))
        except Exception:
            return False
    if ext in (".m4a", ".mp4", ".aac"):
        try:
            return bool(MP4(path).tags.get("covr"))
        except Exception:
            return False
    return False


def read_tags(path):
    m = _open_easy(path)
    tags = (m.tags if m else None) or {}
    return {k: _first(tags[k]) for k in FIELDS if k in tags}


def write_tags(path, changes):
    """changes: {field: value}; an empty value removes the field."""
    m = _open_easy(path)
    if m is None:
        raise ValueError(f"Unsupported file: {path}")
    if m.tags is None:
        m.add_tags()
    for k, v in changes.items():
        if v:
            m.tags[k] = [v]
        elif k in m.tags:
            del m.tags[k]
    m.save()


def write_raw(path, changes):
    """Write arbitrary Vorbis-comment style keys (FLAC/Ogg only), e.g. REPLAYGAIN_*, R128_*."""
    m = mutagen.File(path)
    if m.tags is None:
        m.add_tags()
    for k, v in changes.items():
        m.tags[k] = [v]
    m.save()


def get_picture(path):
    """(bytes, mime) of the front cover, or None."""
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".flac":
            pics = FLAC(path).pictures
            pic = next((p for p in pics if p.type == 3), pics[0] if pics else None)
            return (pic.data, pic.mime) if pic else None
        if ext in (".opus", ".ogg"):
            m = mutagen.File(path)
            for b64 in (m.tags or {}).get("metadata_block_picture", []):
                pic = Picture(base64.b64decode(b64))
                return pic.data, pic.mime
        if ext == ".mp3":
            apics = ID3(path).getall("APIC")
            return (apics[0].data, apics[0].mime) if apics else None
        if ext in (".m4a", ".mp4", ".aac"):
            covr = MP4(path).tags.get("covr")
            if covr:
                mime = "image/png" if covr[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
                return bytes(covr[0]), mime
    except Exception:
        pass
    return None


def set_picture(path, data, mime="image/jpeg"):
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".flac", ".opus", ".ogg"):
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = data
        if ext == ".flac":
            m = FLAC(path)
            m.clear_pictures()
            m.add_picture(pic)
        else:
            m = mutagen.File(path)
            if m.tags is None:
                m.add_tags()
            m.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        m.save()
    elif ext == ".mp3":
        tags = ID3(path)
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        tags.save()
    elif ext in (".m4a", ".mp4", ".aac"):
        m = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        m.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        m.save()
    else:
        raise ValueError(f"Can't embed art in {ext} files")


def image_mime(data):
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


# --- templates ---------------------------------------------------------------

TEMPLATE_HELP = "{artist} {title} {album} {albumartist} {track} {disc} {year} {genre} — e.g. {track:02}"


class _Formatter(string.Formatter):
    def get_value(self, key, args, kwargs):
        return kwargs.get(key, "")

    def format_field(self, value, spec):
        if spec and isinstance(value, str) and not value:
            return ""
        return super().format_field(value, spec)


def template_fields(tags, path=None):
    tags = dict(tags)
    year = re.match(r"\d{4}", tags.get("date", ""))
    track = _int(tags.get("tracknumber"))
    disc = _int(tags.get("discnumber"))
    return {
        "title": tags.get("title") or (Path(path).stem if path else ""),
        "artist": tags.get("artist", ""),
        "album": tags.get("album", ""),
        "albumartist": tags.get("albumartist") or tags.get("artist", ""),
        "track": track if track is not None else "",
        "disc": disc if disc is not None else "",
        "year": year.group(0) if year else "",
        "genre": tags.get("genre", ""),
    }


def render(template, tags, path=None):
    """Render a template into a relative path (components split on '/'), each part filename-safe."""
    # a "/" inside a tag (e.g. a date in a title) must not create folders; only the template's do
    fields = {k: v.replace("/", "-").replace("\\", "-") if isinstance(v, str) else v
              for k, v in template_fields(tags, path).items()}
    text = _Formatter().format(template, **fields)
    parts = []
    for part in text.replace("\\", "/").split("/"):
        # tidy separators left dangling by empty fields, e.g. " - Title" or "Artist - "
        part = re.sub(r"^\s*[-–_.]+\s*|\s*[-–_]+\s*$", "", part)
        part = re.sub(r"\s*-\s*-\s*", " - ", part)
        parts.append(safe_filename(part))
    return "/".join(parts)


def pattern_to_regex(pattern):
    """'{artist} - {title}' -> compiled regex with named groups."""
    out, pos = "", 0
    for m in re.finditer(r"\{(\w+)\}", pattern):
        out += re.escape(pattern[pos:m.start()])
        name = m.group(1)
        out += rf"(?P<{name}>\d+)" if name in ("track", "disc", "year") else rf"(?P<{name}>.+?)"
        pos = m.end()
    out += re.escape(pattern[pos:])
    return re.compile(f"^{out}$")


def parse_filename(stem, pattern):
    m = pattern_to_regex(pattern).match(stem)
    if not m:
        return None
    rename = {"track": "tracknumber", "disc": "discnumber", "year": "date"}
    return {rename.get(k, k): v.strip() for k, v in m.groupdict().items() if v and v.strip()}


def is_audio(path):
    return Path(path).suffix.lower() in AUDIO_EXTS
