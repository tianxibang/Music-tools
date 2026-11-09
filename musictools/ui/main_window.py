"""Main window: sidebar navigation, shared library store, the job indicator and the folder watcher."""

import threading
import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QProgressBar,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..core import library, pipeline
from ..core.jobs import EXCLUSIVE
from ..core.tags import read_track
from ..core.util import APP_NAME, APP_VERSION, AUDIO_EXTS, SETTINGS
from . import icons, theme
from .widgets import JobManager, PageHeader, label


class Background(QObject):
    """Run fn() on a thread and deliver the result to callback on the UI thread."""
    _done = Signal(object, object)

    def __init__(self):
        super().__init__()
        self._done.connect(lambda cb, res: cb(res), Qt.QueuedConnection)

    def run(self, fn, callback=None):
        def worker():
            try:
                res = fn()
            except Exception as e:
                res = e
            if callback:
                self._done.emit(callback, res)
        threading.Thread(target=worker, daemon=True).start()


class LibraryStore(QObject):
    """Tracks of the library folder, read once and shared by the tag, health, art and playlist pages."""
    changed = Signal()
    updated = Signal(list)
    loadingChanged = Signal(bool)

    def __init__(self, bg):
        super().__init__()
        self.bg = bg
        self.tracks = []
        self.loading = False
        self.loaded_once = False

    def reload(self):
        if self.loading:
            return
        self.loading = True
        self.loadingChanged.emit(True)
        self.bg.run(lambda: library.scan(SETTINGS.library, None, AUDIO_EXTS), self._loaded)

    def _loaded(self, tracks):
        self.loading = False
        self.loaded_once = True
        self.tracks = tracks if isinstance(tracks, list) else []
        self.loadingChanged.emit(False)
        self.changed.emit()

    def reread(self, paths):
        """Refresh specific tracks after their tags changed (cheap, keeps selection)."""
        paths = set(paths)
        for t in self.tracks:
            if t.path in paths and t.path.exists():
                t.__dict__.update(read_track(t.path).__dict__)
        self.updated.emit(list(paths))


