"""Lyrics: plain, synced line by line or word by word, stored in the music file and/or a .lrc file next to it.

Synced lyrics are LRC. Word timings use the Enhanced LRC "<mm:ss.xx>" marks, which players without word
support skip over, so one format covers every kind:

    [00:12.30]<00:12.30>Hello <00:12.80>world <00:13.90>    a line, its words, and when the last word ends
    [00:14.00]                                            an empty line: the previous one ends here
                                                           (e.g. an instrumental part until the next line)
    [00:15.00]v2:<00:15.00>Me <00:15.40>too               v1: / v2: who sings it, for duets
    [bg:<00:15.20>(me <00:15.60>too) <00:16.00>]          background vocals / ad-libs going with the line above
    [instrumental:true]                                   a song without words

A word followed by two marks ("word <end><next start>") ends before the next one starts.
"""

import copy
import json
import re
import shutil
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import mutagen
from mutagen.id3 import ID3, USLT, ID3NoHeaderError
from mutagen.mp4 import MP4

from .jobs import Cancelled
from .util import SETTINGS, export_path_for

SAVE_MODES = {"embed": "In the music file", "file": "In a .lrc file next to it", "both": "Both"}
KIND_LABELS = {"": "—", "instrumental": "Instrumental", "plain": "Plain", "lines": "Synced",
               "words": "Word-synced"}
INSTRUMENTAL = "[instrumental:true]"
GAP = 0.0005        # an end mark this close to the next line's start is the same as none

_STAMP = r"(\d+):(\d{1,2}(?:[.:]\d{1,3})?)"
_LINE_STAMP = re.compile(r"\[" + _STAMP + r"\]")
_WORD_STAMP = re.compile(r"<" + _STAMP + r">")
_META = re.compile(r"^\[([a-zA-Z#][\w#-]*):(.*)\]\s*$")
_BG = re.compile(r"^\[bg:(.*)\]$", re.I)
_VOICE = re.compile(r"^\s*(v\d{1,2})\s*:\s*", re.I)
_INSTRUMENTAL = re.compile(r"^\s*\[instrumental:\s*true\s*\]\s*$", re.I)
# Scripts written without spaces between words get one timing per character
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_WORD_SPLIT = re.compile(rf"[{_CJK}]\s*|[^\s{_CJK}]+\s*")


@dataclass
class Word:
    text: str                       # with the spaces that follow it
    start: float | None = None
    end: float | None = None        # None: sung until the next word starts


@dataclass
class Line:
    text: str
    start: float | None = None
    end: float | None = None        # None: shown until the next line starts
    words: list = field(default_factory=list)
    voice: str = ""                 # "v1", "v2", …: who sings it, in a duet
    bg: list = field(default_factory=list)   # background vocals / ad-libs going with it (Lines)

    def ensure_words(self):
        """Split the text into words (keeping any timings that still fit)."""
        if not self.words or "".join(w.text for w in self.words).strip() != self.text.strip():
            self.words = [Word(t) for t in split_words(self.text)] or [Word(self.text)]
        if self.words and self.start is not None:
            self.words[0].start = self.start
        return self.words

    def end_at(self, next_line=None):
        """When this line stops being shown."""
        if self.end is not None:
            return self.end
        return next_line.start if next_line is not None and next_line.start is not None else None

    def word_end(self, j, next_line=None):
        """When word j stops being sung."""
        w = self.words[j]
        if w.end is not None:
            return w.end
        nxt = next((v.start for v in self.words[j + 1:] if v.start is not None), None)
        return nxt if nxt is not None else self.end_at(next_line)

    def shift(self, d):
        """Move the line, its words and ad-libs by d seconds."""
        self.start = _shift(self.start, d)
        self.end = _shift(self.end, d)
        for w in self.words:
            w.start, w.end = _shift(w.start, d), _shift(w.end, d)
        for b in self.bg:
            b.shift(d)


