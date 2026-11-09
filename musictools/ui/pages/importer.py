from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QMessageBox, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from ...core import importer
from ...core.util import SETTINGS, find_tool
from .. import theme
from ..main_window import Page
from ..widgets import Card, LogPanel, PathPicker, Toggle, button, hbox, label, trash_file

COLORS = {"new": theme.OK, "upgrade": theme.ACCENT_HI, "lossy": theme.WARN, "version": theme.TEXT,
          "broken": theme.ERR}


def _summary(counts):
    labels = {"new": "new", "upgrade": "upgrade", "lossy": "lossy", "version": "other version"}
    parts = []
    for action, word in labels.items():
        if n := counts.get(action):
            parts.append(f"{n} {word}{'s' if n > 1 and action != 'lossy' else ''}")
    return ", ".join(parts)


class ReviewDialog(QDialog):
    """Every file from the drive, grouped by what would happen to it; tick what to import."""

    def __init__(self, parent, candidates):
        super().__init__(parent)
        self.setWindowTitle("Review import")
        self.resize(1160, 700)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(label("Ticked files are copied into your library; the drive itself isn't changed. Upgrades "
                            "keep your library's tags and cover, and the file they replace goes to the trash. "
                            "Lossy songs go into the “Lossy” folder of your library.", "hint", wrap=True))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File on the drive", "Details", "Note", "Folder"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        for col, width in enumerate((360, 330, 220)):
            self.tree.setColumnWidth(col, width)
        self.items = []  # (tree item, candidate)
        by_action = {}
        for c in candidates:
            by_action.setdefault(c.action, []).append(c)
        for action, title in importer.ACTIONS:
            group = by_action.get(action)
            if not group:
                continue
            importable = action in importer.IMPORTABLE
            section = QTreeWidgetItem([f"{title}  ({len(group)})"])
            section.setFirstColumnSpanned(True)
            section.setForeground(0, QColor(COLORS.get(action, theme.MUTED)))
            font = section.font(0)
            font.setBold(True)
            section.setFont(0, font)
            self.tree.addTopLevelItem(section)
            for c in group:
                item = QTreeWidgetItem(section, [c.src.path.name, c.reason, "; ".join(c.warnings),
                                                 str(c.src.path.parent)])
                item.setToolTip(0, str(c.src.path))
                item.setToolTip(1, c.reason)
                if c.warnings:
                    item.setForeground(2, QColor(theme.WARN))
                    item.setToolTip(2, "\n".join(c.warnings))
                if importable:
                    item.setCheckState(0, Qt.Checked if c.tick else Qt.Unchecked)
                    self.items.append((item, c))
                else:
                    item.setForeground(0, QColor(theme.MUTED))
            # What needs a decision is open; what's skipped is folded away
            section.setExpanded(importable)
        self.tree.itemChanged.connect(self._update)
        lay.addWidget(self.tree, 1)

        suggested = button("Suggested", icon="check", tip="New songs, upgrades and lossy songs; not other versions")
        suggested.clicked.connect(lambda: self._set_all("suggested"))
        everything = button("Tick everything", tip="Also import the other versions")
        everything.clicked.connect(lambda: self._set_all("all"))
        none = button("Untick all")
        none.clicked.connect(lambda: self._set_all("none"))
        self.count = label("", "muted")
        lay.addLayout(hbox(suggested, everything, none, 12, self.count, None))
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = btns.button(QDialogButtonBox.Ok)
        self.ok.setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._suggested = {id(c): c.tick for _, c in self.items}
        self._update()

    def _set_all(self, mode):
        self.tree.blockSignals(True)
        for item, c in self.items:
            on = mode == "all" or (mode == "suggested" and self._suggested[id(c)])
            item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update()

    def _update(self, *_):
        counts = Counter(c.action for item, c in self.items if item.checkState(0) == Qt.Checked)
        n = sum(counts.values())
        self.count.setText(_summary(counts) or "Nothing ticked")
        self.ok.setText(f"Import {n} file{'s' if n != 1 else ''}")
        self.ok.setEnabled(n > 0)

    def apply_ticks(self):
        for item, c in self.items:
            c.tick = item.checkState(0) == Qt.Checked