class Page(QWidget):
    title = ""
    subtitle = ""
    icon = "dashboard"
    scroll = True

    def __init__(self, win, embedded=False):
        """embedded: shown inside another page, so no header, margins or scroll area of its own."""
        super().__init__()
        self.win, self.jobs, self.store, self.bg = win, win.jobs, win.store, win.bg
        self.locked = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setObjectName("content")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(*((0, 0, 0, 0) if embedded else (34, 28, 34, 28)))
        self.body.setSpacing(16)
        if not embedded:
            self.body.addWidget(PageHeader(self.title, self.subtitle))
        if self.scroll and not embedded:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setWidget(content)
            area.setFrameShape(QScrollArea.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            outer.addWidget(area)
        else:
            outer.addWidget(content)
        self.jobs.busyChanged.connect(self._busy)

    def add(self, w, stretch=0):
        if isinstance(w, QWidget):
            self.body.addWidget(w, stretch)
        else:
            self.body.addLayout(w, stretch)
        return w

    def lock(self, *widgets):
        """Disable these while any job runs."""
        self.locked.extend(widgets)
        return widgets[0] if len(widgets) == 1 else widgets

    def _busy(self, busy):
        for w in self.locked:
            w.setEnabled(not busy)

    def on_show(self):
        pass


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)

        self.bg = Background()
        self.jobs = JobManager()
        self.store = LibraryStore(self.bg)

        from .pages import (convert, dashboard, enhance, health, importer, organize, phone, playlists, settings,
                            tags)
        self.pages = [dashboard.DashboardPage(self), convert.ConvertPage(self), importer.ImportPage(self),
                      phone.PhonePage(self), tags.TagsPage(self), health.HealthPage(self), enhance.EnhancePage(self),
                      playlists.PlaylistsPage(self), organize.OrganizePage(self), settings.SettingsPage(self)]

        root = QWidget()
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        for p in self.pages:
            self.stack.addWidget(p)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)

        self.jobs.busyChanged.connect(self._job_busy)
        self.jobs.started.connect(lambda name: self.job_title.setText(name))
        self.jobs.progressed.connect(self._job_progress)

        # Folder watcher: poll the library and run the pipeline when new FLACs settle
        self._snapshot = None
        self._stable = 0
        self.watch_status = "Off"
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(15000)
        self.watch_timer.timeout.connect(self._watch_tick)
        self.set_watch(SETTINGS["watch"])

        self.store.reload()

    # --- sidebar

    def _sidebar(self):
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(232)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 22, 0, 16)
        col.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(22, 0, 16, 14)
        brand.setSpacing(11)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(34, 34))
        brand.addWidget(logo)
        txt = QVBoxLayout()
        txt.setSpacing(0)
        txt.addWidget(label(APP_NAME, "brand"))
        txt.addWidget(label(f"Library toolkit · v{APP_VERSION}", "brandSub"))
        brand.addLayout(txt)
        brand.addStretch(1)
        col.addLayout(brand)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setCursor(Qt.PointingHandCursor)
        self.nav.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sections = {4: "LIBRARY", 9: "APP"}
        for i, p in enumerate(self.pages):
            if i in sections:
                sep = QListWidgetItem(sections[i])
                sep.setFlags(Qt.NoItemFlags)
                sep.setForeground(Qt.gray)
                sep.setSizeHint(QSize(0, 34))
                f = sep.font()
                f.setPointSizeF(7.8)
                f.setBold(True)
                sep.setFont(f)
                self.nav.addItem(sep)
            item = QListWidgetItem(icons.icon(p.icon, theme.MUTED, theme.ACCENT_HI), p.title)
            item.setData(Qt.UserRole, i)
            item.setSizeHint(QSize(0, 40))
            self.nav.addItem(item)
        self.nav.currentItemChanged.connect(self._nav_changed)
        col.addWidget(self.nav, 1)

        self.job_box = QWidget()
        self.job_box.setObjectName("jobBox")
        jl = QVBoxLayout(self.job_box)
        jl.setContentsMargins(12, 10, 12, 12)
        jl.setSpacing(6)
        self.job_title = label("", "jobTitle")
        self.job_text = label("Working…", "jobText")
        self.job_bar = QProgressBar()
        self.job_bar.setTextVisible(False)
        jl.addWidget(self.job_title)
        jl.addWidget(self.job_bar)
        jl.addWidget(self.job_text)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(12, 0, 12, 0)
        wrap.addWidget(self.job_box)
        col.addLayout(wrap)
        self.job_box.hide()
        return side

    def _nav_changed(self, item, _prev):
        if item is None:
            return
        idx = item.data(Qt.UserRole)
        if idx is None:
            return
        self.stack.setCurrentIndex(idx)
        self.pages[idx].on_show()

    def go(self, page_cls):
        for row in range(self.nav.count()):
            idx = self.nav.item(row).data(Qt.UserRole)
            if idx is not None and isinstance(self.pages[idx], page_cls):
                self.nav.setCurrentRow(row)
                return self.pages[idx]

    def page(self, page_cls):
        return next(p for p in self.pages if isinstance(p, page_cls))

    def _job_busy(self, busy):
        self.job_box.setVisible(busy)
        if busy:
            self.job_bar.setRange(0, 0)
            self.job_text.setText("Working…")

    def _job_progress(self, done, total, text):
        self.job_bar.setRange(0, max(total, 1))
        self.job_bar.setValue(done)
        self.job_text.setText(text or f"{done} / {total}")

    # --- watch folder

    def set_watch(self, on):
        SETTINGS["watch"] = bool(on)
        SETTINGS.save()
        self._snapshot = None
        if on:
            self.watch_timer.start()
            self.watch_status = "Watching for new FLAC files…"
            QTimer.singleShot(1000, self._watch_tick)
        else:
            self.watch_timer.stop()
            self.watch_status = "Off"
        self._watch_changed()

    def _watch_changed(self):
        from .pages.dashboard import DashboardPage
        self.page(DashboardPage).update_watch(self.watch_status)

    def _watch_tick(self):
        if self.jobs.busy or EXCLUSIVE.locked():  # a job from the desktop or the phone app is running
            return
        lib = SETTINGS.library

        def snap():
            if not lib.is_dir():
                return None
            return frozenset((p.as_posix(), p.stat().st_size) for p in lib.rglob("*.flac") if p.is_file())

        self.bg.run(snap, self._watch_compare)

    def _watch_compare(self, snapshot):
        if isinstance(snapshot, Exception) or snapshot is None:
            return
        if snapshot != self._snapshot:
            first = self._snapshot is None
            self._snapshot, self._stable = snapshot, 0
            if not first:
                self.watch_status = "Change detected — waiting for files to finish copying…"
                self._watch_changed()
            else:
                self._stable = 1
                self._watch_maybe_run(snapshot)
            return
        self._stable += 1
        if self._stable == 1:
            self._watch_maybe_run(snapshot)

    def _watch_maybe_run(self, snapshot):
        from ..core.util import opus_path_for
        lib, opus = SETTINGS.library, SETTINGS.opus
        pending = [p for p, _ in snapshot if not opus_path_for(p, lib, opus).exists()]
        if not pending:
            self.watch_status = f"Up to date · checked {time.strftime('%H:%M')}"
            self._watch_changed()
            return
        steps = ["convert", "art", "loudness", "playlists"] + (["sync"] if SETTINGS["watch_sync"] else [])
        from .pages.dashboard import DashboardPage
        dash = self.page(DashboardPage)
        self.watch_status = f"{len(pending)} new file(s) — running pipeline…"
        self._watch_changed()

        def done(_):
            self.watch_status = f"Processed {len(pending)} new file(s) at {time.strftime('%H:%M')}"
            self._watch_changed()
            self.store.reload()
            dash.refresh()

        self.jobs.start("Auto-convert", pipeline.run, SETTINGS, steps, log=dash.log, on_done=done)

    def closeEvent(self, e):
        if self.jobs.busy:
            from PySide6.QtWidgets import QMessageBox
            if QMessageBox.question(self, "Quit", f"“{self.jobs.name}” is still running. Stop it and quit?") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.jobs.cancel()
        for p in self.pages:
            if hasattr(p, "shutdown"):
                p.shutdown()
        SETTINGS.save()
        e.accept()
