"""Library health check: missing tags/art, duplicates, corrupt files and lossy-sourced 'lossless' files."""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .util import LOSSLESS_EXTS, find_tool

KINDS = ["Corrupt", "Lossy source", "Duplicate", "Possible duplicate", "Missing tags", "No cover art",
         "Low quality", "Unreadable"]


@dataclass
class Issue:
    path: Path
    kind: str
    detail: str


def _norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)               # (Live…), [Remastered], (feat. …)
    s = re.sub(r"\s(feat|ft|featuring)\.?\s.*$", " ", s)
    s = re.sub(r"\s-\s.*(remaster|version|edit|mix|live).*$", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


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
        if t.path.suffix.lower() in LOSSLESS_EXTS and t.sample_rate and t.sample_rate < 44100:
            issues.append(Issue(t.path, "Low quality", f"{t.sample_rate / 1000:g} kHz sample rate"))
        elif t.path.suffix.lower() not in LOSSLESS_EXTS and t.bitrate and t.bitrate < 128:
            issues.append(Issue(t.path, "Low quality", f"{t.bitrate} kbps"))

    return issues + duplicate_issues(tracks)


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
            same = [o for o in others if abs(o.duration - t.duration) < 3]
            kind = "Duplicate" if same else "Possible duplicate"
            ref = (same or others)[0]
            issues.append(Issue(t.path, kind, f"Same song as “{ref.path.name}”"
                                + ("" if same else " (different length — another version?)")))
    return issues


def quality_key(t):
    """Higher is better: lossless first, then resolution/bitrate, then art and tag completeness."""
    lossless = t.path.suffix.lower() in LOSSLESS_EXTS
    return (lossless, t.bits if lossless else 0, t.sample_rate if lossless else t.bitrate, t.has_art,
            sum(1 for v in t.tags.values() if v), t.size)


def duplicate_sets(tracks):
    """Copies of the same song with the same length (within 3 s), best copy first.

    Songs whose copies differ in length (live take, radio edit…) are split into separate sets,
    so only true duplicates are offered for deletion.
    """
    sets = []
    for group in _song_groups(tracks):
        group.sort(key=lambda t: t.duration)
        cluster = [group[0]]
        for t in group[1:]:
            if t.duration - cluster[-1].duration < 3:
                cluster.append(t)
            else:
                if len(cluster) > 1:
                    sets.append(cluster)
                cluster = [t]
        if len(cluster) > 1:
            sets.append(cluster)
    for s in sets:
        s.sort(key=quality_key, reverse=True)
    return sorted(sets, key=lambda s: (s[0].artist.lower(), s[0].title.lower()))


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
    if f is None or drop < 25:
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
