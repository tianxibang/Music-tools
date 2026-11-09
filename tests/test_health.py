"""Duplicate detection: copies vs. other versions of the same song, and cleaning up playlists after removal."""

from pathlib import Path

from musictools.core import health, playlists
from musictools.core.tags import Track


def track(name, title, duration=200.0, artist="Caligula's Horse", bits=16):
    return Track(path=Path(f"/lib/{name}.flac"), duration=duration, bits=bits, sample_rate=44100,
                 tags={"artist": artist, "title": title})


def kinds(tracks):
    return {i.path.stem: i.kind for i in health.duplicate_issues(tracks)}


def test_demo_is_another_version_not_a_copy():
    # The case that went wrong: the demo was treated as a copy and the album version got trashed
    album, demo = track("Turntail", "Turntail", 270), track("Turntail demo", "Turntail (Pre-Production Demo 2015)",
                                                          271, bits=24)
    assert kinds([album, demo]) == {"Turntail": "Possible duplicate", "Turntail demo": "Possible duplicate"}
    exact, possible = health.duplicate_sets([album, demo])
    assert exact == [] and len(possible) == 1


def test_real_copies_are_exact():
    a, b = track("a", "Marigold"), track("b", "Marigold", 201, bits=24)
    exact, possible = health.duplicate_sets([a, b])
    assert exact == [[b, a]] and possible == []  # best (24-bit) copy first


def test_remasters_and_features_are_the_same_recording():
    a, b = track("a", "Song (2011 Remaster)"), track("b", "Song (feat. Someone)")
    assert set(kinds([a, b]).values()) == {"Duplicate"}


def test_live_and_different_lengths_are_only_possible():
    studio, live, longer = track("s", "Song"), track("l", "Song (Live at Wembley)"), track("x", "Song", 260)
    exact, possible = health.duplicate_sets([studio, live, longer])
    assert exact == []
    assert len(possible) == 1 and len(possible[0]) == 3


def test_copies_inside_a_group_with_other_versions():
    a, b, live = track("a", "Song"), track("b", "Song", 199), track("l", "Song (Live)")
    exact, possible = health.duplicate_sets([a, b, live])
    assert [sorted(t.path.stem for t in s) for s in exact] == [["a", "b"]]
    assert len(possible) == 1  # the live version is offered for review, unticked


def test_drop_removes_deleted_files_from_playlists(tmp_path):
    keep, gone = tmp_path / "keep.flac", tmp_path / "gone.flac"
    for f in (keep, gone):
        f.write_bytes(b"x")
    pl = playlists.new_path(tmp_path, "Mix")
    playlists.save(pl, [keep, gone])
    assert playlists.drop(tmp_path, [gone]) == 1
    assert playlists.load(pl) == [keep.resolve()]
    assert playlists.drop(tmp_path, [gone]) == 0