@dataclass
class Lyrics:
    lines: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)    # [ar:…], [ti:…], [by:…] and any other tags, kept as they are
    plain: str = ""                 # the text as typed, for lyrics that aren't synced (keeps blank lines)
    instrumental: bool = False
    precision: int = 2              # decimals of the times written (3 when read that way)

    @property
    def synced(self):
        return any(l.start is not None for l in self.lines)

    @property
    def duet(self):
        return len({l.voice for l in self.lines if l.voice}) > 1

    @property
    def kind(self):
        if self.instrumental:
            return "instrumental"
        if self.synced:
            return "words" if any(sum(w.start is not None for w in l.words) > 1 for l in self.lines) else "lines"
        return "plain" if self.text().strip() else ""

    def text(self):
        if self.instrumental:
            return ""
        if not self.synced and self.plain:
            return self.plain
        return "\n".join(l.text for l in self.lines)

    def timed(self):
        """Lines with a start time, in order."""
        return sorted((l for l in self.lines if l.start is not None), key=lambda l: l.start)

    def copy(self):
        return copy.deepcopy(self)

    def set_text(self, text):
        """Replace the text, keeping the timings (and singers, ad-libs) of lines that are still there."""
        old = self.lines
        new_texts = [t.strip() for t in text.splitlines() if t.strip()]
        lines = []
        sm = SequenceMatcher(a=[l.text for l in old], b=new_texts, autojunk=False)
        for op, a0, a1, b0, b1 in sm.get_opcodes():
            if op == "equal":
                lines += old[a0:a1]
            elif op in ("replace", "insert"):
                # an edited line keeps its place in time (its words are split again); new ones have none
                block = [Line(t) for t in new_texts[b0:b1]]
                nxt = 0
                for i, l in enumerate(old[a0:a1]):
                    if nxt >= len(block):
                        break
                    if l.start is None and not l.voice and not l.bg:
                        continue
                    scores = [1.0 if a1 - a0 == b1 - b0 and j == i else
                              SequenceMatcher(a=l.text, b=block[j].text).ratio() for j in range(nxt, len(block))]
                    best = max(range(len(scores)), key=scores.__getitem__)
                    if scores[best] >= 0.6:
                        b = block[nxt + best]
                        b.start, b.end, b.voice, b.bg = l.start, l.end, l.voice, l.bg
                        nxt += best + 1
                lines += block
        self.lines = lines
        self.plain = text
        if text.strip():
            self.instrumental = False

    def normalize(self):
        """Keep timed lines in time order (untimed ones stay where they are) and every end after its start."""
        slots = [i for i, l in enumerate(self.lines) if l.start is not None]
        ordered = sorted((self.lines[i] for i in slots), key=lambda l: l.start)
        for i, l in zip(slots, ordered):
            self.lines[i] = l
        for l in ordered + [b for l in self.lines for b in l.bg]:
            if l.end is not None and l.start is not None and l.end <= l.start:
                l.end = None
            for w in l.words:
                if w.end is not None and w.start is not None and w.end <= w.start:
                    w.end = None

    def clear_timings(self):
        for l in self.lines:
            l.start = l.end = None
            l.words = []
            for b in l.bg:
                b.start = b.end = None
                b.words = []


def split_words(text):
    return _WORD_SPLIT.findall(text.strip())


# --------------------------------------------------------------------------- LRC


def _secs(m):
    mins, rest = m.group(1), m.group(2).replace(":", ".")
    return int(mins) * 60 + float(rest)


def stamp(t, precision=2):
    """Seconds -> mm:ss.xx (or mm:ss.xxx)"""
    scale = 10 ** precision
    n = max(0, round(t * scale))
    m, n = divmod(n, 60 * scale)
    return f"{m:02}:{n // scale:02}.{n % scale:0{precision}}"


def parse_time(text):
    """'1:23.45', '83.45' or '01:23' -> seconds, or None."""
    text = text.strip()
    if m := re.fullmatch(r"(\d+):(\d{1,2}(?:\.\d+)?)", text):
        return int(m.group(1)) * 60 + float(m.group(2))
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    return None


def is_lrc(text):
    return sum(bool(_LINE_STAMP.match(l.strip())) for l in text.splitlines()) >= 2


def is_instrumental(text):
    return bool(_INSTRUMENTAL.match(text or ""))


