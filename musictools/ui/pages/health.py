from collections import Counter

from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QMessageBox,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ...core import health
from ...core.util import LOSSLESS_EXTS, SETTINGS, human_size, reveal
from .. import theme
from ..main_window import Page
from ..widgets import Card, LogPanel, Toggle, button, hbox, label

KIND_COLORS = {"Corrupt": theme.ERR, "Unreadable": theme.ERR, "Lossy source": theme.WARN, "Duplicate": theme.WARN,
               "Possible duplicate": theme.MUTED, "Missing tags": theme.ACCENT_HI, "No cover art": theme.ACCENT_HI,
               "Low quality": theme.WARN}


def _quality(t):
    if t.path.suffix.lower() in LOSSLESS_EXTS:
        return f"{t.codec.upper()} {t.bits or '?'}-bit / {t.sample_rate / 1000:g} kHz"
    return f"{t.codec.upper()} {t.bitrate} kbps"


class DuplicatesDialog(QDialog):
    """Lists each set of duplicates with every copy but the best one ticked for deletion."""

    def __init__(self, parent, sets):
        super().__init__(parent)
        self.setWindowTitle("Remove duplicates")
        self.resize(1000, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Ticked files go to the trash, so you can still restore them. The best copy of each "
                            "song (lossless, highest resolution, cover art, most tags) is kept by default; tick "
                            "or untick to change that. Songs that only differ in length aren't listed here.",
                            "hint", wrap=True))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Quality", "Size", "Folder"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 170)
        self.tree.setColumnWidth(2, 80)
        self.groups = []
        for tracks in sets:
            top = QTreeWidgetItem([f"{tracks[0].display}  ·  {len(tracks)} copies"])
            top.setFirstColumnSpanned(True)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            self.tree.addTopLevelItem(top)
            children = []
            for i, t in enumerate(tracks):
                item = QTreeWidgetItem(top, [t.path.name, _quality(t), human_size(t.size), str(t.path.parent)])
                item.setData(0, Qt.UserRole, str(t.path))
                item.setToolTip(0, str(t.path))
                item.setToolTip(3, str(t.path.parent))
                item.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
                children.append(item)
            self.groups.append((top, children))
        self.tree.expandAll()
        self.tree.itemChanged.connect(self._update)
        lay.addWidget(self.tree, 1)

        reset = button("Keep best of each", icon="check")
        reset.clicked.connect(lambda: self._set_all(True))
        none = button("Untick all")
        none.clicked.connect(lambda: self._set_all(False))
        self.count = label("", "muted")
        lay.addLayout(hbox(reset, none, 12, self.count, None))

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = btns.button(QDialogButtonBox.Ok)
        self.ok.setProperty("kind", "danger")
        btns.accepted.connect(self._confirm)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._update()

    def _set_all(self, keep_best):
        self.tree.blockSignals(True)
        for _, children in self.groups:
            for i, item in enumerate(children):
                item.setCheckState(0, Qt.Checked if keep_best and i else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update()

    def _update(self, *_):
        paths = self.chosen()
        size = sum(Path(p).stat().st_size for p in paths if Path(p).exists())
        self.count.setText(f"{len(paths)} file(s) ticked, {human_size(size)}")
        self.ok.setText(f"Move {len(paths)} to trash")
        self.ok.setEnabled(bool(paths))
        for top, children in self.groups:
            every = all(c.checkState(0) == Qt.Checked for c in children)
            top.setForeground(0, QColor(theme.ERR if every else theme.TEXT))
            top.setToolTip(0, "Every copy of this song is ticked — none would be left!" if every else "")

    def chosen(self):
        return [Path(c.data(0, Qt.UserRole)) for _, children in self.groups for c in children
                if c.checkState(0) == Qt.Checked]

    def _confirm(self):
        lost = [top.text(0).split("  ·  ")[0] for top, children in self.groups
                if all(c.checkState(0) == Qt.Checked for c in children)]
        if lost:
            names = "\n".join(f"• {n}" for n in lost[:8]) + ("\n…" if len(lost) > 8 else "")
            if QMessageBox.warning(self, "No copy left", f"Every copy of these songs is ticked, so they'd be gone "
                                   f"from your library:\n\n{names}\n\nTrash them anyway?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        self.accept()


class HealthPage(Page):
    title = "Health check"
    subtitle = "Find broken files, duplicates, missing tags or art, and “lossless” files that came from MP3s."
    icon = "health"
    scroll = False

    def __init__(self, win):
        super().__init__(win)
        card = Card()
        self.decode = Toggle("Decode every file to find corruption (slow)")
        self.spectrum = Toggle("Detect FLACs made from lossy sources (slow)")
        self.scan_btn = self.lock(button("Run health check", "primary", "health"))
        self.scan_btn.clicked.connect(self.scan)
        card.add(hbox(self.decode, 24, self.spectrum, None, self.scan_btn))
        card.add(label("The quick check (tags, cover art, duplicates) takes a second. The slow checks use ffmpeg "
                       "and every CPU core; on a ~700 track library expect a few minutes each.", "hint", wrap=True))
        self.add(card)

        self.filter = QComboBox()
        self.filter.setMinimumWidth(220)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.summary = label("Run a check to see results.", "muted")
        open_tags = button("Open in tag editor", icon="tag")
        open_tags.clicked.connect(self.open_in_tags)
        show = button("Show in folder", icon="folder")
        show.clicked.connect(self.show_in_folder)
        self.dupes_btn = button("Remove duplicates…", "danger", "trash")
        self.dupes_btn.clicked.connect(self.remove_duplicates)
        self.dupes_btn.setEnabled(False)
        self.add(hbox(self.filter, self.summary, None, open_tags, show, self.dupes_btn))

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Issue", "File", "Details"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 380)
        self.table.doubleClicked.connect(lambda _: self.open_in_tags())
        self.add(self.table, 1)

        self.log = LogPanel(self.jobs, 70)
        self.log.view.setMaximumHeight(110)
        self.add(self.log)
        self.issues = []
        self.tracks = []

    def scan(self):
        decode, spectrum = self.decode.isChecked(), self.spectrum.isChecked()
        lib = SETTINGS.library

        def job(ctx):
            from ...core import library
            tracks = self.store.tracks if self.store.loaded_once else library.scan(lib, ctx)
            return tracks, health.check(ctx, tracks, decode, spectrum, SETTINGS["jobs"])

        self.jobs.start("Health check", job, log=self.log, on_done=self._done)

    def _done(self, result):
        if result:
            self.tracks, issues = result
            self._show(issues)

    def _show(self, issues):
        self.issues = issues
        self.dupes_btn.setEnabled(any(i.kind == "Duplicate" for i in self.issues))
        counts = Counter(i.kind for i in self.issues)
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem(f"All issues ({len(self.issues)})", None)
        for kind in health.KINDS:
            if counts[kind]:
                self.filter.addItem(f"{kind} ({counts[kind]})", kind)
        self.filter.blockSignals(False)
        self._apply_filter()
        files = len({i.path for i in self.issues})
        self.summary.setText("No problems found — nice library!" if not self.issues
                             else f"{len(self.issues)} issue(s) in {files} file(s)")

    def _apply_filter(self):
        kind = self.filter.currentData()
        rows = [i for i in self.issues if kind is None or i.kind == kind]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, issue in enumerate(rows):
            k = QTableWidgetItem(f"●  {issue.kind}")
            k.setForeground(QColor(KIND_COLORS.get(issue.kind, theme.TEXT)))
            k.setData(Qt.UserRole, str(issue.path))
            f = QTableWidgetItem(issue.path.name)
            f.setToolTip(str(issue.path))
            d = QTableWidgetItem(issue.detail)
            d.setForeground(QColor(theme.MUTED))
            d.setToolTip(issue.detail)
            for c, item in enumerate((k, f, d)):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

    def _current_path(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        from pathlib import Path
        return Path(self.table.item(row, 0).data(Qt.UserRole))

    def open_in_tags(self):
        path = self._current_path()
        if path:
            from .tags import TagsPage
            self.win.go(TagsPage).select_path(path)

    def show_in_folder(self):
        path = self._current_path()
        if path:
            reveal(path)

    def remove_duplicates(self):
        if self.jobs.busy:
            QMessageBox.information(self, "Busy", f"Wait for “{self.jobs.name}” to finish first.")
            return
        sets = health.duplicate_sets(self.tracks)
        if not sets:
            QMessageBox.information(self, "No duplicates", "No exact duplicates left to remove.")
            return
        dlg = DuplicatesDialog(self, sets)
        if dlg.exec() != QDialog.Accepted:
            return
        trashed, failed = set(), []
        for path in dlg.chosen():
            ok, _ = QFile.moveToTrash(str(path))
            if ok:
                trashed.add(path)
            else:
                failed.append(path)
        for path in trashed:
            self.log.append(f"Moved to trash: {path}")
        for path in failed:
            self.log.append(f"Couldn't move to trash: {path}", "error")
        if not trashed:
            return
        # Drop the trashed files and recount duplicates among what's left
        self.tracks = [t for t in self.tracks if t.path not in trashed]
        if self.store.loaded_once:
            self.store.tracks = [t for t in self.store.tracks if t.path not in trashed]
            self.store.changed.emit()
        dup_kinds = ("Duplicate", "Possible duplicate")
        self._show([i for i in self.issues if i.path not in trashed and i.kind not in dup_kinds]
                   + health.duplicate_issues(self.tracks))
        msg = f"Moved {len(trashed)} duplicate(s) to the trash."
        if failed:
            msg += f" {len(failed)} couldn't be moved — see the log."
        self.log.append(msg, "warn" if failed else "info")
