"""Reusable widgets and the background job runner."""

import html
import threading
import traceback

from PySide6.QtCore import (QAbstractTableModel, QEasingCurve, QModelIndex, QObject, QPropertyAnimation,
                            QSortFilterProxyModel, Qt, Signal, Property, QSize, QRectF)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
                               QGraphicsDropShadowEffect, QHeaderView, QTableView, QAbstractItemView)

from ..core.jobs import EXCLUSIVE, Cancelled, Context
from ..core.util import MUSIC, human_duration
from . import icons, theme


# --------------------------------------------------------------------------- jobs


class JobContext(QObject, Context):
    logged = Signal(str, str)
    progressed = Signal(int, int, str)
    _ask = Signal(str, str, object)

    def __init__(self):
        QObject.__init__(self)
        Context.__init__(self)
        self._ask.connect(self._show_ask, Qt.QueuedConnection)

    def log(self, msg, level="info"):
        self.logged.emit(msg, level)

    def progress(self, done, total, text=""):
        self.progressed.emit(done, total, text)

    def ask(self, title, msg):
        box = {"event": threading.Event(), "answer": False}
        self._ask.emit(title, msg, box)
        box["event"].wait()
        return box["answer"]

    def _show_ask(self, title, msg, box):
        box["answer"] = QMessageBox.question(None, title, msg) == QMessageBox.Yes
        box["event"].set()


class JobManager(QObject):
    """Runs one job at a time on a worker thread (the tools all touch the same folders)."""
    busyChanged = Signal(bool)
    started = Signal(str)
    progressed = Signal(int, int, str)
    _finished = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.ctx = None
        self.name = ""
        self._finished.connect(self._on_finished, Qt.QueuedConnection)

    @property
    def busy(self):
        return self.ctx is not None

    def start(self, name, fn, *args, log=None, on_done=None):
        if self.busy:
            QMessageBox.information(None, "Busy", f"“{self.name}” is still running. Wait for it or cancel it first.")
            return False
        if not EXCLUSIVE.acquire(blocking=False):
            QMessageBox.information(None, "Busy", "A job started from the phone app is running on this PC. "
                                    "Wait for it to finish first.")
            return False
        ctx = JobContext()
        self.ctx, self.name = ctx, name
        if log:
            log.attach(ctx)
        ctx.progressed.connect(self.progressed)
        self.busyChanged.emit(True)
        self.started.emit(name)

        def worker():
            result = error = None
            try:
                result = fn(ctx, *args)
            except Cancelled:
                ctx.log("Cancelled.", "warn")
            except Exception as e:
                error = e
                ctx.log(f"Error: {e}", "error")
                traceback.print_exc()
            self._finished.emit(result, error, on_done)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def cancel(self):
        if self.ctx:
            self.ctx.cancel()

    def _on_finished(self, result, error, on_done):
        EXCLUSIVE.release()
        self.ctx = None
        self.name = ""
        self.busyChanged.emit(False)
        if on_done and error is None:
            on_done(result)


# ------------------------------------------------------------------------ layout


def vbox(*widgets, spacing=10, margins=(0, 0, 0, 0)):
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        _add(lay, w)
    return lay


