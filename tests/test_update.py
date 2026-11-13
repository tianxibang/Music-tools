"""Finding a newer release, checking downloads, and swapping in a portable update."""

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time

import pytest

from musictools.core import update
from musictools.core.jobs import Context


def test_versions():
    assert update.parse_version("v4.1.1") == (4, 1, 1)
    assert update.newer("v4.2", "4.1.1") and update.newer("v4.1.2", "4.1.1") and update.newer("v10.0", "9.9")
    assert not update.newer("v4.1.1", "4.1.1") and not update.newer("v4.1", "4.1.1")
    assert not update.newer("v4.2.0", "4.2") and not update.newer("nightly", "4.1")


def release(*names):
    return {"tag": "v9.0", "version": "9.0", "assets": {n: (f"https://x/{n}", 1, "") for n in names}}


def test_asset_for_each_install():
    rel = release(*update.ASSETS.values())
    assert update.asset_for(rel, "installer", "win32")[0] == "MusicTools-setup-windows-x64.exe"
    assert update.asset_for(rel, "portable", "win32")[0] == "MusicTools-windows-x64.zip"
    assert update.asset_for(rel, "rpm", "linux")[0] == "MusicTools-linux-x64.rpm"
    assert update.asset_for(rel, "deb", "linux")[0] == "MusicTools-linux-x64.deb"
    assert update.asset_for(rel, "source", "linux") is None
    # An older release without packages: nothing to install
    assert update.asset_for(release("MusicTools-linux-x64.tar.gz"), "rpm", "linux") is None


def test_install_kind(tmp_path):
    assert update.install_kind(tmp_path) == "portable"
    (tmp_path / "install-kind").write_text("rpm\n")
    assert update.install_kind(tmp_path) == "rpm"
    (tmp_path / "install-kind").write_text("nonsense")
    assert update.install_kind(tmp_path) == "portable"


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_download_checks_the_digest(tmp_path, monkeypatch):
    data = b"new version" * 1000
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda req, timeout: FakeResponse(data))
    good = "sha256:" + hashlib.sha256(data).hexdigest()
    f = update.download(Context(), ("a.tar.gz", "https://x/a", len(data), good), tmp_path)
    assert f.read_bytes() == data
    with pytest.raises(update.UpdateError):
        update.download(Context(), ("b.tar.gz", "https://x/b", len(data), "sha256:" + "0" * 64), tmp_path)
    assert not (tmp_path / "b.tar.gz").exists() and not (tmp_path / "b.tar.gz.part").exists()


def _app(folder, version):
    """A stand-in for the PyInstaller folder: a launcher that records which version started."""
    (folder / "_internal").mkdir(parents=True)
    (folder / "_internal" / f"lib-{version}.so").write_text(version)
    exe = folder / "MusicTools"
    exe.write_text(f"#!/bin/sh\necho {version} > \"$(dirname \"$0\")/started\"\n")
    exe.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux swap script")
def test_portable_update_swaps_files_after_exit(tmp_path):
    app = tmp_path / "MusicTools"
    _app(app, "old")
    (app / "bin").mkdir()
    (app / "bin" / "ffmpeg").write_text("user's own tool")
    build = tmp_path / "build" / "MusicTools"
    _app(build, "new")
    archive = tmp_path / "MusicTools-linux-x64.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(build, "MusicTools")

    running = subprocess.Popen(["sleep", "30"])  # the app that is being updated
    try:
        cmd = update.install(Context(), archive, "portable", app, pid=running.pid)
        update.launch_detached(cmd)
        time.sleep(1)
        assert not (app / "started").exists(), "must wait until the old app has quit"
    finally:
        running.kill()
        running.wait()
    for _ in range(50):
        if (app / "started").exists():
            break
        time.sleep(0.1)
    assert (app / "started").read_text().strip() == "new"
    assert sorted(os.listdir(app / "_internal")) == ["lib-new.so"]
    assert (app / "bin" / "ffmpeg").read_text() == "user's own tool"
    update.clean_leftovers(app)
    assert not (app / update.STAGING).exists()


def test_archive_without_the_app_is_refused(tmp_path):
    archive = tmp_path / "x.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        info = tarfile.TarInfo("MusicTools/readme.txt")
        t.addfile(info, io.BytesIO(b""))
    with pytest.raises(update.UpdateError):
        update.install(Context(), archive, "portable", tmp_path / "app", pid=os.getpid())


def test_source_runs_dont_install(tmp_path):
    assert not update.can_install("source")
    with pytest.raises(update.UpdateError):
        update.install(Context(), tmp_path / "x", "source", tmp_path)
