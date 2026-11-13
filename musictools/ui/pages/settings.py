"""Every option in one place: sections that mirror the app, a search box, and a reset for each option.

Changes are saved as they are made. Pages with their own quick controls for an option get load_settings()
called so they show the new value.
"""

import json
import os
import sys

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QLineEdit,
                               QMessageBox, QSpinBox, QWidget)

from ...core import update
from ...core.options import BY_KEY, OPTIONS, SECTIONS
from ...core.util import APP_NAME, APP_VERSION, DEFAULTS, SETTINGS, find_tool
from ..main_window import Page
from ..update_dialog import KIND_NAMES, check_now
from ..widgets import Card, PathPicker, Toggle, button, hbox, label, search_box

INSTALL_HINTS = {
    "linux": "Fedora: sudo dnf install opus-tools ffmpeg android-tools   ·   "
             "Debian/Ubuntu: sudo apt install opus-tools ffmpeg adb   ·   Arch: sudo pacman -S opus-tools ffmpeg android-tools",
    "win32": "Windows: winget install Gyan.FFmpeg Google.PlatformTools — or drop opusenc.exe / ffmpeg.exe / adb.exe "
             "into a “bin” folder next to the app.",
}
# Shown in a section, for things that are switched on a page because they start or stop something
SECTION_NOTES = {
    "phone": "Whether the phone syncs through the app or over USB / Wi-Fi debugging is chosen on the Phone page.",
    "follow": "Turning the sync server on, pairing, and whether this computer follows another are on the Phone "
              "page.",
}


def describe(opt, value):
    """A value as the user sees it: 'On', '128 kbps', 'In the music file', …"""
    if opt.kind == "bool":
        return "On" if value else "Off"
    if opt.kind == "choice":
        return dict(opt.choices).get(value, str(value))
    if opt.kind == "multi":
        names = dict(opt.choices)
        return ", ".join(names.get(v, v) for v in value) or "none"
    if opt.kind in ("int", "float"):
        return f"{value:g} {opt.unit}".strip()
    return str(value) or "automatic"


