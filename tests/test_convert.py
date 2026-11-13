"""Which library files become Opus and which are copied into the export as they are."""

import shutil
import subprocess

import pytest
from mutagen.oggopus import OggOpus

from musictools.core import convert, library, loudness, lyrics, tags, util
from musictools.core.jobs import Context
from musictools.core.util import SETTINGS, converts_to_opus

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

JPEG = b"\xff\xd8\xff\xe0" + bytes(64) + b"\xff\xd9"


def make(path, title, kbps=None, channels=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = ["-b:a", f"{kbps}k"] if kbps else []
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", str(channels), *rate, "-metadata", f"title={title}",
                    "-metadata", "artist=Band", str(path)], check=True)
    return path


@pytest.mark.parametrize("info, expected", [
    (("eac3", 6, 768, False), True),   # Dolby Atmos .m4a: phones can't play it
    (("aac", 2, 256, False), True),    # far above the 128 kbps Opus: converting makes it much smaller
    (("aac", 2, 128, False), False),   # small AAC: another lossy pass would only cost quality
    (("mp3", 2, 192, False), False),   # right at the limit (1.5×): kept
    (("opus", 2, 256, False), False),  # Opus is never re-encoded
    (("alac", 2, 900, True), True),    # lossless in an .m4a
    (("aac", 6, 128, False), True),    # multichannel: downmixed
    (("", 2, 0, False), False),        # unknown codec: copied like before
])
def test_verdict_by_codec(tmp_path, monkeypatch, info, expected):
    f = tmp_path / f"{info[0] or 'x'}-{info[1]}-{info[2]}.m4a"
    f.write_bytes(b"0")
    monkeypatch.setattr(util, "stream_info", lambda p: info)
    assert converts_to_opus(f, bitrate=128) is expected


def test_real_files(tmp_path, monkeypatch):
    monkeypatch.setitem(SETTINGS, "bitrate", 128)
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    flac = make(lib / "Band-Hi.flac", "Hi")
    small = make(lib / "Lossy" / "Band-Small.mp3", "Small", 128)
    big = make(lib / "Lossy" / "Band-Big.mp3", "Big", 320)
    atmos = make(lib / "Lossy" / "Band-Atmos.m4a", "Atmos", 384, channels=6)
    tags.set_picture(atmos, JPEG)
    lyrics.write_embedded(atmos, "[00:01.00] la la")
    # What an earlier version left behind: the .m4a copied as it was, and an Opus whose song is gone
    stale = opus / "Lossy" / "Band-Atmos.m4a"
    stale.parent.mkdir(parents=True)
    shutil.copy(atmos, stale)
    (opus / "Gone.opus").write_bytes(b"OggS")

    assert [converts_to_opus(f) for f in (flac, small, big, atmos)] == [True, False, True, True]
    assert {o.relative_to(opus).as_posix() for _, o in convert.plan(lib, opus)} == \
        {"Band-Hi.opus", "Lossy/Band-Big.opus", "Lossy/Band-Atmos.opus"}
    assert [c.relative_to(opus).as_posix() for _, c in convert.lossy_plan(lib, opus)] == ["Lossy/Band-Small.mp3"]

    convert.convert(Context(), lib, opus, 128, 2)
    names = {p.relative_to(opus).as_posix() for p in opus.rglob("*") if p.is_file()}
    assert names == {"Band-Hi.opus", "Lossy/Band-Big.opus", "Lossy/Band-Atmos.opus", "Lossy/Band-Small.mp3",
                     "Gone.opus"}, names
    out = OggOpus(opus / "Lossy" / "Band-Atmos.opus")
    assert out.info.channels == 2
    assert out["title"] == ["Atmos"] and out["artist"] == ["Band"]
    assert tags.get_picture(out.filename)[0] == JPEG
    assert lyrics.read_embedded(out.filename) == "[00:01.00] la la"
    assert OggOpus(opus / "Lossy" / "Band-Big.opus")["title"] == ["Big"]

    st = convert.status(lib, opus)
    assert st["pending"] == [] and st["orphans"] == [opus / "Gone.opus"]
    # Nothing left to do on a second run
    assert convert.convert(Context(), lib, opus, 128, 2) == []


def test_loudness_of_converted_lossy(tmp_path, monkeypatch):
    monkeypatch.setitem(SETTINGS, "bitrate", 128)
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    atmos = make(lib / "Band-Atmos.m4a", "Atmos", 384, channels=6)
    convert.convert(Context(), lib, opus, 128, 2)
    before = atmos.read_bytes()

    tracks = [t for t in library.scan(lib) if t.path == atmos]
    assert loudness.tag_library(Context(), tracks, lib, opus) == 1
    # Measured from the stereo Opus (a 440 Hz sine is far from the -18 LUFS reference, so never 0)
    gain = int(OggOpus(opus / "Band-Atmos.opus")["R128_TRACK_GAIN"][0])
    assert gain != 0
    assert atmos.read_bytes() == before  # no ReplayGain written into the .m4a
    assert loudness.tag_library(Context(), library.scan(lib), lib, opus) == 0
