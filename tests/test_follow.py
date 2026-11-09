"""Follower mode: a second computer mirroring the main PC, and sending new music and tag edits back."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from musictools.core import convert, tags
from musictools.core.jobs import Context
from musictools.core.netsync.follow import Follower
from musictools.core.netsync.server import SyncServer
from musictools.core.util import DEFAULTS, Settings

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make(path, title, freq, artist="Band"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration=2", "-ar", "44100", "-metadata", f"title={title}",
                    "-metadata", f"artist={artist}", "-metadata", "genre=Rock", str(path)], check=True)
    return path


class Asking(Context):
    def __init__(self, answer):
        super().__init__()
        self.answer, self.asked = answer, []

    def log(self, msg, level="info"):
        pass

    def ask(self, title, msg):
        self.asked.append(msg)
        return self.answer


@pytest.fixture
def pcs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("musictools.core.netsync.discovery.Advertiser.start", lambda self: None)
    main = Settings(DEFAULTS)
    main.update(library=str(tmp_path / "main/lib"), opus=str(tmp_path / "main/opus"), sort_template="{artist}-{title}",
                jobs=2)
    for i, title in enumerate(["One", "Two", "Three"]):
        make(main.library / f"Band-{title}.flac", title, 300 + i * 50)
    convert.convert(Context(), main.library, main.opus, 64, 2)
    trash = tmp_path / "trash"
    trash.mkdir()

    def to_trash(p):
        shutil.move(str(p), str(trash / f"{time.time_ns()}-{p.name}"))
        return True

    server = SyncServer(main, log=lambda *a: None, port=0, trash=to_trash)
    server.start()
    laptop = Settings(DEFAULTS)
    laptop.update(library=str(tmp_path / "laptop/lib"), opus=str(tmp_path / "laptop/opus"), role="follower",
                  follow_get="both", follow_new_music=str(tmp_path / "laptop/New music"))
    f = Follower(laptop, path=tmp_path / "follow.json", trash=to_trash)
    f.pair("127.0.0.1", server.port, server.start_pairing().code)
    yield main, laptop, f, server
    server.stop()


def files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_mirror_and_follow_changes(pcs):
    main, laptop, f, _ = pcs
    res = f.sync(Context())
    assert res["got"] == 6  # 3 FLACs + 3 Opus
    assert files(laptop.library) == files(main.library) and files(laptop.opus) == files(main.opus)
    assert f.sync(Context())["got"] == 0  # nothing changed

    (laptop.library / "my own notes.flac").write_bytes(b"x")  # not from the main PC: never touched
    (main.library / "Band-One.flac").unlink()
    make(main.library / "Band-Four.flac", "Four", 600)
    res = f.sync(Context())
    assert (res["got"], res["removed"]) == (1, 1)
    assert "Band-One.flac" not in files(laptop.library) and "Band-Four.flac" in files(laptop.library)
    assert (laptop.library / "my own notes.flac").exists()


def test_existing_copies_are_adopted(pcs):
    main, laptop, f, _ = pcs
    shutil.copytree(main.library, laptop.library)  # e.g. copied over with a USB stick before
    laptop.update(follow_get="flac")
    assert f.sync(Context())["got"] == 0


def test_big_removals_ask_first(pcs):
    main, laptop, f, _ = pcs
    laptop.update(follow_get="flac")
    for i in range(60):
        make(main.library / f"Band-Extra {i}.flac", f"Extra {i}", 700 + i)
    f.sync(Context())
    for p in main.library.glob("Band-Extra*"):
        p.unlink()
    ctx = Asking(False)
    assert f.sync(ctx)["removed"] == 0 and ctx.asked
    assert f.sync(Asking(True))["removed"] == 60


def test_new_music_goes_through_the_main_pc(pcs):
    main, laptop, f, _ = pcs
    new = make(Path(laptop["follow_new_music"]) / "found.flac", "Found", 800)
    assert f.new_music() == [new]
    f.sync(Context(), send=True)
    assert (main.library / "Band-Found.flac").exists()  # filed on the main PC with its template
    assert (main.opus / "Band-Found.opus").exists()
    assert not new.exists() and f.new_music() == []  # sent: the local copy went to the trash
    assert (laptop.library / "Band-Found.flac").exists()  # and came back with the mirror


def test_tag_edits_and_conflicts(pcs):
    main, laptop, f, _ = pcs
    f.sync(Context())
    song = laptop.library / "Band-Two.flac"
    # Edit on the laptop: recorded, and protected from being overwritten until it's sent
    old = tags.read_track(song).tags
    tags.write_tags(song, {"title": "Two (new name)"})
    f.record_tag_edit(song, old, {"title": "Two (new name)"})
    (main.library / "Band-Two.flac").touch()  # the main PC's file changes meanwhile
    f.sync(Context(), send=False)
    assert tags.read_track(song).tag("title") == "Two (new name)"

    f.sync(Context(), send=True)
    assert tags.read_track(main.library / "Band-Two.flac").tag("title") == "Two (new name)"
    assert tags.read_track(main.opus / "Band-Two.opus").tag("title") == "Two (new name)"  # Opus copy too
    assert f.state["edits"] == []

    # The same field changed on both computers: not overwritten, reported as a conflict
    tags.write_tags(main.library / "Band-Three.flac", {"genre": "Metal"})
    song = laptop.library / "Band-Three.flac"
    f.record_tag_edit(song, {"genre": "Rock"}, {"genre": "Jazz"})
    res = f.send(Context(), edit_ids={f.state["edits"][0]["id"]})
    assert res["conflicts"] == 1 and f.state["edits"][0]["conflicts"] == {"genre": "Metal"}
    assert tags.read_track(main.library / "Band-Three.flac").tag("genre") == "Metal"
    # You decide: keep yours
    f.send(Context(), edit_ids={f.state["edits"][0]["id"]}, force={f.state["edits"][0]["id"]})
    assert tags.read_track(main.library / "Band-Three.flac").tag("genre") == "Jazz"
    assert f.state["edits"] == []


def test_edit_changed_back_is_dropped(pcs):
    _, laptop, f, _ = pcs
    f.sync(Context())
    song = laptop.library / "Band-One.flac"
    f.record_tag_edit(song, {"title": "One"}, {"title": "Uno"})
    f.record_tag_edit(song, {"title": "Uno"}, {"title": "One"})
    assert f.state["edits"] == []


def test_wrong_main_certificate(pcs):
    _, _, f, _ = pcs
    f.state["main"]["fingerprint"] = "00" * 32
    with pytest.raises(Exception, match="certificate"):
        f.sync(Context())
