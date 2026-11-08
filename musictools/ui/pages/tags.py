from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QScrollArea,
                               QHeaderView, QLineEdit, QRadioButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QMessageBox)

from ...core import organize, tags
from ...core.util import SETTINGS
from .. import theme
from ..main_window import Page
from ..widgets import (CoverLabel, FormGrid, LogPanel, TrackModel, button, hbox, label, search_box,
                       selected_tracks, track_table, vbox)

RENAME_PRESETS = ["{artist} - {title}", "{title} - {artist}", "{track:02} - {title}", "{track:02} {artist} - {title}",
                  "{albumartist}/{album}/{track:02} - {title}"]
PARSE_PRESETS = ["{artist} - {title}", "{title} - {artist}", "{track} - {title}", "{track}. {artist} - {title}",
                 "{track} {title}"]
MULTI = "(multiple values)"


def preview_table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.verticalHeader().setDefaultSectionSize(28)
    return t


class TemplateDialog(QDialog):
    """Shared shell for the rename and parse dialogs: template box, scope, live preview."""

    def __init__(self, parent, title, intro, presets, current, headers, n_selected):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label(title, "cardTitle"))
        lay.addWidget(label(intro, "muted", wrap=True))
        self.template = QComboBox()
        self.template.setEditable(True)
        self.template.addItems(presets)
        self.template.setCurrentText(current)
        self.only_sel = QRadioButton(f"Selected tracks ({n_selected})")
        self.all = QRadioButton("All tracks in the library")
        (self.only_sel if n_selected > 1 else self.all).setChecked(True)
        self.only_sel.setEnabled(n_selected > 0)
        form = FormGrid()
        form.add("Pattern", self.template, tags.TEMPLATE_HELP)
        form.add("Apply to", hbox(self.only_sel, self.all, None, spacing=18))
        lay.addWidget(form)
        self.summary = label("", "muted")
        lay.addWidget(self.summary)
        self.table = preview_table(headers)
        lay.addWidget(self.table, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = btns.button(QDialogButtonBox.Ok)
        self.ok.setText("Apply")
        self.ok.setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self._timer = QTimer(self, singleShot=True, interval=200, timeout=self.update_preview)
        self.template.currentTextChanged.connect(lambda _: self._timer.start())
        self.only_sel.toggled.connect(lambda _: self._timer.start())

    def fill(self, rows, summary):
        self.table.setRowCount(0)
        self.table.setRowCount(min(len(rows), 1000))
        for r, row in enumerate(rows[:1000]):
            for c, val in enumerate(row):
                item = QTableWidgetItem(str(val))
                if c == 0:
                    item.setForeground(Qt.gray)
                self.table.setItem(r, c, item)
        self.summary.setText(summary)


class RenameDialog(TemplateDialog):
    def __init__(self, parent, selected, all_tracks):
        super().__init__(parent, "Rename files from tags",
                         "Builds each file name from its tags. Files stay in their folder unless the pattern "
                         "contains “/”. Opus copies and playlists are renamed to match.",
                         RENAME_PRESETS, SETTINGS["rename_template"], ["Current name", "New name"], len(selected))
        self.selected, self.all_tracks = selected, all_tracks
        self.moves = []
        self.update_preview()

    def tracks(self):
        return self.selected if self.only_sel.isChecked() else self.all_tracks

    def update_preview(self):
        tpl = self.template.currentText()
        try:
            root = SETTINGS.library if "/" in tpl else None
            self.moves = organize.plan_moves(self.tracks(), root, tpl)
        except (ValueError, KeyError, IndexError) as e:
            self.moves = []
            self.fill([], f"Pattern error: {e}")
            self.ok.setEnabled(False)
            return
        rows = []
        for src, dst in self.moves:
            try:
                new = dst.relative_to(SETTINGS.library).as_posix()
            except ValueError:
                new = dst.name
            rows.append((src.name, new))
        self.fill(rows, f"{len(self.moves)} of {len(self.tracks())} file(s) would be renamed.")
        self.ok.setEnabled(bool(self.moves))


class ParseDialog(TemplateDialog):
    def __init__(self, parent, selected, all_tracks):
        super().__init__(parent, "Tags from file names",
                         "Reads tags out of each file name using the pattern. Files that don't match are skipped.",
                         PARSE_PRESETS, PARSE_PRESETS[0], ["File", "Artist", "Title", "Other"], len(selected))
        self.selected, self.all_tracks = selected, all_tracks
        self.changes = []
        self.update_preview()

    def update_preview(self):
        tracks = self.selected if self.only_sel.isChecked() else self.all_tracks
        pat = self.template.currentText()
        self.changes, rows = [], []
        try:
            for t in tracks:
                parsed = tags.parse_filename(t.path.stem, pat)
                if not parsed:
                    continue
                diff = {k: v for k, v in parsed.items() if t.tag(k) != v}
                if diff:
                    self.changes.append((t, diff))
                    other = ", ".join(f"{k}={v}" for k, v in diff.items() if k not in ("artist", "title"))
                    rows.append((t.path.name, diff.get("artist", "·"), diff.get("title", "·"), other))
        except Exception as e:
            self.fill([], f"Pattern error: {e}")
            self.ok.setEnabled(False)
            return
        self.fill(rows, f"{len(self.changes)} of {len(tracks)} file(s) would change (“·” = unchanged).")
        self.ok.setEnabled(bool(self.changes))


class TagsPage(Page):
    title = "Tag editor"
    subtitle = "Browse the library, fix tags in bulk and rename files. Changes are mirrored to the Opus copies."
    icon = "tag"
    scroll = False

    def __init__(self, win):
        super().__init__(win)
        self.search = search_box()
        self.count = label("", "muted")
        rename = button("Rename files…", icon="edit")
        rename.clicked.connect(self.rename)
        parse = button("Tags from file names…", icon="tag")
        parse.clicked.connect(self.parse)
        reload_btn = button("", "ghost", "refresh", "Re-read the library")
        reload_btn.clicked.connect(self.store.reload)
        self.lock(rename, parse)
        self.add(hbox(self.search, self.count, None, parse, rename, reload_btn))

        self.model = TrackModel()
        self.view, self.proxy = track_table(self.model)
        self.search.textChanged.connect(self._filter)
        self.view.selectionModel().selectionChanged.connect(self._selection)

        self.editor = QScrollArea()
        self.editor.setWidgetResizable(True)
        self.editor.setFrameShape(QFrame.NoFrame)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor.setWidget(self._editor())
        self.editor.setMinimumWidth(340)
        self.editor.setMaximumWidth(440)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.view)
        split.addWidget(self.editor)
        split.setStretchFactor(0, 1)
        split.setChildrenCollapsible(False)
        split.setSizes([900, 340])
        self.add(split, 1)

        self.log = LogPanel(self.jobs, 70)
        self.log.view.setMaximumHeight(110)
        self.add(self.log)

        self.store.changed.connect(self._reload)
        self.store.updated.connect(self.model.refresh_rows)
        self.store.loadingChanged.connect(lambda on: self.count.setText("Loading library…") if on else None)

    def _editor(self):
        panel = QFrame()
        panel.setObjectName("card")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        self.cover = CoverLabel(150)
        cover_row = hbox(None, self.cover, None)
        lay.addLayout(cover_row)
        self.sel_label = label("Select tracks to edit", "cardTitle")
        self.sel_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.sel_label)
        self.sel_sub = label("", "muted")
        self.sel_sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.sel_sub)
        form = FormGrid()
        self.fields = {}
        for key in tags.FIELDS:
            edit = QLineEdit()
            edit.textEdited.connect(lambda _t, e=edit: e.setProperty("dirty", True))
            form.add(tags.FIELD_LABELS[key], edit)
            self.fields[key] = edit
        lay.addWidget(form)
        self.new_cover = None
        self.cover_btn = button("Change cover…", icon="image")
        self.cover_btn.clicked.connect(self.pick_cover)
        self.save_btn = button("Save", "primary", "check")
        self.save_btn.clicked.connect(self.save)
        self.lock(self.save_btn)
        lay.addLayout(hbox(self.cover_btn, None, self.save_btn))
        lay.addStretch(1)
        self._set_editor_enabled(False)
        return panel

    def on_show(self):
        if not self.store.loaded_once:
            self.count.setText("Loading library…")

    def _reload(self):
        self.model.set_tracks(self.store.tracks)
        self._filter()

    def _filter(self):
        self.proxy.set_needle(self.search.text())
        shown, total = self.proxy.rowCount(), len(self.model.tracks)
        self.count.setText(f"{total:,} tracks" if shown == total else f"{shown:,} of {total:,}")

    def selected(self):
        return selected_tracks(self.view, self.proxy, self.model)

    def select_path(self, path):
        self.search.clear()
        for row, t in enumerate(self.model.tracks):
            if t.path == path:
                idx = self.proxy.mapFromSource(self.model.index(row, 0))
                self.view.selectRow(idx.row())
                self.view.scrollTo(idx, QAbstractItemView.PositionAtCenter)
                return

    def _set_editor_enabled(self, on):
        for e in self.fields.values():
            e.setEnabled(on)
        self.cover_btn.setEnabled(on)
        self.save_btn.setEnabled(on and not self.jobs.busy)

    def _selection(self):
        sel = self.selected()
        self.new_cover = None
        self._set_editor_enabled(bool(sel))
        for key, edit in self.fields.items():
            edit.setProperty("dirty", False)
            values = {t.tag(key) for t in sel}
            if len(values) == 1:
                edit.setText(values.pop())
                edit.setPlaceholderText("")
            else:
                edit.setText("")
                edit.setPlaceholderText(MULTI if values else "")
        if not sel:
            self.sel_label.setText("Select tracks to edit")
            self.sel_sub.setText("")
            self.cover.clear_cover()
            return
        if len(sel) == 1:
            t = sel[0]
            self.sel_label.setText(t.title)
            self.sel_sub.setText(f"{t.codec.upper()} · {t.bits or ''}{'-bit · ' if t.bits else ''}"
                                 f"{t.sample_rate / 1000:g} kHz · {t.bitrate} kbps")
            pic = tags.get_picture(t.path)
            self.cover.set_data(pic[0] if pic else None)
        else:
            self.sel_label.setText(f"{len(sel)} tracks selected")
            self.sel_sub.setText("Only fields you change are written.")
            pic = tags.get_picture(sel[0].path)
            self.cover.set_data(pic[0] if pic else None)

    def pick_cover(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", "", "Images (*.jpg *.jpeg *.png)")
        if path:
            with open(path, "rb") as f:
                self.new_cover = f.read()
            self.cover.set_data(self.new_cover)

    def save(self):
        sel = self.selected()
        changes = {k: e.text().strip() for k, e in self.fields.items() if e.property("dirty")}
        cover = self.new_cover
        if not sel or (not changes and not cover):
            return
        lib, opus = SETTINGS.library, SETTINGS.opus

        def job(ctx):
            for i, t in enumerate(sel, 1):
                ctx.check()
                try:
                    if changes:
                        organize.save_tags(t.path, changes, lib, opus)
                    if cover:
                        mime = tags.image_mime(cover)
                        tags.set_picture(t.path, cover, mime)
                        o = None
                        if t.codec == "flac":
                            from ...core.util import opus_path_for
                            o = opus_path_for(t.path, lib, opus)
                        if o and o.exists():
                            tags.set_picture(o, cover, mime)
                except Exception as e:
                    ctx.log(f"  {t.path.name}: {e}", "error")
                ctx.progress(i, len(sel), f"Saving {i} / {len(sel)}")
            what = ", ".join(tags.FIELD_LABELS[k].lower() for k in changes) + (" cover" if cover else "")
            ctx.log(f"Saved {what.strip(', ')} for {len(sel)} track(s).", "ok")
            return [t.path for t in sel]

        def done(paths):
            self.store.reread(paths or [])
            self._selection()

        self.jobs.start("Saving tags", job, log=self.log, on_done=done)

    def rename(self):
        dlg = RenameDialog(self, self.selected(), self.store.tracks)
        if dlg.exec() != QDialog.Accepted or not dlg.moves:
            return
        SETTINGS["rename_template"] = dlg.template.currentText()
        SETTINGS.save()
        self.jobs.start("Renaming", organize.apply_moves, dlg.moves, False, SETTINGS.library, SETTINGS.opus,
                        log=self.log, on_done=lambda _: self.store.reload())

    def parse(self):
        dlg = ParseDialog(self, self.selected(), self.store.tracks)
        if dlg.exec() != QDialog.Accepted or not dlg.changes:
            return
        changes = dlg.changes
        lib, opus = SETTINGS.library, SETTINGS.opus

        def job(ctx):
            for i, (t, diff) in enumerate(changes, 1):
                ctx.check()
                try:
                    organize.save_tags(t.path, diff, lib, opus)
                except Exception as e:
                    ctx.log(f"  {t.path.name}: {e}", "error")
                ctx.progress(i, len(changes))
            ctx.log(f"Updated tags of {len(changes)} file(s).", "ok")
            return [t.path for t, _ in changes]

        self.jobs.start("Tags from names", job, log=self.log, on_done=lambda p: self.store.reread(p or []))
