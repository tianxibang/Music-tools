"""Loudness normalization tags: ReplayGain on FLAC, R128 gain on Opus (what Android players read)."""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import tags
from .util import converts_to_opus, find_tool, opus_path_for

RG_REF = -18.0    # ReplayGain 2 reference loudness (LUFS)
R128_REF = -23.0  # Opus R128_TRACK_GAIN reference (RFC 7845)


def measure(ctx, path, ffmpeg):
    """Integrated loudness (LUFS) and true peak (linear) via ffmpeg's ebur128 filter."""
    code, _, err = ctx.popen([ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                              "-af", "ebur128=peak=true", "-f", "null", "-"])
    if code != 0:
        return None
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", err)
    if not i:
        return None
    peak_db = float(peak[-1]) if peak and peak[-1] != "-inf" else -70.0
    return float(i[-1]), 10 ** (peak_db / 20)


def r128_value(gain_db):
    """Opus R128 gain from a ReplayGain-style gain (dB vs -18 LUFS), as Q7.8 fixed point."""
    q = round((gain_db + (R128_REF - RG_REF)) * 256)
    return max(-32768, min(32767, q))


def tag_library(ctx, tracks, library, opus, force=False, jobs=4):
    """Write ReplayGain to FLACs that lack it and R128 gain to their Opus versions.

    FLACs that already have REPLAYGAIN_TRACK_GAIN are trusted unless force is set,
    so only new or untagged tracks need the (slow) ffmpeg measurement.
    """
    ffmpeg = find_tool("ffmpeg")
    work = []
    for t in tracks:
        if not converts_to_opus(t.path):
            continue
        o = opus_path_for(t.path, library, opus)
        # ReplayGain tags are only written to FLACs; other sources (an Atmos .m4a, say) are measured when
        # their Opus version needs its R128 gain
        need_flac = t.codec == "flac" and (force or t.rg_gain is None)
        need_opus = o.exists() and (force or "r128_track_gain" not in _raw_keys(o))
        if need_opus and t.codec != "flac" and t.rg_gain is None:
            need_flac = True
        if need_flac or need_opus:
            work.append((t, o if o.exists() else None, need_flac, need_opus))
    if not work:
        ctx.log("All tracks already have loudness tags.", "ok")
        return 0
    if any(need_flac for _, _, need_flac, _ in work) and not ffmpeg:
        ctx.log("ffmpeg is needed to measure loudness. Install it or set its path in Settings.", "error")
        return 0

    ctx.log(f"{len(work)} track(s) need loudness tags.")

    def one(t, o, need_flac, need_opus):
        if ctx.cancelled:
            return False
        gain = t.rg_gain
        if need_flac:
            # A downmixed or re-encoded source is measured as the phone will hear it: the Opus file itself
            res = measure(ctx, t.path if t.codec == "flac" or o is None else o, ffmpeg)
            if not res:
                ctx.log(f"  could not measure {t.path.name}", "error")
                return False
            lufs, peak = res
            gain = RG_REF - lufs
            if t.codec == "flac":
                tags.write_raw(t.path, {"REPLAYGAIN_TRACK_GAIN": f"{gain:+.2f} dB",
                                        "REPLAYGAIN_TRACK_PEAK": f"{peak:.6f}"})
            t.rg_gain = gain
        if need_opus and o is not None and gain is not None:
            tags.write_raw(o, {"R128_TRACK_GAIN": str(r128_value(gain))})
        ctx.log(f"  {gain:+6.2f} dB  {t.path.name}", "ok")
        return True

    ok = 0
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(one, *w) for w in work]
        for i, fut in enumerate(as_completed(futures), 1):
            ok += bool(fut.result())
            ctx.progress(i, len(work), f"Loudness {i} / {len(work)}")
    ctx.check()
    ctx.log(f"Loudness tags written for {ok} track(s).", "ok")
    return ok


def _raw_keys(path):
    import mutagen
    m = mutagen.File(path)
    return {k.lower() for k in (m.tags.keys() if m and m.tags else [])}
