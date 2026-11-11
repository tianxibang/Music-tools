"""Lyrics editor: plain lyrics, syncing them to the song line by line or word by word, duets and ad-libs,
and looking lyrics up online for many songs at once."""

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFrame, QHeaderView, QInputDialog, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core import lyrics
from ...core.jobs import EXCLUSIVE
from ...core.util import SETTINGS, human_duration
from .. import audio, icons, theme
from ..lyrics_dialogs import BulkDialog, FilesDialog, OnlineDialog, ReviewTab
from ..lyrics_widgets import ADLIB, MIN_LINE, MIN_WORD, VOICE_NAMES, Preview, Timeline, adlib_end, voice_colors
from ..main_window import Page
from ..widgets import TrackFilter, TrackModel, button, hbox, label, search_box, selected_tracks, track_table, vbox

FILTERS = [("all", "All songs"), ("none", "Without lyrics"), ("unsynced", "Not synced yet"), ("synced", "Synced"),
           ("instrumental", "Instrumental")]
SPEEDS = [(1.0, "1×"), (0.75, "0.75×"), (0.5, "0.5×"), (0.25, "0.25×")]
KEYS_SHORT = ("<b>Space</b> play/pause · <b>Enter</b> next line starts · <b>E</b> line ends (instrumental) · "
              "<b>↑</b> back · <b>[ ]</b> start / end at the playhead · drag blocks and edges on the timeline · "
              "<u>more…</u>")
KEYS = ("<b>Space</b> play/pause · <b>Enter</b> the next line (word) starts now · <b>E</b> the line ends now "
        "(instrumental) · <b>↑</b> back one · <b>← →</b> 2 s (Shift: 0.1 s) · <b>[ ]</b> set the selected line's "
        "start / end to the playhead · <b>Del</b> clear its times · <b>V</b> change its singer · <b>A</b> add an "
        "ad-lib at the playhead · <b>Ctrl+Z</b> undo<br>"
        "Timeline: drag blocks or their edges · <b>Shift</b>+drag moves all later lines too · <b>Ctrl</b>+drag "
        "where two lines (or words) touch ends the first one early · <b>Ctrl</b>+wheel zooms · double-click plays · "
        "right-click for singers and ad-libs")
VOICE_CYCLE = ["", "v1", "v2"]


def _summary(counts):
    return " · ".join(f"{n} {lyrics.OUTCOMES[k].lower()}" for k, n in counts.items() if k in lyrics.OUTCOMES and n)


class LyricsModel(TrackModel):
    def __init__(self):
        super().__init__([("title", "Title"), ("artist", "Artist"), ("lyrics", "Lyrics")])

    def value(self, t, key):
        if key == "lyrics":
            return lyrics.KIND_LABELS[t.lyrics]
        return super().value(t, key)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and self.columns[index.column()][0] == "lyrics":
            t = self.tracks[index.row()]
            if role == Qt.ForegroundRole:
                return QColor({"words": theme.OK, "lines": theme.OK, "plain": theme.MUTED,
                               "instrumental": theme.MUTED}.get(t.lyrics, theme.FAINT))
            if role == Qt.UserRole:
                return lyrics._RANK[t.lyrics]
        return super().data(index, role)


class LyricsFilter(TrackFilter):
    def __init__(self):
        super().__init__()
        self.kind = "all"

    def set_kind(self, kind):
        self.kind = kind
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        t = self.sourceModel().tracks[row]
        ok = {"all": True, "none": not t.lyrics, "unsynced": t.lyrics in ("", "plain"),
              "synced": t.lyrics in ("lines", "words"), "instrumental": t.lyrics == "instrumental"}[self.kind]
        return ok and super().filterAcceptsRow(row, parent)


class LyricsText(QPlainTextEdit):
    """The text editor; pasting an .lrc keeps its timings."""
    lrcPasted = Signal(str)

    def insertFromMimeData(self, source):
        text = source.text()
        if lyrics.is_lrc(text) and not self.toPlainText().strip():
            self.lrcPasted.emit(text)
            return
        super().insertFromMimeData(source)


