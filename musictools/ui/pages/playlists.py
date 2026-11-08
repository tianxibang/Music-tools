from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QInputDialog, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout)

from ...core import playlists
from ...core.util import SETTINGS, human_duration
from .. import icons, theme
from ..main_window import Page
from ..widgets import (Card, LogPanel, TrackModel, button, hbox, label, search_box, selected_tracks, track_table)


class AddTracksDialog(QDialog):
    def __init__(self, parent, tracks):
        super().__init__(parent)
        self.setWindowTitle("Add tracks")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        self.search = search_box()
        lay.addWidget(self.search)
        self.model = TrackModel([c for c in TrackModel().columns if c[0] in ("title", "artist", "album", "duration")])
        self.model.set_tracks(tracks)
        self.view, self.proxy = track_table(self.model)
        self.search.textChanged.connect(self.proxy.set_needle)
        self.view.doubleClicked.connect(self.accept)
        lay.addWidget(self.view, 1)
        lay.addWidget(label("Tip: Ctrl/Shift-click to pick several, double-click to add one.", "hint"))
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Add")
        btns.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.search.setFocus()

    def chosen(self):
        return selected_tracks(self.view, self.proxy, self.model)


class PlaylistsPage(Page):
    title = "Playlists"
    subtitle = "Build playlists from the library. They're saved as .m3u8 next to your FLACs and exported " \
               "with Opus paths so they sync to the phone."
    icon = "playlist"
    scroll = False

    def __init__(self, win):
        super().__init__(win)
        left = Card("Your playlists", icon="playlist")
        left.setFixedWidth(300)
        self.pl_list = QListWidget()
        self.pl_list.currentItemChanged.connect(self._open)
        left.add(self.pl_list)
        new = button("New", icon="plus")
        new.clicked.connect(self.new)
        ren = button("Rename", icon="edit")
        ren.clicked.connect(self.rename)
        delete = button("", "danger", "trash", "Delete playlist")
        delete.clicked.connect(self.delete)
        left.add(hbox(new, ren, None, delete))

        right = Card()
        self.pl_title = label("Pick or create a playlist", "cardTitle")
        self.pl_info = label("", "muted")
        right.lay.addLayout(hbox(self.pl_title, None, self.pl_info))
        self.items = QListWidget()
        self.items.setDragDropMode(QAbstractItemView.InternalMove)
        self.items.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.items.setAlternatingRowColors(True)
        self.items.model().rowsMoved.connect(lambda *_: self._save())
        right.add(self.items)
        add = button("Add tracks…", "primary", "plus")
        add.clicked.connect(self.add_tracks)
        remove = button("Remove selected", icon="trash")
        remove.clicked.connect(self.remove)
        up = button("", "ghost", "up", "Move up")
        up.clicked.connect(lambda: self.move(-1))
        down = button("", "ghost", "down", "Move down")
        down.clicked.connect(lambda: self.move(1))
        export = self.lock(button("Export to Opus folder", icon="phone",
                                  tip="Writes Opus versions of all playlists; also done by the pipeline"))
        export.clicked.connect(self.export)
        right.add(hbox(add, remove, up, down, None, export))
        self.add(hbox(left, right, spacing=14), 1)

        self.log = LogPanel(self.jobs, 60)
        self.log.view.setMaximumHeight(90)
        self.add(self.log)
        self.current = None
        self.entries = []
        self.store.changed.connect(self._render_items)

    def on_show(self):
        self._load_list()

    def _by_path(self):
        return {t.path.resolve(): t for t in self.store.tracks}

    def _load_list(self, select=None):
        select = select or self.current
        self.pl_list.blockSignals(True)
        self.pl_list.clear()
        for p in playlists.list_playlists(SETTINGS.library):
            item = QListWidgetItem(icons.icon("playlist", theme.MUTED), p.stem)
            item.setData(Qt.UserRole, str(p))
            self.pl_list.addItem(item)
            if select and p == select:
                self.pl_list.setCurrentItem(item)
        self.pl_list.blockSignals(False)
        if self.pl_list.currentItem() is None and self.pl_list.count():
            self.pl_list.setCurrentRow(0)
        elif not self.pl_list.count():
            self.current, self.entries = None, []
            self._render_items()
        else:
            self._open(self.pl_list.currentItem())

    def _open(self, item, _prev=None):
        from pathlib import Path
        if not item:
            return
        self.current = Path(item.data(Qt.UserRole))
        self.entries = playlists.load(self.current)
        self._render_items()

    def _render_items(self):
        by_path = self._by_path()
        self.items.clear()
        total = 0
        for e in self.entries:
            t = by_path.get(e)
            if t:
                total += t.duration
                text = f"{t.display}     {human_duration(t.duration)}"
            else:
                text = f"{e.name}   (missing)"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, str(e))
            if not t:
                item.setForeground(QColor(theme.ERR))
            self.items.addItem(item)
        if self.current:
            self.pl_title.setText(self.current.stem)
            self.pl_info.setText(f"{len(self.entries)} tracks · {human_duration(total, long=True)}")
        else:
            self.pl_title.setText("Pick or create a playlist")
            self.pl_info.setText("")

    def _save(self):
        from pathlib import Path
        if not self.current:
            return
        self.entries = [Path(self.items.item(i).data(Qt.UserRole)) for i in range(self.items.count())]
        by_path = self._by_path()
        info = {e: (by_path[e].duration, by_path[e].display.replace("–", "-")) for e in self.entries if e in by_path}
        playlists.save(self.current, self.entries, info)
        self._render_items()

    def new(self):
        name, ok = QInputDialog.getText(self, "New playlist", "Name:")
        if ok and name.strip():
            path = playlists.new_path(SETTINGS.library, name.strip())
            if path.exists():
                QMessageBox.warning(self, "Exists", "A playlist with that name already exists.")
                return
            playlists.save(path, [])
            self.current = path
            self._load_list(path)

    def rename(self):
        if not self.current:
            return
        name, ok = QInputDialog.getText(self, "Rename playlist", "Name:", text=self.current.stem)
        if ok and name.strip():
            target = playlists.new_path(SETTINGS.library, name.strip())
            if target.exists():
                QMessageBox.warning(self, "Exists", "A playlist with that name already exists.")
                return
            entries = playlists.load(self.current)
            self.current.unlink()
            playlists.save(target, entries)
            self.current = target
            self._load_list(target)
            self._save()

    def delete(self):
        if self.current and QMessageBox.question(self, "Delete playlist", f"Delete “{self.current.stem}”? "
                                                 "The songs themselves are not touched.") == QMessageBox.Yes:
            self.current.unlink(missing_ok=True)
            self.current = None
            self._load_list()

    def add_tracks(self):
        if not self.current:
            self.new()
            if not self.current:
                return
        dlg = AddTracksDialog(self, self.store.tracks)
        if dlg.exec() == QDialog.Accepted:
            for t in dlg.chosen():
                self.entries.append(t.path.resolve())
            self._render_items()
            self._save()

    def remove(self):
        rows = sorted({self.items.row(i) for i in self.items.selectedItems()}, reverse=True)
        for r in rows:
            self.items.takeItem(r)
        if rows:
            self._save()

    def move(self, delta):
        row = self.items.currentRow()
        new = row + delta
        if row < 0 or not (0 <= new < self.items.count()):
            return
        item = self.items.takeItem(row)
        self.items.insertItem(new, item)
        self.items.setCurrentRow(new)
        self._save()
        self.items.setCurrentRow(new)

    def export(self):
        self.jobs.start("Exporting playlists", playlists.export, SETTINGS.library, SETTINGS.opus, log=self.log)
