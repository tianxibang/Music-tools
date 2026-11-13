"""Library health check: missing tags/art, duplicates, corrupt files and lossy-sourced 'lossless' files."""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .util import LOSSLESS_EXTS, SETTINGS, find_tool

KINDS = ["Corrupt", "Lossy source", "Duplicate", "Possible duplicate", "Missing tags", "No cover art",
         "Low quality", "Unreadable"]


@dataclass
class Issue:
    path: Path
    kind: str
    detail: str


def _fold(s):
    """Case-fold, and strip accents from Latin letters only (é → e). Other scripts are kept as they are:
    the dakuten in が is part of the letter, not an accent."""
    out = []
    for ch in unicodedata.normalize("NFKC", s).casefold():
        base = unicodedata.normalize("NFD", ch)
        out.append(base[0] if len(base) > 1 and base[0].isascii() else ch)
    return "".join(out)


def _norm(s):
    """Comparison key for an artist or title: ignores case, accents, punctuation and (Live…)-style extras.
    Letters of every script count, so Japanese or Cyrillic titles stay distinct."""
    raw = _fold(s)
    s = re.sub(r"[\(\[（【].*?[\)\]）】]", " ", raw)     # (Live…), [Remastered], (feat. …), full-width too
    s = re.sub(r"\s(feat|ft|featuring)\.?\s.*$", " ", s)
    s = re.sub(r"\s-\s.*(remaster|version|edit|mix|live).*$", " ", s)
    key = re.sub(r"[\W_]+", "", s)
    return key or re.sub(r"\s+", " ", raw).strip()  # a title that's only symbols still identifies itself


def quick_checks(tracks):
    issues = []
    for t in tracks:
        if t.error:
            issues.append(Issue(t.path, "Unreadable", t.error))
            continue
        missing = [k for k in ("title", "artist", "album") if not t.tag(k)]
        if missing:
            issues.append(Issue(t.path, "Missing tags", "No " + ", ".join(missing)))
        if not t.has_art:
            issues.append(Issue(t.path, "No cover art", "No embedded front cover"))
        if t.path.suffix.lower() in LOSSLESS_EXTS and t.sample_rate and t.sample_rate < SETTINGS["health_min_rate"]:
            issues.append(Issue(t.path, "Low quality", f"{t.sample_rate / 1000:g} kHz sample rate"))
        elif t.path.suffix.lower() not in LOSSLESS_EXTS and t.bitrate and t.bitrate < SETTINGS["health_min_bitrate"]:
            issues.append(Issue(t.path, "Low quality", f"{t.bitrate} kbps"))

    return issues + duplicate_issues(tracks)


_version_re = (None, None)  # (the version_words setting, its compiled regex)


def _version_words():
    """The version_words setting (demo, live, …) as a regex, compiled once per value."""
    global _version_re
    text = SETTINGS["version_words"]
    if _version_re[0] != text:
        words = sorted({w.strip().lower() for w in text.split(",") if w.strip()}, key=len, reverse=True)
        _version_re = (text, re.compile(r"\b(" + "|".join(map(re.escape, words)) + r")\b") if words else None)
    return _version_re[1]


def _versions(title):
    """The version words in a title: {"demo"} for "Turntail (Pre-Production Demo 2015)", empty for the original."""
    t = unicodedata.normalize("NFKD", title.lower())
    t = re.sub(r"\b(re-?master(ed)?|deluxe|bonus|explicit|clean|mono|stereo)\b", " ", t)
    t = re.sub(r"\b(feat|ft|featuring)\b.*?(?=[\)\]]|$)", " ", t)
    words = _version_words()
    return frozenset(m.group(1).replace(" ", "") for m in words.finditer(t)) if words else frozenset()


def _same_recording(a, b):
    return _versions(a.tag("title")) == _versions(b.tag("title")) and abs(a.duration - b.duration) < 3


def _song_groups(tracks):
    groups = {}
    for t in tracks:
        if t.artist and t.tag("title") and not t.error:
            groups.setdefault((_norm(t.artist), _norm(t.tag("title"))), []).append(t)
    return [g for g in groups.values() if len(g) > 1]


def duplicate_issues(tracks):
    issues = []
    for group in _song_groups(tracks):
        for t in group:
            others = [o for o in group if o is not t]
            same = [o for o in others if _same_recording(t, o)]
            if same:
                issues.append(Issue(t.path, "Duplicate", f"Same song as “{same[0].path.name}”"))
                continue
            ref = others[0]
            diff = _versions(t.tag("title")) ^ _versions(ref.tag("title"))
            why = f"another version ({', '.join(sorted(diff))})" if diff else "different length — another version?"
            issues.append(Issue(t.path, "Possible duplicate", f"Same song as “{ref.path.name}”, but {why}"))
    return issues


def quality_key(t):
    """Higher is better: lossless first, then resolution/bitrate, then art and tag completeness."""
    lossless = t.path.suffix.lower() in LOSSLESS_EXTS
    return (lossless, t.bits if lossless else 0, t.sample_rate if lossless else t.bitrate, t.has_art,
            sum(1 for v in t.tags.values() if v), t.size)


@dataclass
class DupFile:
    track: object
    note: str       # "Best copy", "Copy", "Other version: live", …
    remove: bool    # suggested for removal (only extra copies, never other versions)