class ImportPage(Page):
    title = "Import"
    subtitle = "Merge music from another drive or folder: new songs are added, better copies replace yours, " \
               "and what you already have is skipped."
    icon = "down"
    follower_note = "Import on the main PC, or drop the files into this computer's “New music” folder and send them from the Sync page."

    def __init__(self, win):
        super().__init__(win)
        card = Card("Import from a drive or folder", icon="folder")
        self.source = PathPicker(SETTINGS["import_source"], "Choose the drive or folder to import from…")
        self.check_fakes = Toggle("Check that lossless files really are lossless (slower)",
                                  SETTINGS["import_check_fakes"] and bool(find_tool("ffmpeg")))
        self.scan_btn = self.lock(button("Scan", "primary", "search"))
        self.scan_btn.clicked.connect(self.scan)
        card.add(hbox(self.source, self.scan_btn))
        card.add(self.check_fakes)
        card.add(label("Nothing on the drive is changed or deleted: files are copied, and every copy is verified. "
                       "Upgrades keep your library's tags and cover.", "hint", wrap=True))
        self.add(card)

        result = Card("What would happen", icon="eye")
        self.summary = label("Scan a drive or folder to see what's on it.", "muted", wrap=True)
        result.add(self.summary)
        self.review_btn = self.lock(button("Review…", icon="eye"))
        self.review_btn.clicked.connect(self.review)
        self.import_btn = self.lock(button("Import suggested", "primary", "down"))
        self.import_btn.clicked.connect(self.import_suggested)
        result.add(hbox(self.review_btn, self.import_btn, None))
        self.add(result)

        self.log = LogPanel(self.jobs, 200)
        self.add(self.log, 1)
        self.candidates = []
        self._update_buttons()

    def _busy(self, busy):
        super()._busy(busy)
        if not busy:
            self._update_buttons()

    def _update_buttons(self):
        ready = bool(self.candidates)
        self.review_btn.setEnabled(ready and not self.jobs.busy)
        self.import_btn.setEnabled(ready and not self.jobs.busy and any(c.tick for c in self.candidates))

    def scan(self):
        folder = self.source.text()
        src = Path(folder)
        if not folder or not src.is_dir():
            QMessageBox.warning(self, "Import", "Choose a drive or folder first.")
            return
        lib = SETTINGS.library
        if src.resolve() == lib.resolve() or src.resolve().is_relative_to(lib.resolve()) \
                or lib.resolve().is_relative_to(src.resolve()):
            QMessageBox.warning(self, "Import", "That's your library (or contains it). Choose another folder.")
            return
        SETTINGS["import_source"] = folder
        SETTINGS["import_check_fakes"] = self.check_fakes.isChecked()
        SETTINGS.save()
        check = self.check_fakes.isChecked()
        self.candidates = []

        def job(ctx):
            from ...core import library
            source = importer.scan(ctx, src)
            lib_tracks = self.store.tracks if self.store.loaded_once else library.scan(lib, ctx)
            return importer.plan(ctx, source, lib_tracks, check_fakes=check)

        self.jobs.start("Scanning", job, log=self.log, on_done=self._planned)

    def _planned(self, candidates):
        self.candidates = candidates or []
        counts = Counter(c.action for c in self.candidates)
        if not self.candidates:
            self.summary.setText("No music found there.")
        else:
            lines = [f"<b>{n}</b> {title.lower()}" for action, title in importer.ACTIONS if (n := counts[action])]
            suggested = Counter(c.action for c in self.candidates if c.tick)
            self.summary.setText("<br>".join(lines) + ("<br><br>Suggested: " + _summary(suggested) if suggested
                                                        else "<br><br>Nothing new to import."))
        self._update_buttons()

    def review(self):
        dlg = ReviewDialog(self, self.candidates)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_ticks()
            self._import()
        else:
            self._update_buttons()

    def import_suggested(self):
        counts = Counter(c.action for c in self.candidates if c.tick)
        upgrades = counts.get("upgrade", 0)
        msg = f"Import {_summary(counts)}?"
        if upgrades:
            msg += f"\n\n{upgrades} file(s) in your library will be replaced by a better copy (the old ones go to " \
                   f"the trash; your tags are kept)."
        if QMessageBox.question(self, "Import", msg) == QMessageBox.Yes:
            self._import()

    def _import(self):
        chosen = list(self.candidates)

        def done(_):
            self.candidates = []
            self.summary.setText("Done. Scan again to check for more.")
            self.store.reload()
            self._update_buttons()

        self.jobs.start("Importing", importer.apply, chosen, SETTINGS, trash_file, log=self.log, on_done=done)
