"""Lyrics: LRC parsing and writing, where they're stored, and that they follow songs around."""

import shutil
import subprocess
from pathlib import Path

import pytest

from musictools.core import convert, lyrics, organize
from musictools.core.jobs import Context
from musictools.core.netsync.tools import apply_tag_edits
from musictools.core.tags import read_track

WORDS = """[ar:Band]
[offset:+500]
[00:01.50]<00:01.50>Hello <00:02.00>world <00:02.90>
[00:02.90]
[00:10.50]Second line
[00:12.50][00:20.50]Chorus
"""


def test_parse_words_ends_and_offset():
    doc = lyrics.parse(WORDS)
    assert doc.meta == {"ar": "Band"}
    assert [l.text for l in doc.lines] == ["Hello world", "Second line", "Chorus", "Chorus"]
    hello = doc.lines[0]
    assert (hello.start, hello.end) == (1.0, 2.4)          # the offset moves everything 0.5 s earlier
    assert [(w.text, w.start) for w in hello.words] == [("Hello ", 1.0), ("world", 1.5)]
    assert doc.lines[1].end is None                         # shown until the chorus
    assert [l.start for l in doc.lines[2:]] == [12.0, 20.0]  # a line with two times is two lines
    assert doc.kind == "words"


def test_write_round_trip():
    doc = lyrics.parse(WORDS)
    text = lyrics.to_lrc(doc)
    assert "[00:01.00]<00:01.00>Hello <00:01.50>world<00:02.40>" in text
    assert "[00:02.40]\n" in text                           # the instrumental gap after it
    assert lyrics.to_lrc(lyrics.parse(text)) == text
    assert "<" not in lyrics.to_lrc(doc, words=False)


DUET = """[ti:Song]
[by:Mornye]

[00:01.146]v1:<00:01.146>I'm <00:01.397><00:01.397>freezing<00:01.834>
[00:02.000]v2:<00:02.000>Me <00:02.300><00:02.400>too<00:02.900>
[bg:<00:02.500><00:02.500>(me <00:02.700><00:02.700>too)<00:03.100><00:03.100>]
[00:04.000]v1:Plain line
"""


def test_duets_ad_libs_and_word_ends():
    doc = lyrics.parse(DUET)
    assert doc.precision == 3 and doc.duet and doc.kind == "words"
    one, two, plain = doc.lines
    assert (one.voice, two.voice, plain.voice) == ("v1", "v2", "v1")
    assert [(w.text, w.start, w.end) for w in one.words] == [("I'm ", 1.146, 1.397), ("freezing", 1.397, 1.834)]
    assert two.words[0].end == 2.3 and two.words[1].start == 2.4      # a pause between the words
    assert two.word_end(0) == 2.3 and one.word_end(1) == 1.834
    (bg,) = two.bg
    assert (bg.text, bg.start, bg.end) == ("(me too)", 2.5, 3.1)
    assert not one.bg and plain.words == []


def test_duets_round_trip():
    doc = lyrics.parse(DUET)
    text = lyrics.to_lrc(doc)
    assert "[00:02.000]v2:<00:02.000>Me <00:02.300><00:02.400>too<00:02.900>" in text
    assert "[bg:<00:02.500>(me <00:02.700><00:02.700>too)<00:03.100>]" in text
    assert "[00:04.000]v1:Plain line" in text and "[by:Mornye]" in text
    assert lyrics.parse(text) == doc
    shifted = doc.copy()
    shifted.lines[1].shift(1.0)                                       # ad-libs move along with their line
    assert shifted.lines[1].bg[0].start == 3.5 and shifted.lines[1].words[0].end == 3.3


def test_instrumental():
    doc = lyrics.parse("[instrumental:true]")
    assert doc.instrumental and doc.kind == "instrumental" and doc.text() == ""
    assert lyrics.serialize(doc) == "[instrumental:true]"
    doc.set_text("Some words after all")
    assert not doc.instrumental and doc.kind == "plain"