def hbox(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        _add(lay, w)
    return lay


def _add(lay, w):
    if w is None:
        lay.addStretch(1)
    elif isinstance(w, int):
        lay.addSpacing(w)
    elif isinstance(w, (QHBoxLayout, QVBoxLayout)) or hasattr(w, "addWidget") and not isinstance(w, QWidget):
        lay.addLayout(w)
    else:
        lay.addWidget(w)


def trash_file(path):
    from ..core.util import trash
    return trash(path)


def label(text, name=None, wrap=False):
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def button(text, kind=None, icon=None, tip=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if icon:
        color = "#ffffff" if kind in ("primary", "big") else theme.TEXT
        b.setIcon(icons.icon(icon, color))
        b.setIconSize(QSize(16, 16))
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class Card(QFrame):
    def __init__(self, title=None, subtitle=None, icon=None):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 20)
        self.lay.setSpacing(12)
        self.lay.setAlignment(Qt.AlignTop)
        if title:
            head = QHBoxLayout()
            head.setSpacing(10)
            if icon:
                ic = QLabel()
                ic.setPixmap(icons.pixmap(icon, theme.ACCENT_HI, 18))
                head.addWidget(ic)
            head.addWidget(label(title, "cardTitle"))
            head.addStretch(1)
            self.head = head
            self.lay.addLayout(head)
            if subtitle:
                self.lay.addWidget(label(subtitle, "cardSub", wrap=True))
                self.lay.addSpacing(-4)

    def add(self, w):
        _add(self.lay, w)
        return w


class StatCard(QFrame):
    def __init__(self, icon, caption, value="—"):
        super().__init__()
        self.setObjectName("card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        ic = QLabel()
        ic.setObjectName("statIcon")
        ic.setFixedSize(40, 40)
        ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(icons.pixmap(icon, theme.ACCENT_HI, 20))
        lay.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = label(value, "statValue")
        self.caption = label(caption, "statLabel")
        col.addWidget(self.value)
        col.addWidget(self.caption)
        lay.addLayout(col)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, value, caption=None):
        self.value.setText(str(value))
        if caption:
            self.caption.setText(caption)


class PageHeader(QWidget):
    def __init__(self, title, subtitle=""):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(2)
        lay.addWidget(label(title, "pageTitle"))
        if subtitle:
            lay.addWidget(label(subtitle, "pageSub", wrap=True))


class Toggle(QAbstractButton):
    """An animated on/off switch."""

    def __init__(self, text="", checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _get_knob(self):
        return self._pos

    def _set_knob(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def sizeHint(self):
        w = 40 + (10 + self.fontMetrics().horizontalAdvance(self.text()) if self.text() else 0)
        return QSize(w, 24)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(1, 3, 36, 18)
        off, on = QColor(theme.BORDER_HI), QColor(theme.ACCENT)
        t = self._pos
        color = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            color.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(track, 9, 9)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(4 + 18 * t, 6, 12, 12))
        if self.text():
            p.setPen(QColor(theme.TEXT if self.isEnabled() else theme.FAINT))
            p.drawText(QRectF(48, 0, self.width() - 48, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class PathPicker(QWidget):
    def __init__(self, value="", placeholder="Choose a folder…", file=False):
        super().__init__()
        self.file = file
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText("Auto-detect" if file and placeholder == "Choose a folder…" else placeholder)
        browse = button("Browse", icon="folder")
        browse.clicked.connect(self.browse)
        self.setLayout(hbox(self.edit, browse))

    def browse(self):
        start = self.edit.text() or str(MUSIC)
        if self.file:
            path, _ = QFileDialog.getOpenFileName(self, "Choose program", start)
        else:
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text().strip()

    def setText(self, t):
        self.edit.setText(t)


class FormGrid(QWidget):
    """Label / field rows with consistent alignment."""

    def __init__(self):
        super().__init__()
        from PySide6.QtWidgets import QGridLayout
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        self.row = 0

    def add(self, text, widget, hint=None):
        lab = label(text, "muted")
        self.grid.addWidget(lab, self.row, 0, Qt.AlignLeft | Qt.AlignVCenter)
        if isinstance(widget, (QHBoxLayout, QVBoxLayout)):
            self.grid.addLayout(widget, self.row, 1)
        elif widget.maximumWidth() < 5000 or isinstance(widget, Toggle):
            # keep fixed-size fields left-aligned instead of letting them float in the column
            self.grid.addLayout(hbox(widget, None), self.row, 1)
        else:
            self.grid.addWidget(widget, self.row, 1)
        self.row += 1
        if hint:
            self.grid.addWidget(label(hint, "hint", wrap=True), self.row, 1)
            self.row += 1
        return widget


class LogPanel(QWidget):
    """Progress bar, status line and coloured log, bound to a running job."""

    def __init__(self, jobs, min_height=180):
        super().__init__()
        self.jobs = jobs
        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setFont(theme.mono_font())
        self.view.setMinimumHeight(min_height)
        self.view.setMaximumBlockCount(20000)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setPlaceholderText("Output will appear here.")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.status = label("", "muted")
        self.cancel_btn = button("Cancel", "ghost", "stop")
        self.cancel_btn.clicked.connect(jobs.cancel)
        self.cancel_btn.hide()
        self.bar.hide()
        lay = vbox(hbox(self.status, None, self.cancel_btn), self.bar, self.view, spacing=8)
        self.setLayout(lay)
        jobs.busyChanged.connect(self._busy)
        self._mine = False

    def attach(self, ctx):
        self.view.clear()
        self._mine = True
        self.bar.setRange(0, 0)
        self.bar.show()
        self.cancel_btn.show()
        self.status.setText("Working…")
        ctx.logged.connect(self.append)
        ctx.progressed.connect(self._progress)

    def append(self, msg, level="info"):
        color = theme.LEVEL_COLORS.get(level, theme.TEXT)
        weight = "font-weight:600;" if level == "head" else ""
        text = html.escape(msg).replace(" ", "&nbsp;") or "&nbsp;"
        self.view.appendHtml(f'<span style="color:{color};{weight}">{text}</span>')

    def _progress(self, done, total, text):
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(done)
        self.status.setText(text or f"{done} / {total}")

    def _busy(self, busy):
        if not busy and self._mine:
            self._mine = False
            self.cancel_btn.hide()
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.status.setText("Finished")


class CoverLabel(QLabel):
    def __init__(self, size=180):
        super().__init__()
        self.size_px = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background:{theme.RAISED}; border-radius:12px; border:1px solid {theme.BORDER};")
        self.clear_cover()

    def clear_cover(self, text=""):
        self.setPixmap(icons.pixmap("music", theme.FAINT, 48))
        self.setToolTip(text)

    def set_data(self, data):
        pm = QPixmap()
        if not data or not pm.loadFromData(data):
            self.clear_cover()
            return
        ratio = self.devicePixelRatioF()
        side = int(self.size_px * ratio)
        pm = pm.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        rounded = QPixmap(side, side)
        rounded.fill(Qt.transparent)
        p = QPainter(rounded)
        p.setRenderHint(QPainter.Antialiasing)
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, side, side), 12 * ratio, 12 * ratio)
        p.setClipPath(path)
        p.drawPixmap((side - pm.width()) // 2, (side - pm.height()) // 2, pm)
        p.end()
        rounded.setDevicePixelRatio(ratio)
        self.setPixmap(rounded)


class BarList(QWidget):
    """Label  ▇▇▇▇▇▇░░░  count rows, for breakdowns."""

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(7)

    def set(self, rows):
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not rows:
            self.lay.addWidget(label("Nothing here", "hint"))
            return
        top = max(c for _, c in rows) or 1
        for name, count in rows:
            row = QWidget()
            name_lab = label(str(name))
            name_lab.setMinimumWidth(110)
            name_lab.setMaximumWidth(220)
            name_lab.setToolTip(str(name))
            bar = QProgressBar()
            bar.setRange(0, top)
            bar.setValue(count)
            count_lab = label(str(count), "muted")
            count_lab.setMinimumWidth(36)
            count_lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.setLayout(hbox(name_lab, bar, count_lab, spacing=10))
            self.lay.addWidget(row)


def shadow(widget, blur=30, alpha=90):
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, 6)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


# ------------------------------------------------------------------- track table


TRACK_COLUMNS = [
    ("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("track", "#"), ("year", "Year"),
    ("genre", "Genre"), ("duration", "Length"), ("art", "Art"), ("file", "File"),
]


class TrackModel(QAbstractTableModel):
    def __init__(self, columns=TRACK_COLUMNS):
        super().__init__()
        self.columns = columns
        self.tracks = []

    def set_tracks(self, tracks):
        self.beginResetModel()
        self.tracks = list(tracks)
        self.endResetModel()

    def refresh_rows(self, paths):
        paths = set(paths)
        for i, t in enumerate(self.tracks):
            if t.path in paths:
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.columns) - 1))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.tracks)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.columns[section][1]
        return None

    def value(self, t, key):
        if key == "title":
            return t.title
        if key == "track":
            return t.track_no or ""
        if key == "year":
            return t.year
        if key == "duration":
            return human_duration(t.duration)
        if key == "art":
            return "✓" if t.has_art else "—"
        if key == "file":
            return t.path.name
        return t.tag(key)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        t = self.tracks[index.row()]
        key = self.columns[index.column()][0]
        if role == Qt.DisplayRole:
            return self.value(t, key)
        if role == Qt.UserRole:  # sort key
            if key == "track":
                return t.track_no or 0
            if key == "duration":
                return t.duration
            return str(self.value(t, key)).lower()
        if role == Qt.ForegroundRole:
            if key == "art":
                return QColor(theme.OK if t.has_art else theme.FAINT)
            if key in ("file", "duration", "track", "year"):
                return QColor(theme.MUTED)
        if role == Qt.TextAlignmentRole and key in ("track", "duration", "art", "year"):
            return int(Qt.AlignCenter)
        if role == Qt.ToolTipRole and key == "file":
            return str(t.path)
        return None


class TrackFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.setSortRole(Qt.UserRole)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.needle = ""

    def set_needle(self, text):
        self.needle = text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if not self.needle:
            return True
        t = self.sourceModel().tracks[row]
        hay = " ".join((t.title, t.artist, t.album, t.tag("genre"), t.path.name)).lower()
        return all(w in hay for w in self.needle.split())


def track_table(model):
    view = QTableView()
    proxy = TrackFilter()
    proxy.setSourceModel(model)
    view.setModel(proxy)
    view.setSortingEnabled(True)
    view.sortByColumn(1, Qt.AscendingOrder)
    view.setSelectionBehavior(QAbstractItemView.SelectRows)
    view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    view.setAlternatingRowColors(True)
    view.setShowGrid(False)
    view.setWordWrap(False)
    view.verticalHeader().hide()
    view.verticalHeader().setDefaultSectionSize(30)
    view.horizontalHeader().setHighlightSections(False)
    view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    view.horizontalHeader().setStretchLastSection(True)
    widths = {"title": 220, "artist": 160, "album": 200, "track": 44, "year": 56, "genre": 110, "duration": 64,
              "art": 44}
    for i, (key, _) in enumerate(model.columns):
        if key in widths:
            view.setColumnWidth(i, widths[key])
    return view, proxy


def selected_tracks(view, proxy, model):
    rows = sorted({proxy.mapToSource(i).row() for i in view.selectionModel().selectedRows()})
    return [model.tracks[r] for r in rows]


def search_box(placeholder="Search title, artist, album…"):
    box = QLineEdit()
    box.setPlaceholderText(placeholder)
    box.setClearButtonEnabled(True)
    box.addAction(icons.icon("search", theme.FAINT), QLineEdit.LeadingPosition)
    box.setMinimumWidth(260)
    return box
