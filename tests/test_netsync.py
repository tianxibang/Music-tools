"""Sync server: pairing and its security checks, the manifest, downloads and the phone inbox round trip."""

import hashlib
import secrets
import shutil
import subprocess
import time
from urllib.parse import quote

import pytest

from musictools.core.netsync import security
from musictools.core.netsync.client import Client, SyncError
from musictools.core.netsync.library import safe_join
from musictools.core.netsync.server import SyncServer, is_local
from musictools.core.util import DEFAULTS, Settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    settings = Settings(DEFAULTS)
    settings.update(library=str(tmp_path / "lib"), opus=str(tmp_path / "opus"), net_require_pairing=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "opus" / "Artist").mkdir(parents=True)
    (tmp_path / "opus" / "Artist" / "Song.opus").write_bytes(b"opus data " * 1000)
    server = SyncServer(settings, log=lambda *a: None, port=0)
    monkeypatch.setattr("musictools.core.netsync.discovery.Advertiser.start", lambda self: None)
    server.start()
    yield server, tmp_path
    server.stop()


def client(server, **kw):
    return Client("127.0.0.1", server.port, secrets.token_hex(16), name="Test phone", kind="phone", **kw)


def paired(server):
    c = client(server)
    c.pair(server.start_pairing().code)
    return c


def test_pairing_gives_working_token(env):
    server, _ = env
    c = paired(server)
    assert c.token and c.fingerprint == server.identity.fingerprint
    assert [f["path"] for f in c.manifest()] == ["Artist/Song.opus"]
    # the token is stored only as a hash
    assert c.token not in server.registry.path.read_text()


def test_code_can_be_typed_loosely(env):
    server, _ = env
    code = server.start_pairing().code
    client(server).pair(security.format_code(code).lower())


def test_no_access_without_pairing(env):
    server, _ = env
    with pytest.raises(SyncError) as e:
        client(server).manifest()
    assert e.value.status == 401
    with pytest.raises(SyncError) as e:
        client(server, token="made-up").manifest()
    assert e.value.status == 401


def test_pairing_needs_a_session_and_the_right_code(env):
    server, _ = env
    with pytest.raises(SyncError) as e:
        client(server).pair("0000-0000-0000")
    assert e.value.status == 403
    session = server.start_pairing()
    with pytest.raises(SyncError):
        client(server).pair("0000-0000-0000")
    assert session.attempts == 1
    client(server).pair(session.code)
    with pytest.raises(SyncError):  # codes are single-use
        client(server).pair(session.code)


def test_code_locks_after_too_many_attempts(env, monkeypatch):
    server, _ = env
    monkeypatch.setattr("musictools.core.netsync.server.time.sleep", lambda s: None)
    session = server.start_pairing()
    for _ in range(security.MAX_ATTEMPTS):
        with pytest.raises(SyncError):
            client(server).pair("0000-0000-0000")
    with pytest.raises(SyncError):
        client(server).pair(session.code)


def test_code_expires(env):
    server, _ = env
    session = server.start_pairing()
    session.expires = time.time() - 1
    with pytest.raises(SyncError):
        client(server).pair(session.code)


def test_proof_bound_to_certificate():
    """A man in the middle presents its own certificate, so the phone's proof can't be replayed to the PC."""
    session = security.PairingSession()
    real, mitm, nonce, dev = "aa" * 32, "bb" * 32, "cd" * 16, "ef" * 16
    proof_for_mitm = security.client_proof(session.code, mitm, nonce, dev)
    assert not session.check(proof_for_mitm, real, nonce, dev)
    assert session.check(security.client_proof(session.code, real, nonce, dev), real, nonce, dev)


def test_pinned_fingerprint_mismatch_is_refused(env):
    server, _ = env
    c = paired(server)
    c.fingerprint = "00" * 32
    with pytest.raises(SyncError, match="certificate changed"):
        c.manifest()


def test_revoked_device_loses_access(env):
    server, _ = env
    c = paired(server)
    server.registry.remove(c.device_id)
    with pytest.raises(SyncError) as e:
        c.manifest()
    assert e.value.status == 401


