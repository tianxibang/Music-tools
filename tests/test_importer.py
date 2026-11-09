"""Importing from a drive: what gets added, upgraded or skipped, and what happens to the library."""

import shutil
import subprocess

import pytest

from musictools.core import convert, importer, library, tags
from musictools.core.jobs import Context
from musictools.core.util import DEFAULTS, Settings

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make(path, title, artist="Band", freq=440, rate=44100, fmt="s16", extra=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["-ar", str(rate)]
    if path.suffix == ".flac":
        args += ["-sample_fmt", fmt]
    elif path.suffix == ".mp3":
        args += ["-b:a", "128k"]
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration=3", *args, "-metadata", f"title={title}",
                    "-metadata", f"artist={artist}", *extra, str(path)], check=True)
    return path


@pytest.fixture
def setup(tmp_path):
    lib, opus, drive, trash = (tmp_path / n for n in ("lib", "opus", "drive", "trash"))
    trash.mkdir()
    settings = Settings(DEFAULTS)
    settings.update(library=str(lib), opus=str(opus), sort_template="{artist}-{title}", jobs=2)
    # The library: a curated FLAC and an MP3 in the Lossy folder
    make(lib / "Band-Song A.flac", "Song A", extra=["-metadata", "album=Real Album", "-metadata", "genre=Rock"])
    make(lib / "Lossy" / "Band-Song B.mp3", "Song B", freq=500)
    convert.convert(Context(), lib, opus, 64, 2)

    def to_trash(p):
        shutil.move(str(p), str(trash / p.name))
        return True
    return settings, drive, trash, to_trash


def plan_for(settings, drive):
    return {c.src.path.name: c for c in importer.plan(Context(), library.scan(drive),
                                                      library.scan(settings.library))}


def test_every_kind_of_file(setup):
    settings, drive, _, _ = setup
    lib = settings.library
    drive.mkdir()
    shutil.copy(lib / "Band-Song A.flac", drive / "renamed.flac")
    tags.write_tags(drive / "renamed.flac", {"title": "Totally Different Title"})  # same audio, other tags
    make(drive / "a.mp3", "Song A")                                       # worse copy of a-hires on the drive
    make(drive / "a-hires.flac", "Song A", rate=96000, fmt="s32",                     # better than our FLAC
         extra=["-metadata", "album=Wrong Album"])
    make(drive / "b.flac", "Song B", freq=500)                                        # FLAC of our MP3
    make(drive / "c.flac", "Song C", freq=600)                                        # new
    make(drive / "c.mp3", "Song C", freq=600)                                         # worse copy on the drive
    make(drive / "d.mp3", "Song D", freq=700)                                         # new, lossy
    make(drive / "a-live.flac", "Song A (Live)", freq=800)                            # other version
    make(drive / "e.wav", "Song E", freq=900)                                         # new, lossless but WAV
    (drive / "junk.flac").write_bytes(b"not audio at all")
    p = plan_for(settings, drive)
    got = {name: (c.action, c.tick) for name, c in p.items()}
    assert got == {
        "renamed.flac": ("same", False),
        "a.mp3": ("dupe", False),
        "a-hires.flac": ("upgrade", True),
        "b.flac": ("upgrade", True),
        "c.flac": ("new", True),
        "c.mp3": ("dupe", False),
        "d.mp3": ("lossy", True),
        "a-live.flac": ("version", False),
        "e.wav": ("new", True),
        "junk.flac": ("broken", False),
    }
    assert p["a-hires.flac"].reason == "FLAC 16-bit / 44.1 kHz → FLAC 24-bit / 96 kHz"


def test_apply(setup):
    settings, drive, trash, to_trash = setup
    lib, opus = settings.library, settings.opus
    drive.mkdir()
    make(drive / "a-hires.flac", "Song A", rate=96000, fmt="s32", extra=["-metadata", "album=Wrong Album",
                                                                           "-metadata", "date=2020"])
    make(drive / "b.flac", "Song B", freq=500)
    make(drive / "c.flac", "Song C", freq=600)
    make(drive / "d.mp3", "Song D", freq=700)
    make(drive / "e.wav", "Song E", freq=900)
    make(drive / "a-live.flac", "Song A (Live)", freq=800)
    before = {f: f.read_bytes() for f in drive.iterdir()}
    cands = importer.plan(Context(), library.scan(drive), library.scan(lib))
    counts, failed = importer.apply(Context(), cands, settings, to_trash)
    assert failed == 0 and counts == {"upgrade": 2, "new": 2, "lossy": 1}

    # Upgrade: same place, better audio, the library's tags kept, gaps filled from the drive
    a = tags.read_track(lib / "Band-Song A.flac")
    assert a.sample_rate == 96000
    assert a.tag("album") == "Real Album" and a.tag("genre") == "Rock" and a.tag("date") == "2020"
    # Lossy → lossless upgrade leaves the Lossy folder; the MP3 and its export copy are in the trash
    assert (lib / "Band-Song B.flac").exists() and not (lib / "Lossy" / "Band-Song B.mp3").exists()
    assert {p.name for p in trash.iterdir()} >= {"Band-Song B.mp3", "Band-Song A.flac"}
    # New songs: lossless in the library (WAV became FLAC), lossy in Lossy/, other version not ticked
    assert (lib / "Band-Song C.flac").exists() and (lib / "Band-Song E.flac").exists()
    assert (lib / "Lossy" / "Band-Song D.mp3").exists()
    assert not any("Live" in p.name for p in lib.rglob("*"))
    # The phone side: FLACs converted, the MP3 copied as is, no staging left over
    assert {p.relative_to(opus).as_posix() for p in opus.rglob("*") if p.is_file()} == {
        "Band-Song A.opus", "Band-Song B.opus", "Band-Song C.opus", "Band-Song E.opus", "Lossy/Band-Song D.mp3"}
    assert not any(p.name.startswith(".import-tmp") for p in lib.iterdir())
    # The drive is untouched
    assert {f: f.read_bytes() for f in drive.iterdir()} == before


def test_ticked_other_version_is_added_as_its_own_song(setup):
    settings, drive, _, to_trash = setup
    drive.mkdir()
    make(drive / "a-live.flac", "Song A (Live)", freq=800)
    cands = importer.plan(Context(), library.scan(drive), library.scan(settings.library))
    cands[0].tick = True
    importer.apply(Context(), cands, settings, to_trash)
    assert (settings.library / "Band-Song A (Live).flac").exists()
    assert (settings.library / "Band-Song A.flac").exists()  # the original stays


def test_worse_than_library(setup):
    settings, drive, _, _ = setup
    drive.mkdir()
    make(drive / "a.mp3", "Song A")
    c = plan_for(settings, drive)["a.mp3"]
    assert (c.action, c.tick) == ("worse", False) and c.reason == "You have FLAC 16-bit / 44.1 kHz"
