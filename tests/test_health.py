"""Duplicate detection: copies vs. other versions of the same song, and cleaning up playlists after removal."""

from pathlib import Path

from musictools.core import health, playlists
from musictools.core.tags import Track


def track(name, title, duration=200.0, artist="Caligula's Horse", bits=16):
    return Track(path=Path(f"/lib/{name}.flac"), duration=duration, bits=bits, sample_rate=44100,
                 tags={"artist": artist, "title": title})


def notes(tracks):
    """{file: (note, suggested for removal)} for the first duplicate group."""
    [group] = health.duplicate_groups(tracks)
    return {f.track.path.stem: (f.note, f.remove) for f in group}


def kinds(tracks):
    return {i.path.stem: i.kind for i in health.duplicate_issues(tracks)}


def test_demo_is_another_version_not_a_copy():
    # The case that went wrong: the demo was treated as a copy and the album version got trashed
    album, demo = track("Turntail", "Turntail", 270), track("Turntail demo", "Turntail (Pre-Production Demo 2015)",
                                                          271, bits=24)
    assert kinds([album, demo]) == {"Turntail": "Possible duplicate", "Turntail demo": "Possible duplicate"}
    assert notes([album, demo]) == {"Turntail": ("Original", False),
                                    "Turntail demo": ("Other version: demo", False)}


def test_real_copies_are_exact():
    a, b = track("a", "Marigold"), track("b", "Marigold", 201, bits=24)
    assert notes([a, b]) == {"b": ("Best copy (kept)", False), "a": ("Copy", True)}  # 24-bit kept


def test_remasters_and_features_are_the_same_recording():
    a, b = track("a", "Song (2011 Remaster)"), track("b", "Song (feat. Someone)")
    assert set(kinds([a, b]).values()) == {"Duplicate"}


def test_live_and_different_lengths_are_only_possible():
    studio, live, longer = track("s", "Song"), track("l", "Song (Live at Wembley)"), track("x", "Song", 260)
    n = notes([studio, live, longer])
    assert n["l"] == ("Other version: live", False)
    assert not any(remove for _, remove in n.values())


def test_copies_and_other_versions_are_one_group():
    # "Ain't It Fun": two copies plus a live version, every file listed exactly once
    a, b, live = track("a", "Song"), track("b", "Song", 199, bits=24), track("l", "Song (Live)")
    groups = health.duplicate_groups([a, b, live])
    assert len(groups) == 1 and len(groups[0]) == 3
    assert notes([a, b, live]) == {"b": ("Best copy (kept)", False), "a": ("Copy", True),
                                   "l": ("Other version: live", False)}


def test_songs_with_copies_come_first():
    versions_only = [track("o", "Alpha", artist="A"), track("od", "Alpha (Demo)", artist="A")]
    copies = [track("c1", "Zulu", artist="Z"), track("c2", "Zulu", artist="Z")]
    groups = health.duplicate_groups(versions_only + copies)
    assert groups[0][0].track.title == "Zulu"


def test_drop_removes_deleted_files_from_playlists(tmp_path):
    keep, gone = tmp_path / "keep.flac", tmp_path / "gone.flac"
    for f in (keep, gone):
        f.write_bytes(b"x")
    pl = playlists.new_path(tmp_path, "Mix")
    playlists.save(pl, [keep, gone])
    assert playlists.drop(tmp_path, [gone]) == 1
    assert playlists.load(pl) == [keep.resolve()]
    assert playlists.drop(tmp_path, [gone]) == 0