def test_open_mode_skips_pairing(env):
    server, _ = env
    server.settings["net_require_pairing"] = False
    assert client(server).manifest()


def test_downloads_stay_inside_export(env):
    server, tmp = env
    c = paired(server)
    assert c.request("GET", "files/Artist/Song.opus", raw=True) == b"opus data " * 1000
    (tmp / "secret.txt").write_text("nope")
    for bad in ("../secret.txt", "..%2Fsecret.txt", "%2E%2E/secret.txt", "/etc/passwd", "Artist/../../secret.txt"):
        with pytest.raises(SyncError) as e:
            c.request("GET", "files/" + bad, raw=True)
        assert e.value.status == 404, bad
    assert safe_join(tmp / "opus", "../secret.txt") is None
    assert safe_join(tmp / "opus", "Artist/Song.opus") == (tmp / "opus" / "Artist" / "Song.opus").resolve()


def test_range_download(env):
    server, _ = env
    c = paired(server)
    data = c.request("GET", "files/Artist/Song.opus", headers={"Range": "bytes=5-14"}, raw=True)
    assert data == (b"opus data " * 1000)[5:15]


def test_only_local_network():
    for ip in ("192.168.1.20", "10.0.0.5", "172.16.3.3", "127.0.0.1", "::1", "fe80::1", "::ffff:192.168.0.2"):
        assert is_local(ip), ip
    for ip in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "::ffff:8.8.8.8", "nonsense"):
        assert not is_local(ip), ip


def test_upload_rejects_bad_files(env):
    server, tmp = env
    c = paired(server)
    bad = tmp / "song.mp3"
    bad.write_bytes(b"x" * 100)
    with pytest.raises(SyncError, match="Only FLAC"):
        c.upload(bad)
    flac = tmp / "song.flac"
    flac.write_bytes(b"x" * 100)
    with pytest.raises(SyncError, match="Checksum"):
        c.request("PUT", "inbox?name=song.flac&sha256=" + "0" * 64, body=flac.read_bytes())


needs_tools = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def make_flac(path, title="New Song", freq=440):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration=3", "-metadata", f"title={title}", "-metadata", "artist=Tester",
                    "-metadata", "albumartist=Tester", "-metadata", "album=Phone Finds", "-metadata", "track=1",
                    str(path)], check=True)
    return path


def wait_done(c, item_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        item = c.inbox(item_id)
        if item["state"] in ("ready", "duplicate", "failed"):
            return item
        time.sleep(0.2)
    raise AssertionError("inbox item never finished")


@needs_tools
def test_phone_flac_round_trip(env):
    server, tmp = env
    c = paired(server)
    flac = make_flac(tmp / "from phone.flac")
    item = wait_done(c, c.upload(flac)["id"])
    assert item["state"] == "ready", item
    # filed with the sort template, original kept on the PC
    assert item["library_path"] == "Tester/Phone Finds/01 - New Song.flac"
    lib_file = tmp / "lib" / item["library_path"]
    assert hashlib.sha256(lib_file.read_bytes()).digest() == hashlib.sha256(flac.read_bytes()).digest()
    # the Opus is in the manifest and can be downloaded
    assert item["opus_path"] in {f["path"] for f in c.manifest()}
    opus = c.request("GET", "files/" + quote(item["opus_path"]), raw=True)
    assert opus[:4] == b"OggS"
    c.acknowledge(item["id"])
    assert c.inbox()["items"] == []

    # The same music again (even with other tags) is recognised as a duplicate, not imported twice
    again = make_flac(tmp / "copy.flac", title="Other Title")
    item = wait_done(c, c.upload(again)["id"])
    assert item["state"] == "duplicate"
    assert item["library_path"] == "Tester/Phone Finds/01 - New Song.flac"
    assert len(list((tmp / "lib").rglob("*.flac"))) == 1


@needs_tools
def test_inbox_items_are_private_to_each_device(env):
    server, tmp = env
    a, b = paired(server), paired(server)
    item = a.upload(make_flac(tmp / "x.flac"))
    with pytest.raises(SyncError) as e:
        b.inbox(item["id"])
    assert e.value.status == 404
    with pytest.raises(SyncError):
        b.acknowledge(item["id"])
