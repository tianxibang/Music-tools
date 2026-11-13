import os
import sys

from PySide6.QtWidgets import QMessageBox

from ...core import update
from ...core.util import APP_NAME, APP_VERSION, SETTINGS, find_tool
from ..main_window import Page
from ..update_dialog import KIND_NAMES, check_now
from ..widgets import Card, FormGrid, PathPicker, Toggle, button, hbox, label

TOOLS = [("opusenc", "opusenc", "opus-tools — best Opus encoder (optional if ffmpeg is installed)"),
         ("ffmpeg", "ffmpeg", "Conversion fallback, loudness, health checks"),
         ("adb", "adb", "Android platform-tools — phone sync")]

INSTALL_HINTS = {
    "linux": "Fedora: sudo dnf install opus-tools ffmpeg android-tools   ·   "
             "Debian/Ubuntu: sudo apt install opus-tools ffmpeg adb   ·   Arch: sudo pacman -S opus-tools ffmpeg android-tools",
    "win32": "Windows: winget install Gyan.FFmpeg Google.PlatformTools — or drop opusenc.exe / ffmpeg.exe / adb.exe "
             "into a “bin” folder next to the app.",
}


class SettingsPage(Page):
    title = "Settings"
    subtitle = "Folders, external tools and updates. Changes are saved when you press Save."
    icon = "settings"

    def __init__(self, win):
        super().__init__(win)
        folders = Card("Folders", icon="folder")
        form = FormGrid()
        self.library = form.add("FLAC library", PathPicker(SETTINGS["library"]),
                                "Your lossless master copies. Playlists are stored in its “Playlists” subfolder.")
        self.opus = form.add("Opus export", PathPicker(SETTINGS["opus"]),
                             "Generated copies for the phone. Mirrors the library's folder layout.")
        folders.add(form)
        self.add(folders)

        tools = Card("External tools", "Leave empty to find them automatically (next to the app, in its “bin” "
                     "folder, or on PATH).", icon="settings")
        tform = FormGrid()
        self.tool_fields = {}
        for key, name, desc in TOOLS:
            picker = PathPicker(SETTINGS.get(f"tool_{key}", ""), file=True)
            self.tool_fields[key] = picker
            tform.add(name, picker, desc)
        tools.add(tform)
        self.tool_status = label("", "muted", wrap=True)
        tools.add(self.tool_status)
        tools.add(label(INSTALL_HINTS.get(sys.platform if sys.platform == "win32" else "linux"), "hint", wrap=True))
        self.add(tools)

        updates = Card("Updates", f"New versions come from GitHub. You'll see what's new and choose when to "
                       f"install. This copy is installed as: {KIND_NAMES[update.install_kind()]}.", icon="refresh")
        self.update_check = Toggle("Check for updates when Music Tools starts", SETTINGS["update_check"])
        self.check_btn = button("Check now", icon="refresh")
        self.check_btn.clicked.connect(self.check_updates)
        updates.add(hbox(self.update_check, None, self.check_btn))
        self.add(updates)

        save = button("Save", "primary", "check")
        save.clicked.connect(self.save)
        self.add(hbox(save, None))

        about = Card(f"{APP_NAME} {APP_VERSION}")
        about.add(label(f"Settings file: {SETTINGS.path}", "hint", wrap=True))
        self.add(about)
        self.body.addStretch(1)
        self.on_show()

    def on_show(self):
        parts = []
        for key, name, _ in TOOLS:
            found = find_tool(key)
            color = "#34d399" if found else ("#fbbf24" if key == "opusenc" else "#f87171")
            parts.append(f"<span style='color:{color}'>●</span>&nbsp;{name}: "
                         f"<span style='color:#8a91a3'>{found or 'not found'}</span>")
        self.tool_status.setText("<br>".join(parts))

    def check_updates(self):
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking…")
        check_now(self.win, lambda: (self.check_btn.setEnabled(True), self.check_btn.setText("Check now")))

    def save(self):
        lib, opus = self.library.text(), self.opus.text()
        if not os.path.isdir(lib):
            QMessageBox.warning(self, "Settings", f"The library folder doesn't exist:\n{lib}")
            return
        if os.path.abspath(lib) == os.path.abspath(opus):
            QMessageBox.warning(self, "Settings", "The library and Opus folders must be different.")
            return
        changed = lib != SETTINGS["library"]
        SETTINGS["library"], SETTINGS["opus"] = lib, opus
        for key, picker in self.tool_fields.items():
            SETTINGS[f"tool_{key}"] = picker.text()
        SETTINGS["update_check"] = self.update_check.isChecked()
        SETTINGS.save()
        self.on_show()
        if changed:
            self.store.reload()
        self.win.statusBar().showMessage("Settings saved", 3000)