class LyricsPage(Page):
    title = "Lyrics"
    subtitle = "Write lyrics, look them up online, and sync them to the song line by line or word by word."
    icon = "mic"
    scroll = False

    def __init__(self, win):
        super().__init__(win)
        self.player = audio.Player(self)
        self.track = None
        self.song = None
        self.doc = lyrics.Lyrics()
        self._last = self.doc.copy()      # the doc as of the last undo step
        self.loaded_text = ""
        self.undo_stack, self.redo_stack = [], []
        self.cursor = self.wcursor = 0    # the next line (and word) to tap
        self._rows = []                   # table rows: (line, ad-lib or None)
        self._token = 0
        self._current_row = -1
        self._updating = False

        self.search = search_box()
        self.filter = QComboBox()
        for key, text in FILTERS:
            self.filter.addItem(text, key)
        keys = [k for k, _ in FILTERS]
        self.filter.setCurrentIndex(keys.index(SETTINGS["lyrics_filter"]) if SETTINGS["lyrics_filter"] in keys else 0)
        self.count = label("", "muted")
        self.bulk_btn = button("Find for many songs…", icon="search",
                               tip="Look up lyrics online for the songs in the list and save the sure matches")
        self.bulk_btn.clicked.connect(self.find_many)
        self.files_btn = button("Lyrics files…", icon="folder",
                                tip="Move lyrics out of the music files into .lrc files, or the other way round")
        self.files_btn.clicked.connect(self.move_files)
        self.lock(self.bulk_btn, self.files_btn)
        reload_btn = button("", "ghost", "refresh", "Re-read the library")
        reload_btn.clicked.connect(self.store.reload)
        self.add(hbox(self.search, self.filter, self.count, None, self.bulk_btn, self.files_btn, reload_btn))

        self.model = LyricsModel()
        self.view, _ = track_table(self.model)
        self.proxy = LyricsFilter()
        self.proxy.setSourceModel(self.model)
        self.view.setModel(self.proxy)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.sortByColumn(1, Qt.AscendingOrder)
        self.view.setColumnWidth(0, 140)
        self.view.setColumnWidth(1, 100)
        self.view.setMinimumWidth(300)
        self.view.selectionModel().selectionChanged.connect(self._track_selected)
        self.search.textChanged.connect(self._filter)
        self.filter.currentIndexChanged.connect(self._filter)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.view)
        split.addWidget(self._editor())
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        split.setSizes([360, 1000])
        self.review = ReviewTab(self.jobs)
        self.review.openSong.connect(self.open_path)
        self.review.saveText.connect(self._save_from_review)
        self.pages = QTabWidget()
        self.pages.tabBar().setFocusPolicy(Qt.NoFocus)
        self.pages.addTab(split, "Editor")
        self.pages.addTab(self.review, "")
        self._review_title()
        self.add(self.pages, 1)

        self.timer = QTimer(self, interval=33, timeout=self._tick)
        self.player.stateChanged.connect(self._play_state)
        self.store.changed.connect(self._reload)
        self.store.updated.connect(self.model.refresh_rows)
        self._set_track(None)

    # ------------------------------------------------------------------ layout

    def _editor(self):
        panel = QFrame()
        panel.setObjectName("card")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.song_title = label("Pick a song", "cardTitle")
        self.song_sub = label("", "muted")
        for lab in (self.song_title, self.song_sub):     # long titles mustn't widen the editor
            lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.online_btn = button("Find online…", icon="search", tip="Look the lyrics up on LRCLIB")
        self.online_btn.clicked.connect(self.find_online)
        self.instrumental = QCheckBox("Instrumental")
        self.instrumental.setToolTip("A song without words: it's marked as such ([instrumental:true]) instead of "
                                     "counting as missing lyrics")
        self.instrumental.toggled.connect(self._instrumental_toggled)
        self.save_mode = QComboBox()
        for key, text in lyrics.SAVE_MODES.items():
            self.save_mode.addItem(text, key)
        self.save_mode.setCurrentIndex(max(0, list(lyrics.SAVE_MODES).index(SETTINGS.get("lyrics_save", "embed"))))
        self.save_mode.setToolTip("Where the lyrics are saved. Lyrics already in the other place are updated too, "
                                  "so the two never disagree.")
        self.save_mode.currentIndexChanged.connect(self._save_mode_changed)
        self.remove_btn = button("Remove", "danger", "trash", "Delete this song's lyrics")
        self.remove_btn.clicked.connect(self.remove)
        self.save_btn = button("Save", "primary", "check")
        self.save_btn.clicked.connect(self.save)
        head = hbox(vbox(self.song_title, self.song_sub, spacing=2), label("Save to", "muted"), self.save_mode,
                    self.remove_btn, self.save_btn)
        head.setStretch(0, 1)
        lay.addLayout(head)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setFocusPolicy(Qt.NoFocus)
        self.text = LyricsText()
        self.text.setPlaceholderText("Type or paste the lyrics here, one line per lyric line.\n\n"
                                     "Pasting an .lrc file keeps its timings. Then sync them in the Sync tab.")
        f = QFont(self.text.font())
        f.setPointSizeF(11)
        self.text.setFont(f)
        self.text.textChanged.connect(lambda: self._text_timer.start() if not self._updating else None)
        self.text.lrcPasted.connect(lambda t: self._replace_doc(lyrics.parse(t)))
        self._text_timer = QTimer(self, singleShot=True, interval=350, timeout=self._text_edited)
        self.tabs.addTab(self.text, "Text")
        self.tabs.addTab(self._sync_tab(), "Sync")
        self.lrc = QPlainTextEdit()
        self.lrc.setFont(theme.mono_font())
        self.lrc.setPlaceholderText("The lyrics as they're saved (LRC). You can edit them here too.")
        self.lrc.textChanged.connect(lambda: self._lrc_timer.start() if not self._updating else None)
        self._lrc_timer = QTimer(self, singleShot=True, interval=500, timeout=self._lrc_edited)
        self.tabs.addTab(self.lrc, "LRC")
        self.tabs.currentChanged.connect(self._tab_changed)
        corner = QWidget()
        corner.setLayout(hbox(self.instrumental, 10, self.online_btn, margins=(0, 0, 0, 6)))
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        lay.addWidget(self.tabs, 1)
        return panel

    def _sync_tab(self):
        tab = QWidget()
        tab.setFocusPolicy(Qt.StrongFocus)
        self.sync_tab = tab
        self.play_btn = button("", "primary", "play", "Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.time_label = label("00:00.00 / 00:00.00")
        self.time_label.setFont(theme.mono_font())
        self.time_label.setMinimumWidth(self.time_label.fontMetrics().horizontalAdvance("000:00.00 / 000:00.00"))
        self.speed = QComboBox()
        for rate, text in SPEEDS:
            self.speed.addItem(text, rate)
        self.speed.setToolTip("Playback speed. Slower makes tapping easier; the pitch stays the same.")
        self.speed.currentIndexChanged.connect(lambda: self.player.set_rate(self.speed.currentData()))
        self.mode = QComboBox()
        self.mode.addItem("Line by line", False)
        self.mode.addItem("Word by word", True)
        self.mode.setToolTip("Line by line: Enter times the next line. Word by word: Enter times the next word, "
                             "for karaoke-style lyrics.")
        self.mode.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.speed.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 1)
        self.delay.setSingleStep(0.05)
        self.delay.setDecimals(2)
        self.delay.setSuffix(" s")
        self.delay.setValue(float(SETTINGS.get("lyrics_tap_offset", 0.0)))
        self.delay.setToolTip("Taps are moved this much earlier, to make up for your reaction time")
        self.delay.valueChanged.connect(self._delay_changed)
        self.delay.editingFinished.connect(lambda: self.sync_tab.setFocus())
        self.undo_btn = button("", "ghost", "undo", "Undo (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = button("", "ghost", "redo", "Redo (Ctrl+Shift+Z)")
        self.redo_btn.clicked.connect(self.redo)
        shift = button("Shift all…", tip="Move every line earlier or later")
        shift.clicked.connect(self.shift_all)
        clear = button("Clear times", tip="Remove all timings and start over")
        clear.clicked.connect(self.clear_times)
        zoom_out = button("−", "ghost", tip="Zoom out (Ctrl+wheel)")
        zoom_in = button("+", "ghost", tip="Zoom in (Ctrl+wheel)")
        for w in (self.play_btn, self.speed, self.mode, self.undo_btn, self.redo_btn, shift, clear, zoom_out, zoom_in):
            w.setFocusPolicy(Qt.NoFocus)

        self.preview = Preview()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Start", "End", "Singer", "Line"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.horizontalHeaderItem(3).setToolTip("Who sings the line, for duets: 1, 2 or empty. Press V to "
                                                      "change it.")
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(4, QHeaderView.Stretch)
        for c, w in ((0, 26), (1, 92), (2, 92), (3, 54)):
            self.table.setColumnWidth(c, w)
        self.table.itemChanged.connect(self._cell_edited)
        self.table.itemSelectionChanged.connect(self._row_selected)

        self.timeline = Timeline(self.player)
        self.timeline.edited.connect(self.commit)
        self.timeline.lineClicked.connect(self._timeline_clicked)
        self.timeline.playFrom.connect(lambda t: self.player.play(t))
        self.timeline.addAdlib.connect(self.add_adlib)
        zoom_out.clicked.connect(lambda: self.timeline.zoom(1 / 1.5))
        zoom_in.clicked.connect(lambda: self.timeline.zoom(1.5))
        self.status = label("", "muted")
        keys = label(KEYS_SHORT, "hint", wrap=True)
        keys.setToolTip(KEYS)

        top = hbox(self.play_btn, self.time_label, 12, label("Speed", "muted"), self.speed, 12,
                   label("Sync", "muted"), self.mode, 12, label("Tap delay", "muted"), self.delay, None)
        tools = hbox(self.undo_btn, self.redo_btn, shift, clear, None, label("Zoom", "muted"), zoom_out, zoom_in)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.preview)
        split.addWidget(self.table)
        split.setSizes([100, 320])
        split.setChildrenCollapsible(False)
        tab.setLayout(vbox(top, split, tools, self.timeline, self.timeline.scrollbar, hbox(self.status, None),
                           keys, spacing=8, margins=(0, 10, 0, 0)))
        return tab

    # ------------------------------------------------------------ track list

    def on_show(self):
        if not self.store.loaded_once:
            self.count.setText("Loading library…")

    def showEvent(self, e):
        # Keys for tapping go to the editor wherever the focus is on this page (the tab bar, a button…)
        QApplication.instance().installEventFilter(self)
        super().showEvent(e)

    def hideEvent(self, e):
        QApplication.instance().removeEventFilter(self)
        self.player.pause()
        super().hideEvent(e)

    def shutdown(self):
        self.player.shutdown()

    def confirm_close(self):
        return self._confirm_discard()

    def set_follower(self, on, main_name):
        # lyrics edits are sent to the main PC like tag edits; moving them between files would fight the mirror
        self.files_btn.setVisible(not on)

    def _reload(self):
        path = self.track.path if self.track else None
        self.model.set_tracks(self.store.tracks)
        self._filter()
        if path:
            self._select_path(path)

    def _filter(self):
        kind = self.filter.currentData()
        SETTINGS["lyrics_filter"] = kind
        self.proxy.set_kind(kind)
        self.proxy.set_needle(self.search.text())
        shown, total = self.proxy.rowCount(), len(self.model.tracks)
        self.count.setText(f"{total:,} songs" if shown == total else f"{shown:,} of {total:,}")

    def shown_tracks(self):
        return [self.model.tracks[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                for r in range(self.proxy.rowCount())]

    def _select_path(self, path):
        for row, t in enumerate(self.model.tracks):
            if t.path == path:
                idx = self.proxy.mapFromSource(self.model.index(row, 0))
                if idx.isValid():
                    self.view.selectionModel().blockSignals(True)
                    self.view.selectRow(idx.row())
                    self.view.selectionModel().blockSignals(False)
                    self.view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                return t

    def open_path(self, path):
        """Open a song in the editor (from the "Added online" tab)."""
        from pathlib import Path
        path = Path(path)
        track = next((t for t in self.model.tracks if t.path == path), None)
        if track is None:
            QMessageBox.information(self, "Lyrics", "That song isn't in the library any more.")
            return
        if not (self.track and self.track.path == path) and not self._confirm_discard():
            return
        self.search.clear()
        self.filter.setCurrentIndex(0)
        self._select_path(path)
        if not (self.track and self.track.path == path):
            self._set_track(track)
        self.pages.setCurrentIndex(0)

    def _track_selected(self):
        sel = selected_tracks(self.view, self.proxy, self.model)
        new = sel[0] if sel else None
        if new is None or (self.track and new.path == self.track.path):
            return      # (filtered out of view: keep editing it)
        if not self._confirm_discard():
            if self.track:
                self._select_path(self.track.path)
            return
        self._set_track(new)

    def _flush(self):
        """Apply typing that's still waiting for its timer."""
        for timer, apply in ((self._text_timer, self._text_edited), (self._lrc_timer, self._lrc_edited)):
            if timer.isActive():
                timer.stop()
                apply()

    def _confirm_discard(self):
        self._flush()
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Question, "Unsaved lyrics",
                          f"Save the lyrics of “{self.track.title}” first?",
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
        answer = box.exec()
        if answer == QMessageBox.Save:
            return self.save()
        return answer == QMessageBox.Discard

    def _set_track(self, track):
        self.player.stop()
        self.track = track
        self.song = None
        self._token += 1
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.cursor = self.wcursor = 0
        for w in (self.online_btn, self.save_btn, self.remove_btn, self.tabs, self.save_mode, self.instrumental):
            w.setEnabled(track is not None)
        if track is None:
            self.song_title.setText("Pick a song")
            self.song_sub.setText("Choose a song on the left to write or sync its lyrics.")
            self.loaded_text = ""
            self._replace_doc(lyrics.Lyrics(), undoable=False)
            self.timeline.set_song(None, 0)
            self.timeline.message = "Pick a song to see its waveform."
            return
        self.song_title.setText(track.title)
        text, where = lyrics.load(track.path)
        doc = lyrics.parse(text)
        what = (lyrics.KIND_LABELS[doc.kind] + (" duet" if doc.duet else "") + " lyrics "
                + ("in the music file" if where == "embed" else "in a .lrc file")) if where else "No lyrics yet"
        self.song_sub.setText(f"{track.artist or 'Unknown artist'} · {human_duration(track.duration)} · {what}")
        self._replace_doc(doc, undoable=False)
        self.loaded_text = lyrics.serialize(doc) if text.strip() else ""
        self.cursor = next((i for i, l in enumerate(doc.lines) if l.start is None), 0)
        self.timeline.duration = 0
        self.timeline.message = "Loading the audio…"
        self.timeline.update()
        token, path = self._token, track.path
        self.bg.run(lambda: audio.decode(path), lambda res: self._audio_loaded(token, res))
        if not doc.lines and not doc.instrumental:
            self.tabs.setCurrentIndex(0)
        self._update_title()

    def _audio_loaded(self, token, res):
        if token != self._token:
            return
        if isinstance(res, Exception):
            self.timeline.message = f"Couldn't load the audio: {res}"
            self.timeline.update()
            return
        self.song = res
        self.player.load(res)
        self.timeline.set_song(res.peaks, res.duration)
        self.timeline.zoom(1.0)
        self._tick()

    # ---------------------------------------------------------------- doc state

    @property
    def dirty(self):
        return self.track is not None and lyrics.serialize(self.doc).strip() != self.loaded_text.strip()

    def commit(self, source=None):
        """The doc changed: one undo step. source: the editor it was typed in, which is left alone."""
        self.doc.normalize()
        self.undo_stack.append(self._last)
        del self.undo_stack[:-200]
        self.redo_stack.clear()
        self._last = self.doc.copy()
        self._refresh(source)

    def _replace_doc(self, doc, undoable=True):
        if undoable:
            self.undo_stack.append(self._last)
            self.redo_stack.clear()
        self.doc = doc
        self.doc.normalize()
        self._last = doc.copy()
        if self.mode.currentData():
            for l in doc.lines:
                l.ensure_words()
        self._refresh()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self._last)
        self._restore(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._last)
        self._restore(self.redo_stack.pop())

    def _restore(self, doc):
        self.doc = doc
        self._last = doc.copy()
        self.cursor = min(self.cursor, len(doc.lines))
        self.wcursor = 0
        self._refresh()

    def _refresh(self, source=None):
        """Show the doc everywhere."""
        self._updating = True
        if source != "text" and self.text.toPlainText() != self.doc.text():
            self.text.setPlainText(self.doc.text())
        if source != "lrc" and self.tabs.currentIndex() == 2:
            self.lrc.setPlainText(lyrics.serialize(self.doc))
        self.instrumental.blockSignals(True)
        self.instrumental.setChecked(self.doc.instrumental)
        self.instrumental.blockSignals(False)
        self._updating = False
        self.text.setReadOnly(self.doc.instrumental)
        self.text.setPlaceholderText("Marked as instrumental: untick “Instrumental” to write lyrics."
                                     if self.doc.instrumental else
                                     "Type or paste the lyrics here, one line per lyric line.\n\n"
                                     "Pasting an .lrc file keeps its timings. Then sync them in the Sync tab.")
        self.timeline.set_doc(self.doc)
        self._fill_table()
        self.undo_btn.setEnabled(bool(self.undo_stack))
        self.redo_btn.setEnabled(bool(self.redo_stack))
        self._update_title()
        self._tick()

    def _update_title(self):
        if self.track:
            self.song_title.setText(("● " if self.dirty else "") + self.track.title)
            self.save_btn.setEnabled(self.dirty)
            self.remove_btn.setEnabled(bool(self.loaded_text.strip()))

    def _text_edited(self):
        text = self.text.toPlainText()
        if text == self.doc.text():
            return
        self.doc.set_text(text)
        if self.mode.currentData():
            for l in self.doc.lines:
                l.ensure_words()
        self.commit("text")

    def _lrc_edited(self):
        text = self.lrc.toPlainText()
        if text.strip() == lyrics.serialize(self.doc).strip():
            return
        self.doc = lyrics.parse(text)
        self.commit("lrc")

    def _tab_changed(self, i):
        self._flush()
        if i == 2:
            self._updating = True
            self.lrc.setPlainText(lyrics.serialize(self.doc))
            self._updating = False
        elif i == 1:
            self.sync_tab.setFocus()
        if i != 1:
            self.player.pause()

    def _instrumental_toggled(self, on):
        if self._updating:
            return
        if on and self.doc.text().strip() and QMessageBox.question(
                self, "Instrumental", "Mark this song as instrumental? Its lyrics are replaced by the "
                "instrumental mark when you save (Undo brings them back).") != QMessageBox.Yes:
            self.instrumental.blockSignals(True)
            self.instrumental.setChecked(False)
            self.instrumental.blockSignals(False)
            return
        self.doc.instrumental = on
        self.commit()

    def _save_mode_changed(self):
        SETTINGS["lyrics_save"] = self.save_mode.currentData()
        SETTINGS.save()

    def _delay_changed(self, v):
        SETTINGS["lyrics_tap_offset"] = v

    def _mode_changed(self):
        on = bool(self.mode.currentData())
        if on:
            for l in self.doc.lines:
                l.ensure_words()
        self.wcursor = 0
        self.timeline.set_words(on)
        self._fill_table()
        self._say_next()

    # ------------------------------------------------------------------ table

    def _fill_table(self):
        self._updating = True
        self.table.blockSignals(True)
        rows = []
        for l in self.doc.lines:
            rows.append((l, None))
            rows += [(l, b) for b in l.bg]
        self._rows = rows
        self.table.setRowCount(len(rows))
        sel = self.timeline.selected
        selected_row = None
        for r, (l, b) in enumerate(rows):
            item_line = b or l
            end = adlib_end(b) if b is not None and b.start is not None and b.end is None else item_line.end
            cells = ["", lyrics.stamp(item_line.start) if item_line.start is not None else "—",
                     lyrics.stamp(end) if end is not None else "",
                     "" if b is not None else VOICE_NAMES.get(l.voice, l.voice),
                     ("↳  " + b.text) if b is not None else l.text]
            for c, v in enumerate(cells):
                item = self.table.item(r, c)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(r, c, item)
                item.setText(v)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if c and not (c == 3 and b is not None):
                    flags |= Qt.ItemIsEditable
                item.setFlags(flags)
                f = item.font()
                f.setItalic(b is not None)
                item.setFont(f)
                if c in (0, 3):
                    item.setTextAlignment(Qt.AlignCenter)
                if c in (1, 2):
                    has = (item_line.start if c == 1 else item_line.end) is not None
                    item.setForeground(QColor(theme.TEXT if has else theme.FAINT))
                    item.setToolTip("" if c == 1 else "Empty: shown until the next line starts")
                elif c == 3:
                    item.setForeground(QColor(voice_colors(l.voice)[1] if l.voice else theme.FAINT))
                elif c == 4:
                    item.setForeground(QColor(ADLIB[1] if b is not None else theme.TEXT))
            if l is sel and b is None:
                selected_row = r
        if selected_row is not None:
            self.table.selectRow(selected_row)
        self._current_row = -1
        self._mark_rows()
        self.table.blockSignals(False)
        self._updating = False
        empty = "Type or paste the lyrics in the Text tab first."
        if not self.doc.lines:
            self.status.setText("Marked as instrumental." if self.doc.instrumental else empty)
        elif self.status.text() == empty:
            self.status.setText("")

    def _row_of(self, line):
        return next((r for r, (l, b) in enumerate(self._rows) if l is line and b is None), None)

    def _mark_rows(self, current=None):
        """♪ marks the line playing now, › the next one Enter will time."""
        blocked = self.table.blockSignals(True)
        cursor_line = self.doc.lines[self.cursor] if self.cursor < len(self.doc.lines) else None
        for r, (l, b) in enumerate(self._rows):
            item = self.table.item(r, 0)
            if item is None or b is not None:
                continue
            mark = "♪" if r == current else "›" if l is cursor_line else ""
            item.setText(mark)
            item.setForeground(QColor(theme.ACCENT_HI if r == current else theme.WARN))
            f = self.table.item(r, 4).font()
            f.setBold(r == current)
            self.table.item(r, 4).setFont(f)
        self.table.blockSignals(blocked)

    def _cell_edited(self, item):
        if self._updating:
            return
        r, c = item.row(), item.column()
        if r >= len(self._rows):
            return
        line, b = self._rows[r]
        target = b if b is not None else line
        text = item.text().strip()
        if c == 4:
            text = text.removeprefix("↳").strip() if b is not None else text
            if text == target.text:
                return
            if not text:
                if b is not None:
                    line.bg = [x for x in line.bg if x is not b]
                else:
                    self.doc.lines.remove(line)
            else:
                target.text, target.words = text, []
                if b is None and self.mode.currentData():
                    line.ensure_words()
        elif c == 3:
            v = {"": "", "—": "", "-": "", "0": ""}.get(text, f"v{text}" if text.isdigit() else None)
            if v is None:
                self.status.setText("The singer is a number: 1, 2… (or empty).")
                self._fill_table()
                return
            line.voice = v
        elif c in (1, 2):
            t = None if text in ("", "—") else lyrics.parse_time(text)
            if text not in ("", "—") and t is None:
                self.status.setText(f"“{text}” isn't a time. Use minutes:seconds, like 1:23.45.")
                self._fill_table()
                return
            if c == 1:
                if t is None:
                    target.start = target.end = None
                    target.words = []
                elif target.start is not None:
                    target.shift(t - target.start)       # words (and ad-libs) move along
                else:
                    target.start = t
                    if target.words:
                        target.words[0].start = t
            else:
                if t is not None and target.start is not None and t <= target.start:
                    self.status.setText("It has to end after it starts.")
                    self._fill_table()
                    return
                target.end = t
                if b is not None and b.words:
                    b.words[-1].end = t
        self.commit()

    def _row_selected(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or self._updating:
            return
        r = rows[0].row()
        if r < len(self._rows):
            line, b = self._rows[r]
            self.timeline.selected = line
            self.cursor, self.wcursor = self.doc.lines.index(line), 0
            self._mark_rows(self._current_row)
            start = (b or line).start
            if start is not None:
                self.timeline.show_time(start, 0.3)
            self.timeline.update()
            self._say_next()

    def _timeline_clicked(self, line):
        r = self._row_of(line)
        if r is None:
            return
        self.table.blockSignals(True)
        self.table.selectRow(r)
        self.table.blockSignals(False)
        self.table.scrollToItem(self.table.item(r, 4))
        self.cursor, self.wcursor = self.doc.lines.index(line), 0
        self._mark_rows(self._current_row)
        self._say_next()

    def _table_menu(self, pos):
        r = self.table.rowAt(pos.y())
        if r < 0 or r >= len(self._rows):
            return
        line, b = self._rows[r]
        menu = QMenu(self)
        if b is not None:
            menu.addAction("Remove this ad-lib", lambda: self._remove_adlib(line, b))
        else:
            singer = menu.addMenu("Sung by")
            for v, name in (("", "Not set"), ("v1", "Singer 1"), ("v2", "Singer 2"), ("v3", "Singer 3")):
                act = singer.addAction(name, lambda v=v: self._set_voice(line, v))
                act.setCheckable(True)
                act.setChecked(line.voice == v)
            menu.addAction("Add an ad-lib at the playhead…", lambda: self.add_adlib(line, self.player.position()))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _set_voice(self, line, voice):
        line.voice = voice
        self.commit()

    def _remove_adlib(self, line, b):
        line.bg = [x for x in line.bg if x is not b]
        self.commit()

    def add_adlib(self, line, t):
        """A background vocal / ad-lib going with a line, starting at t."""
        text, ok = QInputDialog.getText(self, "Add an ad-lib", f"Ad-lib with “{line.text}”, starting at "
                                        f"{lyrics.stamp(t)} (e.g. “(yeah)”):")
        if ok and text.strip():
            line.bg.append(lyrics.Line(text.strip(), t))
            line.bg.sort(key=lambda b: b.start if b.start is not None else 1e9)
            self.commit()

    # --------------------------------------------------------------- playback

    def toggle_play(self):
        if self.song:
            self.player.toggle()

    def _play_state(self, playing):
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", "#ffffff"))
        if playing:
            self.timer.start()
        else:
            self.timer.stop()
            self._tick()

    def _tick(self):
        pos = self.player.position()
        if self.player.playing and pos >= self.player.duration - 0.01:
            self.player.pause()
            pos = self.player.position()
        self.time_label.setText(f"{lyrics.stamp(pos)} / {lyrics.stamp(self.player.duration)}")
        self.timeline.set_playhead(pos, self.player.playing)
        self.preview.set_time(self.doc, pos)
        current = None
        timed = self.doc.timed()
        for i, l in enumerate(timed):
            if l.start > pos:
                break
            end = l.end_at(timed[i + 1] if i + 1 < len(timed) else None)
            current = self._row_of(l) if end is None or pos < end else None
        if current != self._current_row:
            self._current_row = current
            self._mark_rows(current)
            if current is not None and self.player.playing:
                self.table.scrollToItem(self.table.item(current, 4), QAbstractItemView.PositionAtCenter)

    def seek(self, t):
        self.player.seek(t)
        self._tick()

    # --------------------------------------------------------------- tapping

    def eventFilter(self, obj, e):
        if e.type() != QEvent.KeyPress or self.tabs.currentIndex() != 1 or not self.track \
                or self.pages.currentIndex() != 0 or not self.isVisible():
            return False
        focus = QApplication.focusWidget()
        if QApplication.activeModalWidget() is not None or focus is None or focus.window() is not self.window():
            return False
        if focus is not self and not self.isAncestorOf(focus):
            return False
        if isinstance(focus, (QLineEdit, QAbstractSpinBox, QPlainTextEdit)) \
                or self.table.state() == QAbstractItemView.EditingState:
            return False
        if self._key(e):
            if not self.sync_tab.isAncestorOf(focus) and focus is not self.sync_tab:
                self.sync_tab.setFocus()       # e.g. the tab bar had it: keep the keys coming here
            return True
        return False

    def _key(self, e):
        k, mods = e.key(), e.modifiers()
        ctrl, shift = bool(mods & Qt.ControlModifier), bool(mods & Qt.ShiftModifier)
        if e.isAutoRepeat() and k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_E, Qt.Key_Space):
            return True                         # holding a key down mustn't tap many lines
        if ctrl and k == Qt.Key_Z:
            self.redo() if shift else self.undo()
        elif ctrl and k == Qt.Key_Y:
            self.redo()
        elif ctrl or mods & Qt.AltModifier:
            return False
        elif k == Qt.Key_Space:
            self.toggle_play()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            self.tap()
        elif k == Qt.Key_E:
            self.tap_end()
        elif k == Qt.Key_Up:
            self.tap_back()
        elif k in (Qt.Key_Left, Qt.Key_Right):
            step = (0.1 if shift else 2.0) * (1 if k == Qt.Key_Right else -1)
            self.seek(self.player.position() + step)
        elif k in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            self._set_selected("start" if k == Qt.Key_BracketLeft else "end")
        elif k in (Qt.Key_Delete, Qt.Key_Backspace):
            self._clear_selected()
        elif k == Qt.Key_V:
            line = self._selected_line()
            if line is not None:
                self._set_voice(line, VOICE_CYCLE[(VOICE_CYCLE.index(line.voice) + 1) % len(VOICE_CYCLE)]
                                if line.voice in VOICE_CYCLE else "")
        elif k == Qt.Key_A:
            line = self._selected_line()
            if line is not None:
                self.add_adlib(line, self.player.position())
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.timeline.zoom(1.5)
        elif k == Qt.Key_Minus:
            self.timeline.zoom(1 / 1.5)
        else:
            return False
        return True

    def _now(self):
        return max(0.0, self.player.position() - self.delay.value())

    def _say_next(self):
        """Show what the next Enter will time, and keep that line in view."""
        lines = self.doc.lines
        if self.cursor >= len(lines):
            if lines:
                self.status.setText("Every line has a time. Click a line to tap again from there.")
            return
        line = lines[self.cursor]
        r = self._row_of(line)
        if r is not None:
            self.table.scrollToItem(self.table.item(r, 4), QAbstractItemView.EnsureVisible)
        if self.mode.currentData():
            words = line.ensure_words()
            word = words[min(self.wcursor, len(words) - 1)].text.strip()
            self.next_text = f"Next: “{word}” in “{line.text}”"
        else:
            self.next_text = f"Next: “{line.text}”"

    def _done(self, msg):
        self._say_next()
        self.status.setText(f"{msg}   ·   {getattr(self, 'next_text', '')}".strip(" ·"))

    def tap(self):
        lines = self.doc.lines
        if self.cursor >= len(lines):
            self.status.setText("Every line has a time. Click a line to tap again from there.")
            return
        t = self._now()
        line = lines[self.cursor]
        if self.mode.currentData() and self.wcursor > 0:
            words = line.ensure_words()
            before = max((w.start for w in words[:self.wcursor] if w.start is not None), default=line.start)
            if t <= before + MIN_WORD:
                self.status.setText("Too soon after the previous word: that tap was skipped.")
                return
            words[self.wcursor].start = t
            for w in words[:self.wcursor]:
                if w.end is not None and w.end > t:
                    w.end = None
            word = words[self.wcursor].text.strip()
            self._advance_word(words)
            self.commit()
            self._done(f"“{word}” at {lyrics.stamp(t)}")
            return
        prev = next((l for l in reversed(lines[:self.cursor]) if l.start is not None), None)
        if prev is not None and t <= prev.start + MIN_LINE:
            self.status.setText("That's before the previous line starts, so the tap was skipped. Press ↑ to go "
                                "back, or click a line.")
            return
        if prev is not None and prev.end is not None and prev.end > t:
            prev.end = None
        for later in lines[self.cursor + 1:]:      # left over from an earlier try: tapped again next
            if later.start is not None and later.start <= t:
                later.start = later.end = None
                for w in later.words:
                    w.start = w.end = None
        if self.mode.currentData():
            words = line.ensure_words()
            old = line.start
            for w in words[1:]:
                w.start = w.end = None
            words[0].end = None
            line.start = words[0].start = t
            for b in line.bg:                      # ad-libs keep their place relative to the line
                if old is not None and b.start is not None:
                    b.shift(t - old)
            if line.end is not None and line.end <= t:
                line.end = None
            self._advance_word(words)
        else:
            if line.start is not None:
                line.shift(t - line.start)          # its words and ad-libs move along
            else:
                line.start = t
                line.words = []
            nxt = next((l for l in lines[self.cursor + 1:] if l.start is not None), None)
            if line.end is not None and (line.end <= t or nxt is not None and line.end > nxt.start):
                line.end = None
            self.cursor += 1
        self.commit()
        self._done(f"“{line.text}” starts at {lyrics.stamp(line.start)}")

    def _advance_word(self, words):
        self.wcursor += 1
        if self.wcursor >= len(words):
            self.cursor += 1
            self.wcursor = 0

    def tap_end(self):
        t = self._now()
        lines = self.doc.lines
        if self.mode.currentData() and self.wcursor > 0:
            target = lines[self.cursor]
            self.cursor += 1
            self.wcursor = 0
        else:
            target = next((l for l in reversed(lines[:self.cursor]) if l.start is not None), None)
        if target is None or target.start is None:
            return
        last = max((w.start for w in target.words if w.start is not None), default=target.start)
        if t <= last + MIN_LINE:
            self.status.setText("That's before the line has started.")
            return
        nxt = next((l for l in lines[lines.index(target) + 1:] if l.start is not None), None)
        if nxt is not None and nxt.start < t:
            self.status.setText("The next line already starts before that.")
            return
        target.end = t
        self.commit()
        self._done(f"“{target.text}” ends at {lyrics.stamp(t)}")

    def tap_back(self):
        if self.mode.currentData() and self.wcursor > 0:
            self.wcursor -= 1
        elif self.cursor > 0:
            self.cursor -= 1      # in word mode the whole line is tapped again
        line = self.doc.lines[self.cursor] if self.cursor < len(self.doc.lines) else None
        if line is not None and line.start is not None:
            self.seek(max(0.0, line.start - 3))
        self._mark_rows(self._current_row)
        self._say_next()
        self.status.setText(getattr(self, "next_text", ""))

    def _selected_line(self):
        rows = self.table.selectionModel().selectedRows()
        return self._rows[rows[0].row()][0] if rows and rows[0].row() < len(self._rows) else None

    def _set_selected(self, what):
        line = self._selected_line()
        if line is None:
            return
        t = self.player.position()
        if line.start is None:
            if what == "end":
                return
            line.start = t
            self.commit()
            return
        if not self.timeline._set(line, what, t):
            self.status.setText(f"The {what} can't go there: it would overlap the line before or after.")

    def _clear_selected(self):
        line = self._selected_line()
        if line is None or line.start is None:
            return
        line.start = line.end = None
        for w in line.words:
            w.start = w.end = None
        self.commit()

    def shift_all(self):
        if not self.doc.synced:
            return
        secs, ok = QInputDialog.getDouble(self, "Shift all lines", "Seconds (negative: earlier, positive: later)",
                                          0.0, -600, 600, 2)
        if not ok or not secs:
            return
        secs = max(secs, -self.doc.timed()[0].start)
        for l in self.doc.timed():
            l.shift(secs)
        self.commit()

    def clear_times(self):
        if not self.doc.synced or QMessageBox.question(
                self, "Clear times", "Remove every line's timing and start syncing over?") != QMessageBox.Yes:
            return
        self.doc.clear_timings()
        if self.mode.currentData():
            for l in self.doc.lines:
                l.ensure_words()
        self.cursor = self.wcursor = 0
        self.commit()

    # ------------------------------------------------------------------ actions

    def find_online(self):
        if not self.track:
            return
        dlg = OnlineDialog(self, self.bg, self.track)
        if dlg.exec() != QDialog.Accepted or not dlg.text:
            return
        if (self.doc.lines or self.doc.instrumental) and QMessageBox.question(
                self, "Replace lyrics", "Replace the lyrics you have with these?") != QMessageBox.Yes:
            return
        doc = lyrics.parse(dlg.text)
        self._replace_doc(doc)
        self.cursor = next((i for i, l in enumerate(doc.lines) if l.start is None), 0)

    def remove(self):
        if not self.track or QMessageBox.question(
                self, "Remove lyrics", f"Delete the lyrics of “{self.track.title}”? This removes them from the "
                "music file and its .lrc file.") != QMessageBox.Yes:
            return
        self._replace_doc(lyrics.Lyrics())
        self.save()

    def _write(self, path, text, old):
        """Save lyrics to a song (and its Opus copy); on a follower they're queued for the main PC too."""
        lyrics.save(path, text, SETTINGS.get("lyrics_save", "embed"), SETTINGS.library, SETTINGS.opus)
        if SETTINGS["role"] == "follower":
            self.win.follower.record_tag_edit(path, {"lyrics": old}, {"lyrics": text})

    def save(self):
        if not self.track:
            return False
        self._flush()
        if not EXCLUSIVE.acquire(blocking=False):
            QMessageBox.information(self, "Busy", "A job is running. Save the lyrics when it's finished.")
            return False
        text = lyrics.serialize(self.doc)
        path = self.track.path
        try:
            self._write(path, text, self.loaded_text)
        except Exception as e:
            QMessageBox.warning(self, "Lyrics", f"Couldn't save the lyrics:\n{e}")
            return False
        finally:
            EXCLUSIVE.release()
        self.loaded_text = text
        self.store.reread([path])
        self._update_title()
        kind = lyrics.KIND_LABELS[self.doc.kind].lower()
        self.win.statusBar().showMessage(f"Saved the lyrics of {self.track.title}" +
                                         (f" ({kind})" if self.doc.kind else " (removed)"), 4000)
        return True

    def _save_from_review(self, path, text, what):
        """"Use this match" / "Undo" in the Added online tab."""
        from pathlib import Path
        path = Path(path)
        if self.track and self.track.path == path and not self._confirm_discard():
            return
        try:
            self._write(path, text, lyrics.load(path)[0])
        except Exception as e:
            QMessageBox.warning(self, "Lyrics", f"Couldn't save the lyrics:\n{e}")
            return
        self.store.reread([path])
        if self.track and self.track.path == path:
            self._set_track(self.track)
        self.win.statusBar().showMessage(f"{what}: {path.stem}", 4000)

    def _review_title(self):
        n = self.review.to_check()
        self.pages.setTabText(1, f"Added online ({n} to check)" if n else "Added online")

    def find_many(self):
        tracks = self.shown_tracks()
        if not tracks:
            return
        dlg = BulkDialog(self, tracks)
        if dlg.exec() != QDialog.Accepted:
            return
        if not self._confirm_discard():
            return
        want, mode = dlg.want, SETTINGS.get("lyrics_save", "embed")
        lib, opus = SETTINGS.library, SETTINGS.opus
        on_saved = None
        if SETTINGS["role"] == "follower":
            on_saved = lambda p, old, new: self.win.follower.record_tag_edit(p, {"lyrics": old}, {"lyrics": new})  # noqa: E731

        def job(ctx):
            ctx.log(f"Looking up lyrics on LRCLIB for {len(tracks)} song(s)…", "head")
            counts = lyrics.fetch_many(ctx, tracks, want, mode, lib, opus, on_saved=on_saved)
            ctx.log(_summary(counts) or "Nothing to do: the songs already have lyrics.", "ok")
            return counts

        def done(counts):
            self.review.reload()
            self._review_title()
            counts = counts or {}
            if counts.get("stopped") or counts.get(lyrics.FAILED):
                self.review.filter.setCurrentIndex(1)       # everything, so the failed ones show
                QMessageBox.warning(self, "Finding lyrics",
                                    (f"Stopped early: {counts['stopped']}\n\n" if counts.get("stopped") else "")
                                    + f"{_summary(counts)}.\n\nSongs that couldn't be looked up are listed as "
                                    "“Couldn't look up” in the Added online tab, with the reason; they're tried "
                                    "again next time.")
            self.store.reread([t.path for t in tracks])
            if self.track and any(t.path == self.track.path for t in tracks) and not self.dirty:
                self._set_track(self.track)

        self.pages.setCurrentIndex(1)
        self.jobs.start("Finding lyrics", job, log=self.review.log, on_done=done)

    def move_files(self):
        tracks = self.shown_tracks()
        if not tracks:
            return
        dlg = FilesDialog(self, tracks)
        if dlg.exec() != QDialog.Accepted or not self._confirm_discard():
            return
        to, keep = dlg.to, dlg.keep.isChecked()
        lib, opus = SETTINGS.library, SETTINGS.opus

        def job(ctx):
            moved = 0
            for i, t in enumerate(tracks, 1):
                ctx.check()
                ctx.progress(i, len(tracks), f"Moving lyrics {i} / {len(tracks)}")
                try:
                    moved += lyrics.relocate(t.path, to, keep, lib, opus)
                except Exception as e:
                    ctx.log(f"  {t.path.name}: {e}", "error")
            where = ".lrc files" if to == "file" else "the music files"
            ctx.log(f"Moved the lyrics of {moved} song(s) into {where}.", "ok")
            return moved

        def done(_):
            self.store.reread([t.path for t in tracks])
            if self.track and not self.dirty:
                self._set_track(self.track)

        self.pages.setCurrentIndex(1)
        self.jobs.start("Moving lyrics", job, log=self.review.log, on_done=done)