def test_untimed_lines_are_kept_in_place():
    doc = lyrics.parse("[00:01.00]One\n[00:03.00]Two\n")
    doc.set_text("One\nNew\nTwo")
    text = lyrics.to_lrc(doc)
    assert text == "[00:01.00]One\nNew\n[00:03.00]Two\n"
    assert [l.text for l in lyrics.parse(text).lines] == ["One", "New", "Two"]


def test_editing_text_keeps_singers_and_ad_libs():
    doc = lyrics.parse(DUET)
    doc.set_text("I'm freezing\nMe too, edited\nPlain line")
    assert doc.lines[1].voice == "v2" and doc.lines[1].bg[0].text == "(me too)" and doc.lines[1].start == 2.0


def test_end_mark_only_for_real_gaps():
    doc = lyrics.parse("[00:01.00]One\n[00:03.00]Two\n")
    doc.lines[0].end = 3.0                                  # ends exactly when the next starts: no mark
    assert lyrics.to_lrc(doc) == "[00:01.00]One\n[00:03.00]Two\n"
    doc.lines[1].end = 5.0                                  # the last line ending is always written
    assert lyrics.to_lrc(doc).endswith("[00:03.00]Two\n[00:05.00]\n")


def test_plain_lyrics_keep_their_blank_lines():
    text = "Verse one\nstill verse one\n\nChorus\n"
    doc = lyrics.parse(text)
    assert doc.kind == "plain" and not doc.synced
    assert lyrics.serialize(doc) == text
    assert [l.text for l in doc.lines] == ["Verse one", "still verse one", "Chorus"]


def test_editing_text_keeps_timings():
    doc = lyrics.parse("[00:01.00]Hello world\n[00:10.00]Second line\n[00:12.00]Chorus\n")
    doc.set_text("Hello world\nA new line\nSecond line, edited\nChorus")
    assert [(l.text, l.start) for l in doc.lines] == [
        ("Hello world", 1.0), ("A new line", None), ("Second line, edited", 10.0), ("Chorus", 12.0)]


def test_words_split_per_character_for_cjk():
    assert lyrics.split_words("你好 world, again") == ["你", "好 ", "world, ", "again"]


def test_normalize_keeps_untimed_lines_in_place():
    doc = lyrics.parse("[00:05.00]B\n[00:01.00]A\n")
    doc.lines.insert(1, lyrics.Line("between"))
    doc.lines[0].start, doc.lines[2].start = 9.0, 2.0
    doc.normalize()
    assert [l.text for l in doc.lines] == ["B", "between", "A"]
    assert [l.start for l in doc.lines] == [2.0, None, 9.0]


def test_parse_time():
    assert lyrics.parse_time("1:23.45") == pytest.approx(83.45)
    assert lyrics.parse_time("83.5") == 83.5
    assert lyrics.parse_time("soon") is None
    assert lyrics.stamp(83.456) == "01:23.46"


# --------------------------------------------------------------------- storage

pytestmark_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make(path):
    ext = path.suffix.lstrip(".")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ar", "48000" if ext == "opus" else "44100", "-metadata",
                    "title=Song", str(path)],
                   check=True)
    return path


SYNCED = "[00:00.50]One\n[00:01.20]Two\n"


@pytestmark_ffmpeg
@pytest.mark.parametrize("ext", ["flac", "mp3", "m4a", "opus"])
def test_embedded_in_every_format(tmp_path, ext):
    song = make(tmp_path / f"song.{ext}")
    lyrics.save(song, SYNCED, "embed")
    assert lyrics.read_embedded(song) == SYNCED
    assert lyrics.load(song) == (SYNCED, "embed")
    assert not lyrics.sidecar(song).exists()
    assert read_track(song).lyrics == "lines"
    lyrics.save(song, "", "embed")
    assert lyrics.load(song) == ("", "")
    assert read_track(song).lyrics == ""