def parse(text):
    """LRC (plain, line-synced, Enhanced LRC, with singers and ad-libs) or plain text -> Lyrics."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if is_instrumental(text):
        return Lyrics(instrumental=True)
    if not is_lrc(text):
        return Lyrics([Line(t.strip()) for t in text.splitlines() if t.strip()], plain=text.strip("\n"))
    meta, offset, precision = {}, 0.0, 2
    lines, ends, last = [], [], []      # ends: times of empty lines; last: the lines of the previous row
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if re.search(r"[<\[]\d+:\d{2}\.\d{3}[>\]]", raw):
            precision = 3
        if m := _BG.match(raw):
            bg = _parse_body(m.group(1), None, bg=True)
            for l in last:          # (a line with several times gets it each time)
                if bg is not None:
                    l.bg.append(copy.deepcopy(bg))
            continue
        if (m := _META.match(raw)) and not _LINE_STAMP.match(raw):
            key, val = m.group(1).lower(), m.group(2).strip()
            if key == "offset":
                try:
                    offset = int(val) / 1000   # positive: lyrics come earlier
                except ValueError:
                    pass
            else:
                meta[key] = val
            continue
        starts, pos = [], 0
        while m := _LINE_STAMP.match(raw, pos):
            starts.append(_secs(m))
            pos = m.end()
        body = raw[pos:]
        voice = ""
        if v := _VOICE.match(body):
            voice, body = v.group(1).lower(), body[v.end():]
        if not starts:
            last = [Line(body.strip(), voice=voice)]
            lines += last
            continue
        last = []
        for s in starts:
            line = _parse_body(body, s)
            if line is None:
                ends.append(s)
            else:
                line.voice = voice
                last.append(line)
        lines += last
    doc = Lyrics(lines, meta, precision=precision)
    if not lines and meta.get("instrumental", "").lower() == "true":
        return Lyrics(instrumental=True)
    doc.normalize()
    timed = doc.timed()
    for s in sorted(ends):
        # an empty timed line ends the line before it
        before = [l for l in timed if l.start < s]
        if before and before[-1].end is None:
            before[-1].end = s
    if offset:
        for l in lines:
            l.shift(-offset)
    return doc


def _shift(t, d):
    return None if t is None else max(0.0, t + d)


def _parse_body(body, start, bg=False):
    """'<t>Hello <t>world <t>' -> Line (None for an empty end mark). Ad-libs take their start from their words."""
    marks = list(_WORD_STAMP.finditer(body))
    if not marks:
        text = body.strip()
        return Line(text, start) if text else None
    words, trailing = [], None
    lead = body[:marks[0].start()]
    if lead.strip():
        words.append(Word(lead.lstrip(), start))
    for i, m in enumerate(marks):
        seg = body[m.end():marks[i + 1].start() if i + 1 < len(marks) else len(body)]
        t = _secs(m)
        if seg.strip():
            words.append(Word(seg, t))
        elif words and words[-1].end is None and (words[-1].start is None or t > words[-1].start):
            words[-1].end = t          # a mark right after a word: when it ends
            if i == len(marks) - 1:
                trailing = t
        elif words:
            words[-1].text += seg
    text = "".join(w.text for w in words).strip()
    if not text:
        return None
    words[-1].text = words[-1].text.rstrip()
    if bg:
        start = next((w.start for w in words if w.start is not None), None)
        trailing = words[-1].end
    line = Line(text, start, trailing if bg else None, words)
    if len(words) == 1 and words[0].end is None:
        line.words = []                # a single timed word is just a line
    return line


def _body(l, precision, words=True, marks=False):
    """The text of a line with its word marks (always some for an ad-lib: marks=True, it has no line time)."""
    st = lambda t: f"<{stamp(t, precision)}>"   # noqa: E731
    timed = sum(w.start is not None for w in l.words) > 1 or any(w.end is not None for w in l.words)
    if not (words or marks) or not l.words or not (timed or marks):
        return l.text
    body = ""
    last = len(l.words) - 1
    for j, w in enumerate(l.words):
        s = w.start if w.start is not None else (l.start if j == 0 else None)
        if s is not None and (words or j == 0):
            body += st(s)
        body += w.text.rstrip() if j == last else w.text   # an untimed word carries on the one before
        end = w.end if w.end is not None else (l.end if j == last else None)
        if end is not None and (words or j == last):
            body += st(end)
    return body.rstrip()


def to_lrc(lyrics, words=True):
    """Lyrics -> LRC text; word timings only when words=True."""
    if lyrics.instrumental:
        return INSTRUMENTAL
    p = lyrics.precision
    out = [f"[{k}:{v}]" for k, v in lyrics.meta.items() if k != "instrumental"]
    timed = lyrics.timed()
    nxt_of = {id(l): timed[i + 1] if i + 1 < len(timed) else None for i, l in enumerate(timed)}
    for l in lyrics.lines:
        prefix = f"{l.voice}:" if l.voice else ""
        if l.start is None:
            out.append(prefix + l.text)          # not timed yet: kept, in its place
        else:
            out.append(f"[{stamp(l.start, p)}]{prefix}{_body(l, p, words)}")
        for b in l.bg:
            if b.start is not None and not b.words:
                b = copy.deepcopy(b)
                b.words = [Word(b.text, b.start)]
            out.append(f"[bg:{_body(b, p, words, marks=True) if b.start is not None else b.text}]")
        if l.start is not None and l.end is not None:
            nxt = nxt_of.get(id(l))
            if nxt is None or nxt.start - l.end > GAP:
                out.append(f"[{stamp(l.end, p)}]")
    return "\n".join(out) + "\n" if out else ""


def serialize(lyrics):
    """What gets saved: LRC when synced, the plain text otherwise."""
    if lyrics.instrumental:
        return INSTRUMENTAL
    if lyrics.synced:
        return to_lrc(lyrics)
    return lyrics.text().strip("\n") + "\n" if lyrics.text().strip() else ""


def kind_of(text):
    return parse(text).kind if text and text.strip() else ""


# ----------------------------------------------------------------------- storage


def sidecar(path):
    return Path(path).with_suffix(".lrc")


def read_embedded(path):
    path = Path(path)
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            frames = ID3(path).getall("USLT")
            return frames[0].text if frames else ""
        if ext in (".m4a", ".mp4", ".aac"):
            vals = (MP4(path).tags or {}).get("\xa9lyr")
            return vals[0] if vals else ""
        m = mutagen.File(path)
        tags = (m.tags if m else None) or {}
        for key in ("lyrics", "unsyncedlyrics"):
            if key in tags and tags[key]:
                v = tags[key]
                return v[0] if isinstance(v, list) else str(v)
    except Exception:
        pass
    return ""


def write_embedded(path, text):
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("USLT")
        if text:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
        tags.save(path)
        return
    if ext in (".m4a", ".mp4", ".aac"):
        m = MP4(path)
        if m.tags is None:
            m.add_tags()
        if text:
            m.tags["\xa9lyr"] = [text]
        else:
            m.tags.pop("\xa9lyr", None)
        m.save()
        return
    m = mutagen.File(path)
    if m is None:
        raise ValueError(f"Can't store lyrics in {path.name}")
    if m.tags is None:
        m.add_tags()
    # some downloaders write the same lyrics to UNSYNCEDLYRICS too: keep that copy in step rather than drop it
    both = "unsyncedlyrics" in m.tags
    for key in ("unsyncedlyrics", "lyrics"):
        if key in m.tags:
            del m.tags[key]
    if text:
        m.tags["LYRICS"] = [text]
        if both:
            m.tags["UNSYNCEDLYRICS"] = [text]
    m.save()


def read_sidecar(path):
    p = sidecar(path)
    try:
        return p.read_text(encoding="utf-8-sig") if p.is_file() else ""
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")
    except OSError:
        return ""


def write_sidecar(path, text):
    p = sidecar(path)
    if text:
        tmp = p.with_name(p.name + ".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
    else:
        p.unlink(missing_ok=True)


_RANK = {"": 0, "instrumental": 1, "plain": 1, "lines": 2, "words": 3}


def load(path):
    """(text, where) of a file's lyrics: the richer of the embedded ones and the .lrc file, which wins a tie."""
    emb, side = read_embedded(path), read_sidecar(path)
    if not side.strip():
        return emb, "embed" if emb.strip() else ""
    if not emb.strip() or _RANK[kind_of(side)] >= _RANK[kind_of(emb)]:
        return side, "file"
    return emb, "embed"


