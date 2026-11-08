import os
import sys

from PySide6.QtWidgets import QMessageBox

from ...core.util import APP_NAME, APP_VERSION, SETTINGS, find_tool
from ..main_window import Page
from ..widgets import Card, FormGrid, PathPicker, button, hbox, label

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
    subtitle = "Folders and external tools. Changes are saved when you press Save."
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
        SETTINGS.save()
        self.on_show()
        if changed:
            self.store.reload()
        self.win.statusBar().showMessage("Settings saved", 3000)