class OptionRow(QFrame):
    """Label, control, reset button, and the help text with the default under them."""

    def __init__(self, opt, changed):
        super().__init__()
        self.opt, self.changed = opt, changed
        self.search_text = f"{opt.label} {opt.help} {SECTIONS_BY_KEY[opt.section]}".lower()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 1)
        title = label(opt.label, "muted")
        title.setMinimumWidth(240)
        title.setWordWrap(True)
        grid.addWidget(title, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self.control = self._make_control()
        grid.addLayout(hbox(self.control, None) if opt.kind not in ("folder", "file", "text") else
                       hbox(self.control), 0, 1)
        self.reset = button("", "ghost", "undo", f"Back to the default ({describe(opt, DEFAULTS[opt.key])})")
        self.reset.clicked.connect(lambda: self.commit(DEFAULTS[opt.key]))
        grid.addWidget(self.reset, 0, 2)
        grid.addWidget(label(f"{opt.help} Default: {describe(opt, DEFAULTS[opt.key])}.", "hint", wrap=True), 1, 1)
        self.load()

    def _make_control(self):
        opt = self.opt
        if opt.kind == "bool":
            w = Toggle()
            w.toggled.connect(lambda v: self.commit(v))
        elif opt.kind == "int":
            w = QSpinBox()
            w.setRange(int(opt.min), int(opt.max))
            w.setSingleStep(int(opt.step))
            w.setSuffix(f" {opt.unit}" if opt.unit else "")
            w.setFixedWidth(max(140, 60 + 7 * len(opt.unit)))
            w.editingFinished.connect(lambda: self.commit(w.value()))
        elif opt.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(opt.min, opt.max)
            w.setSingleStep(opt.step)
            w.setDecimals(2 if opt.step < 0.1 else 1)
            w.setSuffix(f" {opt.unit}" if opt.unit else "")
            w.setFixedWidth(max(140, 70 + 7 * len(opt.unit)))
            w.editingFinished.connect(lambda: self.commit(w.value()))
        elif opt.kind == "choice":
            w = QComboBox()
            for value, text in opt.choices:
                w.addItem(text, value)
            w.setMinimumWidth(220)
            w.activated.connect(lambda _: self.commit(w.currentData()))
        elif opt.kind == "multi":
            w = QWidget()
            self.boxes = {}
            boxes = []
            for value, text in opt.choices:
                box = QCheckBox(text)
                box.toggled.connect(lambda _: self.commit([v for v, b in self.boxes.items() if b.isChecked()]))
                self.boxes[value] = box
                boxes.append(box)
            w.setLayout(hbox(*boxes, spacing=14))
        elif opt.kind in ("folder", "file"):
            w = PathPicker(file=opt.kind == "file")
            w.edit.editingFinished.connect(lambda: self.commit(w.text()))
            w.edit.textChanged.connect(lambda _: w.edit.isModified() or self.commit(w.text()))  # Browse…
        else:
            w = QLineEdit()
            w.editingFinished.connect(lambda: self.commit(w.text()))
        return w

    def load(self):
        """Show the saved value."""
        opt, value, w = self.opt, SETTINGS[self.opt.key], self.control
        blockers = [QSignalBlocker(w)] + [QSignalBlocker(b) for b in getattr(self, "boxes", {}).values()]
        if opt.kind in ("folder", "file"):
            blockers.append(QSignalBlocker(w.edit))
        if opt.kind == "bool":
            w.setChecked(value)
        elif opt.kind in ("int", "float"):
            w.setValue(value)
        elif opt.kind == "choice":
            w.setCurrentIndex(max(0, w.findData(value)))
        elif opt.kind == "multi":
            for v, box in self.boxes.items():
                box.setChecked(v in value)
        else:
            w.setText(value)
        for b in blockers:
            b.unblock()
        self.reset.setVisible(value != DEFAULTS[opt.key])

    def commit(self, value):
        try:
            value = self.opt.check(value)
        except ValueError as e:
            QMessageBox.warning(self, "Settings", str(e))
        else:
            if value != SETTINGS[self.opt.key]:
                self.changed(self.opt, value)
        self.load()


SECTIONS_BY_KEY = {key: title for key, title, _ in SECTIONS}


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Everything you can change, in one place. Changes are saved right away."
    icon = "settings"

    def __init__(self, win):
        super().__init__(win)
        self.search = search_box("Search settings…")
        self.search.textChanged.connect(self.filter)
        self.add(hbox(self.search, None))
        self.rows, self.cards = [], []
        for key, title, icon in SECTIONS:
            card = Card(title, SECTION_NOTES.get(key), icon=icon)
            rows = [OptionRow(o, self.changed) for o in OPTIONS if o.section == key]
            for r in rows:
                card.add(r)
            self.rows += rows
            if key == "updates":
                self._updates(card)
            elif key == "tools":
                self._tools(card)
            self.cards.append((card, rows))
            self.add(card)
        self.add(self._advanced())
        self.body.addStretch(1)
        self.on_show()

    def _updates(self, card):
        card.add(label(f"New versions come from GitHub; you see what's new and choose when to install. This copy is "
                       f"installed as: {KIND_NAMES[update.install_kind()]}.", "hint", wrap=True))
        self.check_btn = button("Check now", icon="refresh")
        self.check_btn.clicked.connect(self.check_updates)
        card.add(hbox(self.check_btn, None))

    def _tools(self, card):
        self.tool_status = label("", "muted", wrap=True)
        card.add(self.tool_status)
        card.add(label(INSTALL_HINTS.get(sys.platform if sys.platform == "win32" else "linux"), "hint", wrap=True))

    def _advanced(self):
        card = Card(f"{APP_NAME} {APP_VERSION}", f"Settings file: {SETTINGS.path}", icon="settings")
        folder = button("Open folder", icon="folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SETTINGS.path.parent))))
        export = button("Export…", icon="up", tip="Save your settings to a file, e.g. for another computer")
        export.clicked.connect(self.export)
        imp = button("Import…", icon="down", tip="Load settings saved with Export")
        imp.clicked.connect(self.import_)
        reset = button("Reset all…", "danger", "undo", "Put every option on this page back to its default")
        reset.clicked.connect(self.reset_all)
        card.add(hbox(folder, export, imp, None, reset))
        self.advanced = card
        return card

    # --- changes

    def changed(self, opt, value):
        if opt.key in ("library", "opus"):
            other = SETTINGS["opus" if opt.key == "library" else "library"]
            if opt.key == "library" and not os.path.isdir(value):
                QMessageBox.warning(self, "Settings", f"The library folder doesn't exist:\n{value}")
                return
            if os.path.abspath(value) == os.path.abspath(other):
                QMessageBox.warning(self, "Settings", "The library and Opus folders must be different.")
                return
        self.apply({opt.key: value})

    def apply(self, values):
        """Save new values and let the rest of the app know."""
        old = dict(SETTINGS)
        SETTINGS.update(values)
        SETTINGS.validate()
        SETTINGS.save()
        if SETTINGS["library"] != old["library"]:
            self.store.reload()
        if SETTINGS["watch_interval"] != old["watch_interval"]:
            self.win.watch_timer.setInterval(SETTINGS["watch_interval"] * 1000)
        for page in self.win.findChildren(Page):
            if hasattr(page, "load_settings"):
                page.load_settings()
        for row in self.rows:
            row.load()
        self.on_show()
        self.win.statusBar().showMessage("Settings saved", 2000)

    def reset_all(self):
        if QMessageBox.question(self, "Reset all settings", "Put every option on this page back to its default? "
                                "Pairings, the phone connection and other things the app remembers stay.") \
                != QMessageBox.Yes:
            return
        values = {o.key: DEFAULTS[o.key] for o in OPTIONS}
        if not os.path.isdir(values["library"]):  # the default folder may not exist here: keep the library
            values.pop("library")
        self.apply(values)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", str(SETTINGS.path.parent /
                                              "music-tools-settings.json"), "Settings (*.json)")
        if path:
            data = {o.key: SETTINGS[o.key] for o in OPTIONS}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.win.statusBar().showMessage(f"Settings exported to {path}", 4000)

    def import_(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", str(SETTINGS.path.parent),
                                              "Settings (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a settings file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import settings", f"Couldn't read {path}:\n{e}")
            return
        # Only the options on this page: never pairings, tokens or what the app remembers
        values = {k: v for k, v in data.items() if k in BY_KEY}
        if "library" in values and not os.path.isdir(str(values["library"])):
            values.pop("library")
        self.apply(values)
        QMessageBox.information(self, "Import settings", f"{len(values)} setting(s) imported.")

    def check_updates(self):
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking…")
        check_now(self.win, lambda: (self.check_btn.setEnabled(True), self.check_btn.setText("Check now")))

    # --- view

    def filter(self, text):
        words = text.lower().split()
        for card, rows in self.cards:
            shown = 0
            for r in rows:
                visible = all(w in r.search_text for w in words)
                r.setVisible(visible)
                shown += visible
            title = card.findChild(QWidget, "cardTitle")
            match_card = bool(words) and title is not None and all(w in title.text().lower() for w in words)
            if match_card:
                for r in rows:
                    r.setVisible(True)
            card.setVisible(not words or shown > 0 or match_card)
        self.advanced.setVisible(not words)

    def on_show(self):
        parts = []
        for key in ("opusenc", "ffmpeg", "adb"):
            found = find_tool(key)
            color = "#34d399" if found else ("#fbbf24" if key == "opusenc" else "#f87171")
            parts.append(f"<span style='color:{color}'>●</span>&nbsp;{key}: "
                         f"<span style='color:#8a91a3'>{found or 'not found'}</span>")
        self.tool_status.setText("<br>".join(parts))