@pytestmark_ffmpeg
def test_save_modes_and_export_copy(tmp_path):
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    song = make(lib / "A" / "song.flac")
    convert.convert(Context(), lib, opus, 64, 1)
    copy = opus / "A" / "song.opus"
    assert copy.exists()

    lyrics.save(song, SYNCED, "file", lib, opus)
    assert lyrics.sidecar(song).read_text() == SYNCED and lyrics.read_embedded(song) == ""
    assert lyrics.sidecar(copy).read_text() == SYNCED      # the phone's copy gets them too

    # switching to "in the file": the .lrc that's already there is kept up to date, not left stale
    lyrics.save(song, "Plain now\n", "embed", lib, opus)
    assert lyrics.read_embedded(song) == "Plain now\n" == lyrics.read_sidecar(song)
    assert lyrics.read_embedded(copy) == "Plain now\n"

    lyrics.save(song, "", "embed", lib, opus)               # removing them removes every copy
    assert not lyrics.sidecar(song).exists() and not lyrics.sidecar(copy).exists()
    assert lyrics.read_embedded(song) == lyrics.read_embedded(copy) == ""


@pytestmark_ffmpeg
def test_load_prefers_the_richer_copy(tmp_path):
    song = make(tmp_path / "song.flac")
    lyrics.write_embedded(song, "Just text\n")
    lyrics.write_sidecar(song, SYNCED)
    assert lyrics.load(song) == (SYNCED, "file")
    lyrics.write_embedded(song, WORDS)
    assert lyrics.load(song)[1] == "embed"


@pytestmark_ffmpeg
def test_convert_carries_lyrics(tmp_path):
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    song = make(lib / "song.flac")
    lyrics.write_embedded(song, SYNCED)
    lyrics.write_sidecar(song, SYNCED)
    convert.convert(Context(), lib, opus, 64, 1)
    assert lyrics.read_embedded(opus / "song.opus") == SYNCED
    assert lyrics.read_sidecar(opus / "song.opus") == SYNCED
    lyrics.write_sidecar(song, "[00:00.50]Changed\n")      # later edits reach the export on the next run
    convert.convert(Context(), lib, opus, 64, 1)
    assert lyrics.read_sidecar(opus / "song.opus") == "[00:00.50]Changed\n"


@pytestmark_ffmpeg
def test_renaming_moves_lrc_files(tmp_path):
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    song = make(lib / "old.flac")
    lyrics.write_sidecar(song, SYNCED)
    convert.convert(Context(), lib, opus, 64, 1)
    organize.apply_moves(Context(), [(song, lib / "new.flac")], False, lib, opus)
    assert lyrics.read_sidecar(lib / "new.flac") == SYNCED and not lyrics.sidecar(song).exists()
    assert lyrics.read_sidecar(opus / "new.opus") == SYNCED and not (opus / "old.lrc").exists()


@pytestmark_ffmpeg
def test_lyrics_edit_from_a_follower(tmp_path):
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    song = make(lib / "song.flac")
    res = apply_tag_edits(lib, opus, song, {"lyrics": ("", SYNCED)}, lyrics_mode="both")
    assert res == {"applied": ["lyrics"], "conflicts": {}}
    assert lyrics.read_embedded(song) == SYNCED == lyrics.read_sidecar(song)
    # changed on the main PC in the meantime: a conflict, nothing written
    res = apply_tag_edits(lib, opus, song, {"lyrics": ("Something else\n", "Mine\n")})
    assert res["conflicts"] == {"lyrics": SYNCED} and lyrics.load(song)[0] == SYNCED


