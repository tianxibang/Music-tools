"""The options on the Settings page: their checks, the settings file, and the page itself."""

import json
import os

import pytest

from musictools.core import health, options, util
from musictools.core.util import DEFAULTS, SETTINGS, Settings


def test_every_option_has_a_default_and_a_section():
    sections = {key for key, _, _ in options.SECTIONS}
    for opt in options.OPTIONS:
        assert opt.key in DEFAULTS, opt.key
        assert opt.section in sections, opt.key
        assert opt.check(DEFAULTS[opt.key]) == DEFAULTS[opt.key], f"default of {opt.key} isn't valid"


def test_checks():
    by = options.BY_KEY
    assert by["lrclib_gap"].check(0.1) == 1.0          # LRCLIB is never asked more than once a second
    assert by["jobs"].check("3") == 3 and by["jobs"].check(0) == 1
    assert by["phone_codecs"].check(["opus", "nonsense", "mp3"]) == ["mp3", "opus"]
    assert by["update_check"].check(0) is False
    with pytest.raises(ValueError):
        by["sync_confirm"].check("sometimes")
    with pytest.raises(ValueError):
        by["lossy_folder"].check("  ")
    assert by["tool_ffmpeg"].check("") == ""           # empty: found automatically


def test_bad_values_in_the_file_fall_back(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"bitrate": "loud", "jobs": 999, "sync_confirm": "sometimes", "serial": "abc"}))
    monkeypatch.setattr(Settings, "path", path)
    s = Settings.load()
    assert s["bitrate"] == DEFAULTS["bitrate"]
    assert s["jobs"] == 64
    assert s["sync_confirm"] == DEFAULTS["sync_confirm"]
    assert s["serial"] == "abc"  # not an option: kept as it is


def test_version_words_setting(monkeypatch):
    assert health._versions("Turntail (Pre-Production Demo 2015)") == {"demo"}
    monkeypatch.setitem(SETTINGS, "version_words", "demo, nightcore")
    assert health._versions("Song (Nightcore)") == {"nightcore"}
    assert health._versions("Song (Live)") == frozenset()


def test_phone_codecs_decide_conversion(tmp_path, monkeypatch):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"0")
    monkeypatch.setattr(util, "stream_info", lambda p: ("eac3", 2, 300, False))
    monkeypatch.setitem(SETTINGS, "bitrate", 128)
    assert util.converts_to_opus(f)
    monkeypatch.setitem(SETTINGS, "phone_codecs", ["eac3"])
    monkeypatch.setitem(SETTINGS, "keep_lossy_below", 3.0)
    assert not util.converts_to_opus(f)  # the cached verdict is redone when the settings change


@pytest.fixture
def window(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(Settings, "path", tmp_path / "settings.json")
    saved = dict(SETTINGS)
    from musictools.ui import update_dialog
    monkeypatch.setattr(update_dialog, "check_on_startup", lambda win: None)
    from musictools.ui.main_window import MainWindow
    win = MainWindow()
    yield win
    win.close()
    SETTINGS.clear()
    SETTINGS.update(saved)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_page_saves_and_updates_other_pages(window, tmp_path):
    from musictools.ui.pages.convert import ConvertPage
    from musictools.ui.pages.settings import SettingsPage
    page = window.page(SettingsPage)
    row = next(r for r in page.rows if r.opt.key == "bitrate")
    row.commit(160)
    assert SETTINGS["bitrate"] == 160
    assert json.loads((tmp_path / "settings.json").read_text())["bitrate"] == 160
    assert window.page(ConvertPage).bitrate.currentText() == "160 kbps"
    assert row.reset.isVisibleTo(page)
    row.reset.click()
    assert SETTINGS["bitrate"] == DEFAULTS["bitrate"]

    watch = next(r for r in page.rows if r.opt.key == "watch_interval")
    watch.commit(60)
    assert window.watch_timer.interval() == 60_000

    page.search.setText("lrclib")
    shown = [r.opt.key for r in page.rows if r.isVisibleTo(page)]
    assert shown == ["lrclib_gap", "lyrics_recheck_days"]


def test_export_and_import(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from musictools.ui.pages.settings import SettingsPage
    page = window.page(SettingsPage)
    out = tmp_path / "exported.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(out), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    page.export()
    data = json.loads(out.read_text())
    assert "serial" not in data and data["bitrate"] == SETTINGS["bitrate"]
    data.update(bitrate=96, serial="stolen", lossy_folder="MP3s")
    out.write_text(json.dumps(data))
    page.import_()
    assert SETTINGS["bitrate"] == 96 and SETTINGS["lossy_folder"] == "MP3s"
    assert SETTINGS["serial"] != "stolen"  # only options, never what the app remembers
