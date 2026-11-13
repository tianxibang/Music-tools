import time
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ...core.netsync import PORT, security
from ...core.tags import FIELD_LABELS
from ...core.util import SETTINGS
from .. import theme
from ..main_window import Page
from ..widgets import Card, FormGrid, LogPanel, PathPicker, Toggle, button, hbox, label
from .devices import _ago

GETS = [("flac", "The FLAC library (lossless)"), ("opus", "The Opus versions (small, like the phone)"),
        ("both", "Both")]



def _lyrics_change(old, new):
    from ...core.lyrics import KIND_LABELS, kind_of
    if not new.strip():
        return "Lyrics removed"
    kind = KIND_LABELS[kind_of(new)].lower()
    return f"Lyrics ({kind}) added" if not old.strip() else f"Lyrics edited ({kind})"

class PairMainDialog(QDialog):
    """Find the main PC on the network (or type its address) and pair with the code it shows."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Pair with the main PC")
        self.setMinimumWidth(520)
        self.bg = parent.bg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(10)
        lay.addWidget(label("On the main PC: <b>Sync → Pair a device…</b>. Then pick it here and type the code it "
                            "shows.", "muted", wrap=True))
        self.found = QListWidget()
        self.found.setFixedHeight(120)
        self.found.currentItemChanged.connect(self._picked)
        lay.addWidget(self.found)
        self.status = label("Looking on the network…", "hint")
        lay.addWidget(self.status)
        form = FormGrid()
        self.host = form.add("Address", QLineEdit())
        self.host.setPlaceholderText("192.168.1.20")
        self.port = form.add("Port", QSpinBox())
        self.port.setRange(1, 65535)
        self.port.setValue(PORT)
        self.code = form.add("Pairing code", QLineEdit())
        self.code.setPlaceholderText("XXXX-XXXX-XXXX")
        lay.addWidget(form)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = btns.button(QDialogButtonBox.Ok)
        self.ok.setText("Pair")
        self.ok.setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        self.short_fp = ""
        from ...core.netsync.discovery import browse
        self.bg.run(lambda: browse(3), self._found)

    def _found(self, pcs):
        if isinstance(pcs, Exception) or not pcs:
            self.status.setText("Nothing found. Type the address shown on the main PC's Sync page.")
            return
        mine = security.Identity().id
        pcs = [p for p in pcs if p["id"] != mine]
        for pc in pcs:
            item = QListWidgetItem(f"{pc['name']}   —   {pc['host']}:{pc['port']}")
            item.setData(Qt.UserRole, pc)
            self.found.addItem(item)
        self.status.setText(f"Found {len(pcs)} PC(s)." if pcs else "Only this computer is on the network.")
        if pcs:
            self.found.setCurrentRow(0)

    def _picked(self, item, _prev=None):
        if item:
            pc = item.data(Qt.UserRole)
            self.host.setText(pc["host"])
            self.port.setValue(pc["port"])
            self.short_fp = pc["fp"]
            self.code.setFocus()


class FollowerPanel(Page):
    """Shown on the Sync page when this computer follows another PC."""

    def __init__(self, win, embedded=True):
        super().__init__(win, embedded)
        self.follower = win.follower

        main = Card("Main PC", "This computer mirrors the main PC's library and can send it new music and tag "
                    "edits. Everything else is changed on the main PC.", icon="disk")
        self.main_status = label("", wrap=True)
        main.add(self.main_status)
        self.pair_btn = button("Pair with the main PC…", "primary", "link")
        self.pair_btn.clicked.connect(self.pair)
        self.unpair_btn = button("Unpair", "danger", "trash")
        self.unpair_btn.clicked.connect(self.unpair)
        main.add(hbox(self.pair_btn, self.unpair_btn, None))
        form = FormGrid()
        self.get = QComboBox()
        for key, text in GETS:
            self.get.addItem(text, key)
        self.get.setCurrentIndex([k for k, _ in GETS].index(SETTINGS["follow_get"]))
        self.get.currentIndexChanged.connect(self._save)
        self.where = label("", "hint", wrap=True)
        form.add("This computer gets", self.get)
        form.add("", self.where)
        main.add(form)
        self.auto = Toggle(f"Sync automatically every {SETTINGS['follow_interval']} minutes while Music Tools is open",
                           SETTINGS["follow_auto"])
        self.auto.toggled.connect(self._save)
        main.add(self.auto)
        self.sync_btn = self.lock(button("Sync now", "primary", "refresh"))
        self.sync_btn.clicked.connect(lambda: self.sync(manual=True))
        self.last = label("", "muted")
        main.add(hbox(self.sync_btn, 12, self.last, None))
        self.add(main)

        out = Card("Send to the main PC", "New music and tag edits from this computer. The main PC files them and "
                   "passes them on to all your devices.", icon="up")
        self.new_folder = PathPicker(SETTINGS["follow_new_music"])
        self.new_folder.edit.editingFinished.connect(self._save)
        nf = FormGrid()
        nf.add("New music folder", self.new_folder, "Drop music here to send it. It's removed from this folder once "
               "the main PC has it, and comes back into your library with the next sync.")
        out.add(nf)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Item", "Change", "Note"])
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(180)
        for col, width in enumerate((330, 330)):
            self.tree.setColumnWidth(col, width)
        out.add(self.tree)
        self.send_btn = self.lock(button("Send ticked", "primary", "up"))
        self.send_btn.clicked.connect(self.send)
        self.mine_btn = self.lock(button("Keep mine", tip="For the selected conflicts: overwrite the main PC's "
                                                          "value with yours"))
        self.mine_btn.clicked.connect(self.keep_mine)
        self.theirs_btn = button("Use the main PC's", tip="Drop the selected edits; the next sync brings back the "
                                                          "main PC's version")
        self.theirs_btn.clicked.connect(self.use_theirs)
        refresh = button("", "ghost", "refresh", "Refresh the list")
        refresh.clicked.connect(self.refresh)
        out.add(hbox(self.send_btn, self.mine_btn, self.theirs_btn, None, refresh))
        self.send_auto = Toggle("Send automatically when syncing (conflicts always wait for you)",
                                SETTINGS["follow_send_auto"])
        self.send_auto.toggled.connect(self._save)
        out.add(self.send_auto)
        self.add(out)

        self.log = LogPanel(self.jobs, 140)
        self.log.view.setMaximumHeight(220)
        self.add(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(60_000)
        self.refresh()

    # --- state

    def _save(self, *_):
        SETTINGS["follow_get"] = self.get.currentData()
        SETTINGS["follow_auto"] = self.auto.isChecked()
        SETTINGS["follow_send_auto"] = self.send_auto.isChecked()
        SETTINGS["follow_new_music"] = self.new_folder.text() or SETTINGS["follow_new_music"]
        SETTINGS.save()
        self.refresh()

    def load_settings(self):
        """Show values changed on the Settings page."""
        with QSignalBlocker(self.get), QSignalBlocker(self.auto), QSignalBlocker(self.send_auto):
            self.get.setCurrentIndex([k for k, _ in GETS].index(SETTINGS["follow_get"]))
            self.auto.setChecked(SETTINGS["follow_auto"])
            self.auto.setText(f"Sync automatically every {SETTINGS['follow_interval']} minutes while Music Tools "
                              f"is open")
            self.send_auto.setChecked(SETTINGS["follow_send_auto"])
            self.new_folder.setText(SETTINGS["follow_new_music"])
        self.refresh()

    def on_show(self):
        self.refresh()

    def refresh(self):
        m = self.follower.main
        self.main_status.setText(f"Following <b>{m['name']}</b>  ·  {m['hosts'][0]}:{m['port']}" if m else
                                 "Not paired yet. Pair with the PC that has your music library.")
        self.pair_btn.setVisible(not m)
        self.unpair_btn.setVisible(bool(m))
        self.sync_btn.setEnabled(bool(m) and not self.jobs.busy)
        get = SETTINGS["follow_get"]
        parts = [f"FLAC into {SETTINGS['library']}"] * (get in ("flac", "both")) + \
                [f"Opus into {SETTINGS['opus']}"] * (get in ("opus", "both"))
        self.where.setText(" · ".join(parts) + "  (change the folders in Settings)")
        last = self.follower.state.get("last") or {}
        self.last.setText(f"Last sync {_ago(last['time'])}: {last.get('got', 0)} downloaded, "
                          f"{last.get('removed', 0)} removed" if last.get("time") else "Not synced yet")
        self._fill_outbox()

    def _fill_outbox(self):
        self.tree.clear()
        errors = self.follower.state["errors"]
        files = self.follower.new_music()
        edits = self.follower.state["edits"]
        if files:
            sec = self._section(f"New music ({len(files)})")
            for p in files:
                item = QTreeWidgetItem(sec, [p.name, "Add to the library", errors.get(str(p), "")])
                item.setData(0, Qt.UserRole, ("file", str(p)))
                item.setCheckState(0, Qt.Checked)
                if str(p) in errors:
                    item.setForeground(2, QColor(theme.ERR))
        if edits:
            sec = self._section(f"Tag edits ({len(edits)})")
            for e in edits:
                change = ", ".join(_lyrics_change(old, new) if k == "lyrics" else
                                   f"{FIELD_LABELS.get(k, k)}: {old or '—'} → {new or '—'}"
                                   for k, (old, new) in e["changes"].items())
                note = ", ".join("main PC has other lyrics" if k == "lyrics" else
                                 f"main PC has {FIELD_LABELS.get(k, k).lower()} “{v}”"
                                 for k, v in e["conflicts"].items())
                note = note and f"Conflict: {note}"
                if e.get("ignored"):
                    names = ", ".join(FIELD_LABELS.get(k, k).lower() for k in e["ignored"])
                    note = f"The main PC didn't take the {names}: update Music Tools there. " + note
                item = QTreeWidgetItem(sec, [Path(e["path"]).name, change, note.strip()])
                item.setData(0, Qt.UserRole, ("edit", e["id"]))
                item.setToolTip(1, change)
                item.setCheckState(0, Qt.Unchecked if e["conflicts"] else Qt.Checked)
                if e["conflicts"]:
                    item.setForeground(2, QColor(theme.WARN))
                    item.setToolTip(2, "Changed on the main PC too. “Keep mine” overwrites it, “Use the main PC's” "
                                       "drops your edit.")
        if not files and not edits:
            empty = QTreeWidgetItem(["Nothing to send. Drop music into the New music folder, or edit tags in the "
                                     "Tag editor."])
            empty.setFirstColumnSpanned(True)
            empty.setForeground(0, QColor(theme.MUTED))
            self.tree.addTopLevelItem(empty)
        self.tree.expandAll()

    def _section(self, title):
        sec = QTreeWidgetItem([title])
        sec.setFirstColumnSpanned(True)
        font = sec.font(0)
        font.setBold(True)
        sec.setFont(0, font)
        self.tree.addTopLevelItem(sec)
        return sec

    def _items(self, checked=None, selected=None):
        out = []
        for i in range(self.tree.topLevelItemCount()):
            sec = self.tree.topLevelItem(i)
            for j in range(sec.childCount()):
                it = sec.child(j)
                if not it.data(0, Qt.UserRole):
                    continue
                if checked and it.checkState(0) != Qt.Checked:
                    continue
                if selected and not it.isSelected():
                    continue
                out.append(it.data(0, Qt.UserRole))
        return out

    # --- actions

    def pair(self):
        dlg = PairMainDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        host, port, code = dlg.host.text().strip(), dlg.port.value(), dlg.code.text().strip()
        if not host or not code:
            return
        self.main_status.setText("Pairing…")

        def done(res):
            if isinstance(res, Exception):
                QMessageBox.warning(self, "Pairing failed", str(res))
            else:
                self.log.append(f"Paired with {res['name']}.", "ok")
                self.win.apply_role()
                if QMessageBox.question(self, "Paired", f"Paired with {res['name']}. Sync now?") == QMessageBox.Yes:
                    self.sync(manual=True)
            self.refresh()

        self.bg.run(lambda: self.follower.pair(host, port, code, dlg.short_fp), done)

    def unpair(self):
        if QMessageBox.question(self, "Unpair", "Stop following the main PC? Your music stays on this computer.") \
                == QMessageBox.Yes:
            self.bg.run(self.follower.unpair, lambda _: (self.refresh(), self.win.apply_role()))

    def _tick(self):
        last = (self.follower.state.get("last") or {}).get("time", 0)
        due = time.time() - last >= SETTINGS["follow_interval"] * 60
        if SETTINGS["role"] == "follower" and SETTINGS["follow_auto"] and self.follower.main and due \
                and not self.jobs.busy:
            self.sync(manual=False)

    def sync(self, manual):
        def job(ctx):
            return self.follower.sync(ctx)

        def done(_):
            self.store.reload()
            self.refresh()

        self.jobs.start("Syncing with the main PC" if manual else "Automatic sync", job, log=self.log, on_done=done)

    def send(self):
        chosen = self._items(checked=True)
        files = [Path(v) for k, v in chosen if k == "file"]
        edits = {v for k, v in chosen if k == "edit"}
        if not files and not edits:
            return
        self._send(files, edits, force=set())

    def keep_mine(self):
        ids = {v for k, v in self._items(selected=True) if k == "edit"}
        conflicted = {e["id"] for e in self.follower.state["edits"] if e["id"] in ids and e["conflicts"]}
        if not conflicted:
            QMessageBox.information(self, "Keep mine", "Select one or more conflicts first.")
            return
        self._send([], conflicted, force=conflicted)

    def use_theirs(self):
        ids = {v for k, v in self._items(selected=True) if k == "edit"}
        if not ids:
            QMessageBox.information(self, "Use the main PC's", "Select one or more tag edits first.")
            return
        self.follower.discard_edits(ids)
        self.log.append(f"Dropped {len(ids)} edit(s); the next sync brings back the main PC's tags.")
        self.refresh()

    def _send(self, files, edits, force):
        def job(ctx):
            res = self.follower.send(ctx, files=files, edit_ids=edits, force=force)
            ctx.log(f"Sent {res['sent']} item(s)" + (f", {res['conflicts']} conflict(s) to decide."
                                                    if res["conflicts"] else "."), "ok")
            return res

        self.jobs.start("Sending to the main PC", job, log=self.log, on_done=lambda _: self.refresh())

    def shutdown(self):
        self.timer.stop()