def test_follower_keeps_unsent_lrc_files(tmp_path):
    from musictools.core.netsync.follow import Follower
    from musictools.core.util import DEFAULTS, Settings
    s = Settings(DEFAULTS)
    s.update(library=str(tmp_path / "lib"), opus=str(tmp_path / "opus"), role="follower")
    f = Follower(s, path=tmp_path / "follow.json")
    f.record_tag_edit(tmp_path / "lib" / "A" / "song.flac", {"lyrics": ""}, {"lyrics": SYNCED})
    assert f.protected() == {("library", "A/song.flac"), ("library", "A/song.lrc")}


def test_follower_says_when_the_main_pc_ignores_lyrics(tmp_path):
    """An older Music Tools on the main PC skips fields it doesn't know without an error."""
    from musictools.core.netsync.follow import Follower
    from musictools.core.util import DEFAULTS, Settings
    s = Settings(DEFAULTS)
    s.update(library=str(tmp_path / "lib"), opus=str(tmp_path / "opus"), role="follower",
             follow_new_music=str(tmp_path / "new"))
    f = Follower(s, path=tmp_path / "follow.json")
    f.record_tag_edit(tmp_path / "lib" / "song.flac", {"lyrics": "", "title": "Old"},
                      {"lyrics": SYNCED, "title": "New"})

    class OldMain:
        def edit_tags(self, rel, changes, root, force=False):
            return {"applied": ["title"], "conflicts": {}}

    logged = []
    ctx = Context()
    ctx.log = lambda msg, level="info": logged.append((level, msg))
    f.send(ctx, OldMain(), files=[])
    (edit,) = f.state["edits"]
    assert list(edit["changes"]) == ["lyrics"] and edit["ignored"] == ["lyrics"]
    assert any(level == "warn" and "Update Music Tools there" in msg for level, msg in logged)


def test_confident_matches():
    r = {"artist": "K/DA, Madison Beer", "title": "VILLAIN", "duration": 199.0}
    assert lyrics.confident(r, "K/DA & Madison Beer", "VILLAIN (feat. Kim Petras)", 199.1)
    assert not lyrics.confident(r, "K/DA", "VILLAIN", 230)              # another version
    assert not lyrics.confident(r, "Someone Else", "VILLAIN", 199)
    assert not lyrics.confident(r, "K/DA", "Villains Anthem", 199)


class _Track:
    def __init__(self, path, artist, title, duration=120):
        self.path, self.artist, self.album, self.duration = path, artist, "", duration
        self._title = title
        self.display = f"{artist} – {title}"

    def tag(self, key):
        return self._title if key == "title" else ""


def _result(artist, title, synced="", plain="", instrumental=False, duration=120):
    return {"artist": artist, "title": title, "album": "", "duration": duration, "synced": synced, "plain": plain,
            "instrumental": instrumental}


@pytestmark_ffmpeg
def test_fetch_many(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics, "backup_dir", lambda: tmp_path / "backups")
    lib = tmp_path / "lib"
    a, b, c, d, e = (make(lib / f"{n}.flac") for n in "abcde")
    lyrics.write_embedded(b, "Old plain words\n")
    lyrics.write_embedded(e, WORDS)
    db = {"A": [_result("Band", "A", synced=SYNCED)],
          "B": [_result("Band", "B", synced=SYNCED)],
          "C": [_result("Other band", "Something else", plain="x")],           # not the song
          "D": [_result("Band", "D", instrumental=True)],
          "E": [_result("Band", "E", synced=SYNCED)]}                          # would be a step down
    tracks = [_Track(p, "Band", p.stem.upper()) for p in (a, b, c, d, e)]
    history = lyrics.History(tmp_path / "history.json")
    saved = []
    counts = lyrics.fetch_many(Context(), tracks, "unsynced", "embed", lib, None, history,
                               on_saved=lambda p, old, new: saved.append(p.name),
                               search=lambda artist, title, album, dur: db[title])
    assert counts == {"added": 1, "replaced": 1, "instrumental": 1, "unsure": 1, "notfound": 0, "error": 0}
    assert lyrics.read_embedded(a) == SYNCED == lyrics.read_embedded(b)
    assert lyrics.read_embedded(c) == ""                                     # unsure: not saved, but listed
    assert lyrics.load(d)[0] == lyrics.INSTRUMENTAL
    assert lyrics.read_embedded(e) == WORDS
    assert sorted(saved) == ["a.flac", "b.flac", "d.flac"]
    replaced = next(i for i in history.items if i["outcome"] == "replaced")
    assert Path(replaced["backup"]).read_text() == "Old plain words\n"
    unsure = next(i for i in lyrics.History(tmp_path / "history.json").items if i["outcome"] == "unsure")
    assert unsure["text"] == "x" and "Other band" in unsure["match"]