def save_one(path, text, mode="embed"):
    """Store lyrics in one file. Places that already hold lyrics are updated too, so copies never disagree."""
    path = Path(path)
    embed = mode in ("embed", "both") or (mode == "file" and read_embedded(path).strip())
    side = mode in ("file", "both") or sidecar(path).is_file()
    if embed:
        write_embedded(path, text)
    if side:
        write_sidecar(path, text)


def save(path, text, mode="embed", library=None, opus=None):
    """Store lyrics in a file and, for a library file, in its copy in the Opus export."""
    save_one(path, text, mode)
    if library and opus:
        try:
            copy = export_path_for(path, library, opus)
        except ValueError:
            return
        if copy.exists() and copy != Path(path):
            save_one(copy, text, mode)


def copy_sidecars(library, opus, pairs):
    """Bring .lrc files next to library songs over to their export copies. pairs: (song, copy). Returns count."""
    n = 0
    for src, dst in pairs:
        s = sidecar(src)
        if not s.is_file() or not dst.exists():
            continue
        d = sidecar(dst)
        try:
            if d.is_file() and d.read_bytes() == s.read_bytes():
                continue
            shutil.copy2(s, d)
            n += 1
        except OSError:
            pass
    return n


def move_sidecar(old, new):
    s = sidecar(old)
    d = sidecar(new)
    if s.is_file() and not d.exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(s), str(d))


