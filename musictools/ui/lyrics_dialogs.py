"""The lyrics page's dialogs (online search, lyrics for many songs, moving lyrics) and its "Added online" tab."""

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHeaderView,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QRadioButton,
                               QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import lyrics
from ..core.util import SETTINGS, human_duration, reveal
from . import theme
from .widgets import LogPanel, button, hbox, label, vbox


def _dialog_shell(dlg, title, intro, size=(640, 420)):
    dlg.setWindowTitle(title)
    dlg.resize(*size)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(12)
    lay.addWidget(label(title, "cardTitle"))
    lay.addWidget(label(intro, "muted", wrap=True))
    return lay


class OnlineDialog(QDialog):
    """Search LRCLIB for one song and pick a result."""

    def __init__(self, parent, bg, track):
        super().__init__(parent)
        self.bg, self.track = bg, track
        self.results, self.text = [], ""
        lay = _dialog_shell(self, "Find lyrics online", "Searches LRCLIB, a free lyrics database that often has "
                            "synced lyrics. The results that best match the song are listed first.", (900, 560))
        self.artist = QLineEdit(track.artist)
        self.artist.setPlaceholderText("Artist")
        self.title = QLineEdit(track.tag("title") or track.path.stem)
        self.title.setPlaceholderText("Title")
        go = button("Search", icon="search")
        go.clicked.connect(self.search)
        self.title.returnPressed.connect(self.search)
        self.artist.returnPressed.connect(self.search)
        lay.addLayout(hbox(self.artist, self.title, go))
        self.status = label("", "muted")
        lay.addWidget(self.status)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(theme.mono_font())
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.list)
        split.addWidget(self.preview)
        split.setSizes([380, 480])
        lay.addWidget(split, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.use_synced = btns.addButton("Use synced lyrics", QDialogButtonBox.AcceptRole)
        self.use_synced.setProperty("kind", "primary")
        self.use_plain = btns.addButton("Use plain lyrics", QDialogButtonBox.AcceptRole)
        self.use_instrumental = btns.addButton("Mark as instrumental", QDialogButtonBox.AcceptRole)
        self.use_synced.clicked.connect(lambda: self._pick("synced"))
        self.use_plain.clicked.connect(lambda: self._pick("plain"))
        self.use_instrumental.clicked.connect(lambda: self._pick("instrumental"))
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._show(-1)
        QTimer.singleShot(0, self.search)

    def search(self):
        artist, title = self.artist.text().strip(), self.title.text().strip()
        if not title:
            return
        self.status.setText("Searching…")
        self.list.clear()
        t = self.track
        self.bg.run(lambda: lyrics.search_online(artist, title, t.album, t.duration), self._found)

    def _found(self, res):
        if isinstance(res, Exception):
            self.status.setText(f"Couldn't search: {res}")
            return
        title = self.track.tag("title") or self.track.path.stem
        sure = [r for r in res if lyrics.confident(r, self.track.artist, title, self.track.duration)]
        self.results = sure + [r for r in res if r not in sure]
        self.list.clear()
        for r in self.results:
            kind = "Synced" if r["synced"] else "Instrumental" if r["instrumental"] else "Plain"
            match = "  ✓ matches" if r in sure else ""
            self.list.addItem(QListWidgetItem(f"{r['artist']} – {r['title']}\n{r['album'] or '—'} · "
                                              f"{human_duration(r['duration'])} · {kind}{match}"))
        self.status.setText(f"{len(res)} result(s)." if res else "Nothing found. Try a shorter title.")
        if res:
            self.list.setCurrentRow(0)

    def _show(self, row):
        r = self.results[row] if 0 <= row < len(self.results) else None
        self.preview.setPlainText(("Instrumental: no lyrics." if r["instrumental"] and not r["synced"]
                                   else r["synced"] or r["plain"]) if r else "")
        self.use_synced.setEnabled(bool(r and r["synced"]))
        self.use_plain.setEnabled(bool(r and r["plain"]))
        self.use_instrumental.setVisible(bool(r and r["instrumental"]))

    def _pick(self, which):
        r = self.results[self.list.currentRow()]
        self.text = lyrics.INSTRUMENTAL if which == "instrumental" else r[which]
        self.accept()


class BulkDialog(QDialog):
    """Which songs to look up online."""

    def __init__(self, parent, tracks):
        super().__init__(parent)
        self.tracks = tracks
        lay = _dialog_shell(self, "Find lyrics for many songs",
                            f"Looks up the {len(tracks):,} song(s) in the list (narrow it down with the search and "
                            "the filter first). Sure matches are saved right away; lyrics that get replaced are "
                            "backed up first. Everything that happened is listed in the “Added online” tab, "
                            "where you can check each song and undo it.")
        none = sum(1 for t in tracks if not t.lyrics)
        unsynced = sum(1 for t in tracks if t.lyrics in ("", "plain"))
        self.missing = QRadioButton(f"Songs without lyrics ({none:,})")
        self.unsynced = QRadioButton(f"Songs without synced lyrics ({unsynced:,}): plain lyrics are replaced "
                                     "by synced ones")
        self.all = QRadioButton(f"All {len(tracks):,} songs: lyrics are replaced when the ones found are as good "
                                "or better (word-synced lyrics are never replaced by line-synced ones)")
        for r in (self.missing, self.unsynced, self.all):
            r.setStyleSheet("QRadioButton { padding: 3px 0; }")
            lay.addWidget(r)
        self.missing.setChecked(True)
        mode = lyrics.SAVE_MODES[SETTINGS.get("lyrics_save", "embed")].lower()
        lay.addWidget(label(f"Lyrics are saved {mode} (the “Save to” setting). Songs marked instrumental online "
                            "are marked here too.", "hint", wrap=True))
        lay.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = btns.addButton("Find lyrics", QDialogButtonBox.AcceptRole)
        go.setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @property
    def want(self):
        return "missing" if self.missing.isChecked() else "unsynced" if self.unsynced.isChecked() else "all"


class FilesDialog(QDialog):
    """Move lyrics between the music files and .lrc files next to them."""

    def __init__(self, parent, tracks):
        super().__init__(parent)
        self.tracks = tracks
        lay = _dialog_shell(self, "Lyrics files", f"For the {len(tracks):,} song(s) in the list (narrow it down "
                            "with the search and the filter first). The Opus copies are changed the same way.")
        self.to_file = QRadioButton("Take the lyrics out of the music files, into .lrc files next to them")
        self.to_embed = QRadioButton("Put the lyrics from .lrc files into the music files")
        self.to_file.setChecked(True)
        self.keep = QCheckBox("Keep the original copy too (the lyrics end up in both places)")
        for w in (self.to_file, self.to_embed, self.keep):
            lay.addWidget(w)
        lay.addWidget(label("Tip: to save new lyrics the same way from now on, change “Save to” in the editor.",
                            "hint", wrap=True))
        lay.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Cancel)
        go = btns.addButton("Move lyrics", QDialogButtonBox.AcceptRole)
        go.setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    @property
    def to(self):
        return "file" if self.to_file.isChecked() else "embed"