@pytestmark_ffmpeg
def test_relocate(tmp_path):
    lib, opus = tmp_path / "lib", tmp_path / "opus"
    song = make(lib / "song.flac")
    convert.convert(Context(), lib, opus, 64, 1)
    lyrics.write_embedded(song, SYNCED)
    assert lyrics.relocate(song, "file", False, lib, opus)
    assert lyrics.read_embedded(song) == "" and lyrics.read_sidecar(song) == SYNCED
    assert lyrics.read_sidecar(opus / "song.opus") == SYNCED
    assert lyrics.relocate(song, "embed", True, lib, opus)
    assert lyrics.read_embedded(song) == SYNCED == lyrics.read_sidecar(song)
    assert not lyrics.relocate(make(lib / "empty.flac"), "file")


def test_lrclib_is_asked_gently(monkeypatch):
    import io
    import urllib.error
    from musictools.core import art
    calls, sleeps = [], []
    replies = [urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "20"}, io.BytesIO()),
               b'{"id": 1, "artistName": "Band", "trackName": "Song", "duration": 120, "syncedLyrics": "x"}']

    def fake_get(url, timeout=20):
        calls.append(url)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(art, "_get", fake_get)
    monkeypatch.setattr(lyrics, "_pause", lambda secs, ctx=None: sleeps.append(round(secs)))
    found = lyrics.search_online("Band", "Song", "", 120, quick=True)
    assert [f["synced"] for f in found] == ["x"]
    assert len(calls) == 2 and "/get?" in calls[0] and "/get?" in calls[1]   # found exactly: no search
    assert 20 in sleeps                                                    # waited as long as it asked


@pytestmark_ffmpeg
def test_fetch_many_skips_recent_misses(tmp_path):
    song = make(tmp_path / "a.flac")
    history = lyrics.History(tmp_path / "history.json")
    asked = []

    def search(*a):
        asked.append(a)
        return []

    lyrics.fetch_many(Context(), [_Track(song, "Band", "A")], history=history, search=search)
    lyrics.fetch_many(Context(), [_Track(song, "Band", "A")], history=history, search=search)
    assert len(asked) == 1


@pytestmark_ffmpeg
def test_fetch_many_lists_failures_and_stops(tmp_path):
    songs = [make(tmp_path / f"{n}.flac") for n in "abcd"]
    history = lyrics.History(tmp_path / "history.json")

    def offline(*a):
        raise OSError("Network is unreachable")

    counts = lyrics.fetch_many(Context(), [_Track(p, "Band", p.stem) for p in songs], history=history,
                               search=offline)
    assert counts["error"] == 3 and "couldn't be reached" in counts["stopped"]     # gives up after 3 in a row
    assert [i["outcome"] for i in history.items] == ["error"] * 3
    assert "Network is unreachable" in history.items[0]["match"]

    def limited(*a):
        raise lyrics.TooManyRequests("LRCLIB says we're asking too often.")

    counts = lyrics.fetch_many(Context(), [_Track(p, "Band", p.stem) for p in songs], history=history,
                               search=limited)
    assert counts["error"] == 1 and counts["stopped"].startswith("LRCLIB says")    # failed ones are retried