# ------------------------------------------------------------------------ LRCLIB


LRCLIB = "https://lrclib.net/api"
_request_lock = threading.Lock()
_last_request = 0.0


class TooManyRequests(Exception):
    pass


def _pause(secs, ctx=None):
    end = time.time() + secs
    while time.time() < end:
        if ctx:
            ctx.check()
        time.sleep(min(0.25, max(0.0, end - time.time())))


def _lrclib(endpoint, params, timeout=15, ctx=None):
    """One LRCLIB request (None when it has nothing), at most one a second. When LRCLIB says we're asking too
    often, wait as long as it says (or longer each time) and try again; give up after a few tries."""
    from .art import _get
    import urllib.error
    global _last_request
    url = f"{LRCLIB}/{endpoint}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        with _request_lock:
            _pause(_last_request + max(1.0, SETTINGS["lrclib_gap"]) - time.time(), ctx)
            _last_request = time.time()
        try:
            return json.loads(_get(url, timeout))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code not in (429, 503):
                raise
            try:
                wait = float(e.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                wait = 0
            _pause(max(wait, 15 * (attempt + 1)), ctx)
    raise TooManyRequests("LRCLIB says we're asking too often. Try again later.")


def search_online(artist, title, album="", duration=0, timeout=15, ctx=None, quick=False):
    """Lyrics from LRCLIB (free, no account): [{artist, title, album, duration, synced, plain, instrumental}].

    It first asks for this exact song (one request); quick=True stops there when that finds it, otherwise it
    also searches by title and artist for other versions (a second request).
    """
    found, seen = [], set()

    def add(item):
        if not item or item.get("id") in seen:
            return
        seen.add(item.get("id"))
        found.append({"artist": item.get("artistName") or "", "title": item.get("trackName") or "",
                      "album": item.get("albumName") or "", "duration": item.get("duration") or 0,
                      "synced": item.get("syncedLyrics") or "", "plain": item.get("plainLyrics") or "",
                      "instrumental": bool(item.get("instrumental"))})

    if artist and title and duration:
        add(_lrclib("get", {"artist_name": artist, "track_name": title, "album_name": album,
                            "duration": round(duration)}, timeout, ctx))
    if not (quick and found):
        for item in _lrclib("search", {"track_name": title, **({"artist_name": artist} if artist else {})},
                            timeout, ctx) or []:
            add(item)
    # the best matches first: synced, then close in length
    found.sort(key=lambda f: (not f["synced"], not f["plain"],
                              abs((f["duration"] or 0) - duration) if duration else 0))
    return found


# ------------------------------------------------------------------ many songs


def _norm(s):
    """'Song (feat. X) [Remastered]' -> 'song' for comparing titles and artists."""
    s = (s or "").lower()
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"\s+(feat\.?|ft\.?|featuring|with)\s.*$", "", s)
    return re.sub(r"[^\w]+", " ", s).strip()