def duplicate_groups(tracks):
    """Every song that has more than one file, each file listed once, best first within its copies.

    Copies of the same recording (same version, length within 3 s) suggest removing all but the best one.
    Other versions (demo, live, …) or lengths are only listed, never suggested.
    Songs with copies come first, then those that only have other versions.
    """
    groups = []
    for group in _song_groups(tracks):
        clusters = []
        for t in group:
            home = next((c for c in clusters if _same_recording(c[0], t)), None)
            if home:
                home.append(t)
            else:
                clusters.append([t])
        clusters = [sorted(c, key=quality_key, reverse=True) for c in clusters]
        # The main recording: the biggest set of copies, else one without version words, else the best file
        main = max(clusters, key=lambda c: (len(c), not _versions(c[0].tag("title")), quality_key(c[0])))
        files = []
        for c in [main] + [c for c in clusters if c is not main]:
            for i, t in enumerate(c):
                if len(c) > 1:
                    files.append(DupFile(t, "Best copy (kept)" if i == 0 else "Copy", remove=i > 0))
                elif c is main:
                    files.append(DupFile(t, "Original", remove=False))
                else:
                    diff = _versions(t.tag("title")) ^ _versions(main[0].tag("title"))
                    what = f"Other version: {', '.join(sorted(diff))}" if diff else "Other version: different length"
                    files.append(DupFile(t, what, remove=False))
        groups.append(files)
    return sorted(groups, key=lambda g: (not any(f.remove for f in g), g[0].track.artist.lower(),
                                         g[0].track.title.lower()))


def decode_check(ctx, path, ffmpeg):
    code, _, err = ctx.popen([ffmpeg, "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "null", "-"])
    err = err.strip()
    if code != 0 or err:
        return err.splitlines()[0] if err else f"ffmpeg exited with {code}"
    return None


def spectrum_cutoff(ctx, path, ffmpeg, duration):
    """Find a sharp high-frequency cliff (typical of MP3/AAC) in 30 s from the middle of the track.

    Returns (cutoff_hz, drop_db, nyquist) or None if the spectrum looks full-band.
    """
    rate = 44100
    start = max(0.0, duration / 2 - 15)
    code, raw, _ = ctx.popen([ffmpeg, "-nostdin", "-v", "error", "-ss", f"{start:.1f}", "-t", "30", "-i", str(path),
                              "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], binary=True)
    if code != 0 or len(raw) < rate * 4 * 5:
        return None
    x = np.frombuffer(raw, dtype=np.float32)
    n = 4096
    frames = x[: len(x) // n * n].reshape(-1, n)
    frames = frames[np.abs(frames).max(axis=1) > 1e-4]  # skip silence
    if len(frames) < 20:
        return None
    spec = (np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2).mean(axis=0)
    db = 10 * np.log10(spec + 1e-20)
    freqs = np.fft.rfftfreq(n, 1 / rate)
    db = np.convolve(db, np.ones(9) / 9, mode="same")

    best = (0.0, None)
    # Stop below 20 kHz: genuine masters often roll off around 20.5–22 kHz, so a cliff up
    # there says little, while MP3/AAC encoders cut at 16–19.5 kHz.
    for f in range(14000, 20000, 100):
        below = db[(freqs > f - 1200) & (freqs < f - 200)].mean()
        above = db[(freqs > f + 200) & (freqs < min(f + 1500, rate / 2 - 300))].mean()
        drop = below - above
        if drop > best[0]:
            best = (drop, f)
    drop, f = best
    if f is None or drop < SETTINGS["lossy_cliff_db"]:
        return None
    # A real cliff leaves nothing above it, not just a dip
    tail = db[(freqs > f + 300) & (freqs < rate / 2 - 300)]
    if tail.size and tail.max() > db[(freqs > f - 1200) & (freqs < f - 200)].mean() - 15:
        return None
    return f, drop, rate / 2


def lossy_label(cutoff):
    if cutoff < 17000:
        return "≈128 kbps MP3/AAC or lower"
    if cutoff < 19000:
        return "≈192 kbps lossy"
    return "≈256–320 kbps lossy (or a steep lowpass)"


def check(ctx, tracks, decode=False, spectrum=False, jobs=4):
    ctx.log(f"Checking {len(tracks)} files…")
    issues = quick_checks(tracks)
    ctx.log(f"Tags, art and duplicates: {len(issues)} issue(s).")
    if not (decode or spectrum):
        return issues

    ffmpeg = find_tool("ffmpeg")
    if not ffmpeg:
        ctx.log("ffmpeg is needed for the deep checks. Install it or set its path in Settings.", "error")
        return issues
    lossless = [t for t in tracks if t.path.suffix.lower() in LOSSLESS_EXTS and not t.error]
    work = tracks if decode else lossless
    what = " and ".join(w for w, on in (("decoding every file", decode), ("analysing spectra", spectrum)) if on)
    ctx.log(f"Deep check: {what} ({len(work)} files, this takes a while)…")

    def one(t):
        found = []
        if ctx.cancelled:
            return found
        if decode and (err := decode_check(ctx, t.path, ffmpeg)):
            found.append(Issue(t.path, "Corrupt", err))
        if spectrum and t in lossless and (res := spectrum_cutoff(ctx, t.path, ffmpeg, t.duration)):
            cutoff, drop, _ = res
            found.append(Issue(t.path, "Lossy source",
                               f"Spectrum stops at {cutoff / 1000:.1f} kHz ({drop:.0f} dB cliff) — {lossy_label(cutoff)}"))
        return found

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(one, t) for t in work]
        for i, fut in enumerate(as_completed(futures), 1):
            for issue in fut.result():
                issues.append(issue)
                ctx.log(f"  {issue.kind}: {issue.path.name} — {issue.detail}", "warn")
            ctx.progress(i, len(work), f"Deep check {i} / {len(work)}")
    ctx.check()
    return issues