REVIEW_FILTERS = [("check", "To check"), ("all", "Everything"), (lyrics.ADDED, "Added"),
                  (lyrics.REPLACED, "Replaced"), (lyrics.INSTRUMENTAL_FOUND, "Instrumental"),
                  (lyrics.UNSURE, "Check the match"), (lyrics.NOT_FOUND, "Not found"),
                  (lyrics.FAILED, "Couldn't look up"), (lyrics.UNDONE, "Undone")]
OUTCOME_COLORS = {lyrics.ADDED: theme.OK, lyrics.REPLACED: theme.OK, lyrics.INSTRUMENTAL_FOUND: theme.MUTED,
                  lyrics.UNSURE: theme.WARN, lyrics.NOT_FOUND: theme.FAINT, lyrics.UNDONE: theme.FAINT,
                  lyrics.FAILED: theme.ERR}


class ReviewTab(QWidget):
    """What "Find lyrics for many songs" did, to check song by song: open it, use an unsure match, or undo."""
    openSong = Signal(str)
    saveText = Signal(str, str, str)     # path, text, what for (shown in the status bar)

    def __init__(self, jobs):
        super().__init__()
        self.history = lyrics.History()
        self.filter = QComboBox()
        for key, text in REVIEW_FILTERS:
            self.filter.addItem(text, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.summary = label("", "muted")
        self.open_btn = button("Open in editor", icon="edit")
        self.use_btn = button("Use this match", "primary", "check", "Save the lyrics found for this song")
        self.undo_btn = button("Undo", icon="undo", tip="Put back what the song had before (from the backup)")
        self.checked_btn = button("Mark as checked", icon="check", tip="Take it off the “To check” list")
        backups = button("Backups", "ghost", "folder", "Open the folder with the replaced lyrics")
        clear = button("Clear list", "ghost", "trash", "Forget this list (the lyrics stay as they are)")
        self.open_btn.clicked.connect(lambda: self._each(lambda i: self.openSong.emit(i["path"]), one=True))
        self.use_btn.clicked.connect(self._use)
        self.undo_btn.clicked.connect(self._undo)
        self.checked_btn.clicked.connect(lambda: self._each(lambda i: i.update(checked=True)))
        backups.clicked.connect(self._backups)
        clear.clicked.connect(self._clear)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Song", "Result", "Found / why", "When"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setDefaultSectionSize(28)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(3, 120)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.itemDoubleClicked.connect(lambda _: self.open_btn.click())
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(theme.mono_font())
        self.preview.setPlaceholderText("Pick a song to see the lyrics that were found.")
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.preview)
        split.setSizes([800, 380])
        self.log = LogPanel(jobs, 60)
        self.log.view.setMaximumHeight(120)
        self.log.setSizePolicy(self.log.sizePolicy().horizontalPolicy(), QSizePolicy.Maximum)
        lay = vbox(hbox(label("Show", "muted"), self.filter, self.summary, None, backups, clear),
                   split, hbox(self.open_btn, self.use_btn, self.undo_btn, self.checked_btn, None),
                   self.log, spacing=10, margins=(0, 10, 0, 0))
        lay.setStretch(1, 1)
        self.setLayout(lay)
        self.refresh()

    def reload(self):
        self.history = lyrics.History()
        self.refresh()

    def to_check(self):
        return sum(1 for i in self.history.items if not i.get("checked") and i["outcome"] != lyrics.NOT_FOUND)

    def _visible(self):
        key = self.filter.currentData()
        if key == "check":
            return [i for i in self.history.items if not i.get("checked") and i["outcome"] != lyrics.NOT_FOUND]
        return [i for i in self.history.items if key == "all" or i["outcome"] == key]

    def refresh(self):
        self.items = self._visible()
        self.table.setRowCount(len(self.items))
        for r, i in enumerate(self.items):
            outcome = lyrics.OUTCOMES[i["outcome"]] + (" ✓" if i.get("checked") else "")
            cells = [i["song"], outcome, i.get("match", ""), time.strftime("%d %b %H:%M", time.localtime(i["time"]))]
            for c, v in enumerate(cells):
                item = QTableWidgetItem(v)
                if c == 1:
                    item.setForeground(QColor(OUTCOME_COLORS[i["outcome"]]))
                if c == 0:
                    item.setToolTip(i["path"])
                self.table.setItem(r, c, item)
        counts = {}
        for i in self.history.items:
            counts[i["outcome"]] = counts.get(i["outcome"], 0) + 1
        self.summary.setText(" · ".join(f"{n} {lyrics.OUTCOMES[k].lower()}" for k, n in counts.items()) or
                             "Nothing yet. Use “Find for many songs…” in the editor tab.")
        self._selection()

    def _selected(self):
        return [self.items[i.row()] for i in self.table.selectionModel().selectedRows() if i.row() < len(self.items)]

    def _selection(self):
        sel = self._selected()
        one = sel[0] if len(sel) == 1 else None
        text = ""
        if one:
            text = one.get("text", "")
            if not text and one["outcome"] != lyrics.NOT_FOUND:
                text = lyrics.load(one["path"])[0] if Path(one["path"]).exists() else ""
        self.preview.setPlainText(text)
        self.open_btn.setEnabled(bool(one))
        self.use_btn.setEnabled(bool(one and one["outcome"] in (lyrics.UNSURE, lyrics.UNDONE) and one.get("text")))
        self.undo_btn.setEnabled(any(i["outcome"] in (lyrics.ADDED, lyrics.REPLACED, lyrics.INSTRUMENTAL_FOUND)
                                     for i in sel))
        self.checked_btn.setEnabled(bool(sel))

    def _each(self, fn, one=False):
        sel = self._selected()[:1] if one else self._selected()
        for i in sel:
            fn(i)
        if not one:
            self.history.save()
            self.refresh()

    def _use(self):
        (i,) = self._selected()
        self.saveText.emit(i["path"], i["text"], "Saved the lyrics found")
        i.update(outcome=lyrics.ADDED, checked=True)
        self.history.save()
        self.refresh()

    def _undo(self):
        sel = [i for i in self._selected() if i["outcome"] in (lyrics.ADDED, lyrics.REPLACED,
                                                                lyrics.INSTRUMENTAL_FOUND)]
        if not sel or QMessageBox.question(self, "Undo", f"Put back what {len(sel)} song(s) had before?") \
                != QMessageBox.Yes:
            return
        for i in sel:
            old = ""
            if i.get("backup"):
                try:
                    old = Path(i["backup"]).read_text(encoding="utf-8")
                except OSError:
                    QMessageBox.warning(self, "Undo", f"The backup of {i['song']} is gone; skipped it.")
                    continue
            self.saveText.emit(i["path"], old, "Put back the lyrics from before")
            i.update(outcome=lyrics.UNDONE, checked=True, backup="")
        self.history.save()
        self.refresh()

    def _backups(self):
        folder = lyrics.backup_dir()
        folder.mkdir(parents=True, exist_ok=True)
        reveal(folder)

    def _clear(self):
        if QMessageBox.question(self, "Clear list", "Forget this list? The lyrics and the backups stay as they "
                                "are.") == QMessageBox.Yes:
            self.history.items = []
            self.history.save()
            self.refresh()