def confident(result, artist, title, duration):
    """Is this search result surely the song? Same title, an artist in common, and about as long."""
    if duration and result["duration"] and abs(result["duration"] - duration) > 3:
        return False
    t1, t2 = _norm(result["title"]), _norm(title)
    if not t1 or not (t1 == t2 or SequenceMatcher(a=t1, b=t2).ratio() >= 0.9):
        return False
    ours = {a for a in re.split(r"\s*(?:,|&|\band\b|/|;|\bx\b)\s*", _norm(artist)) if a}
    theirs = _norm(result["artist"])
    return not ours or any(a in theirs or theirs in a for a in ours)


def backup_dir():
    from .util import config_dir
    return config_dir() / "lyrics-backups"


def backup(path, text, library=None):
    """Keep a copy of lyrics about to be replaced, as a .lrc in the backups folder. Returns its path."""
    path = Path(path)
    try:
        rel = path.relative_to(library) if library else Path(path.name)
    except ValueError:
        rel = Path(path.name)
    dest = backup_dir() / time.strftime("%Y-%m-%d") / rel.with_suffix(".lrc")
    n = 2
    while dest.exists():
        dest = dest.with_name(f"{rel.stem} ({n}).lrc")
        n += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


class History:
    """What the online search did to which songs, for the "Added online" list (newest first)."""

    def __init__(self, path=None):
        from .util import config_dir
        self.path = Path(path or config_dir() / "lyrics-online.json")
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.items[:5000], indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, item):
        self.items = [i for i in self.items if i["path"] != item["path"]]
        self.items.insert(0, item)


# what each search result does
ADDED, REPLACED, INSTRUMENTAL_FOUND, UNSURE, NOT_FOUND, UNDONE, FAILED = (
    "added", "replaced", "instrumental", "unsure", "notfound", "undone", "error")
OUTCOMES = {ADDED: "Added", REPLACED: "Replaced", INSTRUMENTAL_FOUND: "Instrumental", UNSURE: "Check the match",
            NOT_FOUND: "Not found", FAILED: "Couldn't look up", UNDONE: "Undone"}




