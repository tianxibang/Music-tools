from pathlib import Path

from PySide6.QtWidgets import QComboBox, QGridLayout, QMessageBox, QRadioButton, QTabWidget, QWidget

from ...core import library, organize
from ...core.util import SETTINGS, human_duration, human_size
from ..main_window import Page
from ..widgets import (BarList, Card, FormGrid, LogPanel, PathPicker, StatCard, Toggle, button, hbox, label, vbox)
from .tags import preview_table

SORT_PRESETS = ["{albumartist}/{album}/{track:02} - {title}", "{albumartist}/{year} - {album}/{track:02} - {title}",
                "{artist}/{title}", "{genre}/{artist} - {title}", "{year}/{artist} - {title}"]


class OrganizePage(Page):
    title = "Organize"
    subtitle = "Inspect any music folder, sort files into folders by their tags, or flatten a folder."
    icon = "folder"
    follower_note = "Files are moved and renamed on the main PC; this computer follows with the next sync."

    def __init__(self, win):
        super().__init__(win)
        tabs = QTabWidget()
        tabs.addTab(self._info_tab(), "Folder info")
        tabs.addTab(self._sorter_tab(), "Sorter")
        tabs.addTab(self._flatten_tab(), "Flatten")
        self.add(tabs)
        self.log = LogPanel(self.jobs, 160)
        self.add(self.log, 1)

    # --- info

    def _info_tab(self):
        w = QWidget()
        self.info_path = PathPicker(SETTINGS["library"])
        go = self.lock(button("Analyze", "primary", "search"))
        go.clicked.connect(self.analyze)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.info_stats = {
            "tracks": StatCard("music", "Tracks"), "size": StatCard("disk", "Total size"),
            "duration": StatCard("clock", "Play time"), "artists": StatCard("tag", "Artists"),
            "albums": StatCard("image", "Albums"), "avg_bitrate": StatCard("volume", "Average bitrate"),
        }
        for i, c in enumerate(self.info_stats.values()):
            grid.addWidget(c, i // 3, i % 3)
        self.bars = {}
        bar_grid = QGridLayout()
        bar_grid.setSpacing(12)
        for i, (key, title) in enumerate([("top_artists", "Top artists"), ("genres", "Genres"),
                                          ("decades", "Decades"), ("quality", "Quality"), ("formats", "Formats")]):
            card = Card(title)
            self.bars[key] = card.add(BarList())
            card.lay.addStretch(1)
            bar_grid.addWidget(card, i // 3, i % 3)
        self.info_extra = label("", "muted")
        w.setLayout(vbox(hbox(self.info_path, go), grid, bar_grid, self.info_extra, spacing=14,
                         margins=(0, 14, 0, 0)))
        return w

    def analyze(self):
        folder = Path(self.info_path.text())
        if not folder.is_dir():
            QMessageBox.warning(self, "Folder", "Pick an existing folder.")
            return
        self.jobs.start("Analyzing folder", library.folder_stats, folder, log=self.log, on_done=self._show_info)

    def _show_info(self, st):
        if not st:
            return
        s = self.info_stats
        s["tracks"].set(f"{st['tracks']:,}")
        s["size"].set(human_size(st["size"]))
        s["duration"].set(human_duration(st["duration"], long=True))
        s["artists"].set(f"{st['artists']:,}")
        s["albums"].set(f"{st['albums']:,}")
        s["avg_bitrate"].set(f"{st['avg_bitrate']:,} kbps")
        for key, bars in self.bars.items():
            bars.set(st[key])
        self.info_extra.setText(f"{st['no_art']} track(s) without cover art · {st['other_files']} non-audio file(s) "
                                f"({human_size(st['other_size'])})")
        self.log.append(f"Analyzed {st['tracks']} tracks.", "ok")

    # --- sorter

    def _sorter_tab(self):
        w = QWidget()
        form = FormGrid()
        self.sort_src = form.add("Folder to sort", PathPicker(SETTINGS["library"]))
        self.sort_tpl = QComboBox()
        self.sort_tpl.setEditable(True)
        self.sort_tpl.addItems(SORT_PRESETS)
        self.sort_tpl.setCurrentText(SETTINGS["sort_template"])
        form.add("Pattern", self.sort_tpl, "Use “/” for folders. Fields: {artist} {albumartist} {album} {title} "
                                           "{track:02} {disc} {year} {genre}")
        self.mode_move = QRadioButton("Move")
        self.mode_copy = QRadioButton("Copy to another folder")
        self.mode_move.setChecked(True)
        form.add("Mode", hbox(self.mode_move, self.mode_copy, None, spacing=18))
        self.sort_dest = form.add("Copy to", PathPicker(placeholder="Destination folder (for Copy)"))
        self.sort_mirror = Toggle("Move the Opus copies and update playlists too", True)
        form.add("", self.sort_mirror, "Only applies when sorting the library folder itself, so nothing needs "
                                       "re-converting and the phone sync just moves files.")
        self.mode_copy.toggled.connect(self._sorter_mode)
        self._sorter_mode()
        preview = button("Preview", icon="eye")
        preview.clicked.connect(self.sort_preview)
        self.sort_apply = button("Apply", "primary", "check")
        self.sort_apply.clicked.connect(self.sort_run)
        self.sort_apply.setEnabled(False)
        self.lock(preview)
        self.sort_table = preview_table(["Current", "New location"])
        self.sort_table.setMinimumHeight(220)
        self.sort_summary = label("", "muted")
        self.moves = []
        w.setLayout(vbox(form, hbox(preview, self.sort_apply, self.sort_summary, None), self.sort_table,
                         spacing=12, margins=(0, 14, 0, 0)))
        return w

    def _sorter_mode(self):
        copy = self.mode_copy.isChecked()
        self.sort_dest.setEnabled(copy)
        self.sort_mirror.setEnabled(not copy)

    def sort_preview(self):
        src = Path(self.sort_src.text())
        tpl = self.sort_tpl.currentText()
        copy = self.mode_copy.isChecked()
        dest = Path(self.sort_dest.text()) if copy else src
        if not src.is_dir() or (copy and not self.sort_dest.text()):
            QMessageBox.warning(self, "Sorter", "Pick the folder to sort" + (" and a destination." if copy else "."))
            return
        SETTINGS["sort_template"] = tpl
        SETTINGS.save()

        def job(ctx):
            tracks = library.scan(src, ctx)
            return organize.plan_moves(tracks, dest, tpl)

        self.jobs.start("Planning sort", job, log=self.log, on_done=lambda m: self._show_moves(m, src, dest))

    def _show_moves(self, moves, src, dest):
        self.moves = moves or []
        from PySide6.QtWidgets import QTableWidgetItem
        self.sort_table.setRowCount(min(len(self.moves), 2000))
        for r, (a, b) in enumerate(self.moves[:2000]):
            self.sort_table.setItem(r, 0, QTableWidgetItem(a.relative_to(src).as_posix()))
            self.sort_table.setItem(r, 1, QTableWidgetItem(b.relative_to(dest).as_posix()))
        self.sort_summary.setText(f"{len(self.moves)} file(s) would be {'copied' if self.mode_copy.isChecked() else 'moved'}."
                                  if self.moves else "Everything is already where the pattern puts it.")
        self.sort_apply.setEnabled(bool(self.moves))
        self.log.append(self.sort_summary.text())

    def sort_run(self):
        if not self.moves:
            return
        copy = self.mode_copy.isChecked()
        src = Path(self.sort_src.text())
        is_library = src.resolve() == SETTINGS.library.resolve()
        mirror = not copy and is_library and self.sort_mirror.isChecked()
        if QMessageBox.question(self, "Sorter", f"{'Copy' if copy else 'Move'} {len(self.moves)} file(s)?") \
                != QMessageBox.Yes:
            return
        moves = self.moves
        self.moves = []
        self.sort_apply.setEnabled(False)
        args = (SETTINGS.library, SETTINGS.opus) if mirror else (None, None)
        self.jobs.start("Sorting", organize.apply_moves, moves, copy, *args, log=self.log,
                        on_done=lambda _: self.store.reload() if is_library else None)

    # --- flatten

    def _flatten_tab(self):
        w = QWidget()
        form = FormGrid()
        self.flat_path = form.add("Folder", PathPicker(SETTINGS["flatten"]))
        self.flat_dry = Toggle("Dry run — only show what would happen")
        form.add("", self.flat_dry)
        go = self.lock(button("Flatten", "primary", "folder"))
        go.clicked.connect(self.flatten)
        w.setLayout(vbox(label("Moves every file in the folder's subfolders up into the folder itself, then removes "
                               "the empty subfolders. Files whose name already exists are skipped, never "
                               "overwritten.", "muted", wrap=True), form, hbox(go, None), None,
                         spacing=12, margins=(0, 14, 0, 0)))
        return w

    def flatten(self):
        target = Path(self.flat_path.text())
        if not self.flat_path.text() or not target.is_dir():
            QMessageBox.warning(self, "Flatten", "Pick an existing folder first.")
            return
        dry = self.flat_dry.isChecked()
        n = len(organize.plan_flatten(target))
        if not n:
            self.log.append("No files in subfolders — nothing to do.", "ok")
            return
        if target.resolve() in (Path.home().resolve(), SETTINGS.library.parent.resolve()) and not dry:
            if QMessageBox.warning(self, "Flatten", f"{target} looks like a top-level folder. Really move "
                                   f"{n} files out of all its subfolders?", QMessageBox.Yes | QMessageBox.No) \
                    != QMessageBox.Yes:
                return
        elif not dry and QMessageBox.question(self, "Flatten", f"Move {n} file(s) from the subfolders of\n{target}\n"
                                              "into that folder?") != QMessageBox.Yes:
            return
        SETTINGS["flatten"] = str(target)
        SETTINGS.save()
        self.jobs.start("Flattening", organize.flatten, target, dry, log=self.log)