def fetch_many(ctx, tracks, want="missing", mode="embed", library=None, opus=None, history=None, on_saved=None,
               search=None):
    """Look up lyrics online for many songs and save the sure matches.

    want: "missing" (songs without lyrics), "unsynced" (also plain ones, which get synced lyrics) or "all"
    (anything better than what's there). Replaced lyrics are backed up first. Unsure matches aren't saved:
    they're listed in the history with the best result, to be checked. on_saved(path, old, new) is called
    after each save (followers queue the edit for the main PC). Returns {outcome: count}, plus "stopped": why,
    when it had to stop early (LRCLIB asking us to slow down, or not reachable).
    """
    search = search or (lambda artist, title, album, duration:
                        search_online(artist, title, album, duration, ctx=ctx, quick=True))
    history = history or History()
    counts = {k: 0 for k in OUTCOMES if k != UNDONE}
    recent = {i["path"] for i in history.items if i["outcome"] in (NOT_FOUND, UNSURE)
              and time.time() - i["time"] < SETTINGS["lyrics_recheck_days"] * 86400}
    skipped = failures = 0
    for i, t in enumerate(tracks, 1):
        ctx.check()
        ctx.progress(i, len(tracks), f"Looking up {t.display}")
        old, _ = load(t.path)
        kind = kind_of(old)
        if (want == "missing" and kind) or (want == "unsynced" and kind not in ("", "plain")):
            continue
        if str(t.path) in recent:
            skipped += 1
            continue
        try:
            results = search(t.artist, t.tag("title") or t.path.stem, t.album, t.duration)
            failures = 0
        except Exception as e:
            if isinstance(e, Cancelled):
                raise
            # listed too (it's looked up again next time), so a failed run doesn't just look empty
            err = str(e) or type(e).__name__
            history.add({"path": str(t.path), "song": t.display, "time": time.time(), "backup": "",
                         "checked": False, "outcome": FAILED, "match": err})
            counts[FAILED] += 1
            failures += 1
            ctx.log(f"  {OUTCOMES[FAILED]:<16} {t.display}: {err}", "error")
            if isinstance(e, TooManyRequests) or failures >= 3:
                counts["stopped"] = (str(e) if isinstance(e, TooManyRequests) else
                                     f"LRCLIB couldn't be reached ({err}). Check the internet connection and "
                                     "try again.")
                ctx.log(f"Stopped: {counts['stopped']} The songs done so far are saved.", "warn")
                break
            continue
        sure = [r for r in results if confident(r, t.artist, t.tag("title") or t.path.stem, t.duration)]
        best = next((r for r in sure if r["synced"]), None) or next(
            (r for r in sure if r["instrumental"]), None) or next((r for r in sure if r["plain"]), None)
        item = {"path": str(t.path), "song": t.display, "time": time.time(), "backup": "", "checked": False}
        if best is None:
            near = next((r for r in results if r["synced"] or r["plain"]), None)
            outcome = UNSURE if near else NOT_FOUND
            if near:
                item.update(match=_describe(near), text=near["synced"] or near["plain"],
                            kind=kind_of(near["synced"] or near["plain"]))
        else:
            text = INSTRUMENTAL if best["instrumental"] and not best["synced"] else best["synced"] or best["plain"]
            new_kind = kind_of(text)
            if kind and (_RANK[new_kind] < _RANK[kind] or old.strip() == text.strip()
                         or _RANK[new_kind] == _RANK[kind] and want != "all"):
                continue                     # nothing better than what's there (never a step down)
            if old.strip():
                item["backup"] = str(backup(t.path, old, library))
            save(t.path, text, mode, library, opus)
            if on_saved:
                on_saved(t.path, old, text)
            outcome = INSTRUMENTAL_FOUND if new_kind == "instrumental" else REPLACED if old.strip() else ADDED
            item.update(match=_describe(best), kind=new_kind, text=text)
        item["outcome"] = outcome
        counts[outcome] += 1
        history.add(item)
        if outcome != NOT_FOUND or i % 20 == 0:
            history.save()
        ctx.log(f"  {OUTCOMES[outcome]:<16} {t.display}", "ok" if outcome in (ADDED, REPLACED) else
                "warn" if outcome == UNSURE else "muted")
    history.save()
    if skipped:
        ctx.log(f"Skipped {skipped} song(s) looked up in the last {SETTINGS['lyrics_recheck_days']} days without a sure match "
                "(see the Added online tab).", "muted")
    return counts


def _describe(r):
    m, s = divmod(round(r["duration"] or 0), 60)
    return f"{r['artist']} – {r['title']}" + (f" ({m}:{s:02})" if r["duration"] else "")


def relocate(path, to, keep=False, library=None, opus=None):
    """Move a song's lyrics into a .lrc file (to="file") or into the music file (to="embed"), for its export
    copy too. keep: leave the other copy as it is. Returns True if there was something to move."""
    text, _ = load(path)
    if not text.strip():
        return False
    targets = [Path(path)]
    if library and opus:
        try:
            copy_ = export_path_for(path, library, opus)
            if copy_.exists() and copy_ != Path(path):
                targets.append(copy_)
        except ValueError:
            pass
    for p in targets:
        if to == "file":
            write_sidecar(p, text)
            if not keep and read_embedded(p).strip():
                write_embedded(p, "")
        else:
            write_embedded(p, text)
            if not keep:
                write_sidecar(p, "")
    return True
