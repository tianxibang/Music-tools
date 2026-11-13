"""Asking about a new version: the release notes, then Update now / Later / Skip this version."""

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QMessageBox, QTextBrowser, QVBoxLayout

from ..core import update
from ..core.jobs import Cancelled
from ..core.util import APP_NAME, APP_VERSION, SETTINGS
from .widgets import button, hbox, label

KIND_NAMES = {"installer": "Windows installer", "rpm": "Linux package (.rpm)", "deb": "Linux package (.deb)",
              "portable": "portable folder", "source": "from source"}


class UpdateDialog(QDialog):
    def __init__(self, parent, release):
        super().__init__(parent)
        self.release, self.choice = release, "later"
        self.setWindowTitle("Update available")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label(f"{APP_NAME} {release['version']} is available", "cardTitle"))
        installable = update.can_install() and update.asset_for(release) is not None
        sub = f"You have {APP_VERSION}. "
        if installable:
            sub += "It downloads in the background; the app then restarts in the new version."
        elif update.install_kind() == "source":
            sub += "This copy runs from source: update it with git pull."
        else:
            sub += "This copy can't update itself, so get it from the download page."
        lay.addWidget(label(sub, "muted", wrap=True))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        # The downloads table under the "---" is for the web page
        notes.setMarkdown(release["notes"].split("\n---\n")[0].strip() or "No release notes.")
        lay.addWidget(notes, 1)

        main = button("Update now", "primary", "down") if installable else \
            button("Open download page", "primary", "down")
        main.clicked.connect(lambda: self._done("update" if installable else "page"))
        later = button("Later")
        later.clicked.connect(lambda: self._done("later"))
        skip = button("Skip this version", tip="Don't ask about this version again. Settings → Check now "
                                               "still finds it.")
        skip.clicked.connect(lambda: self._done("skip"))
        lay.addLayout(hbox(skip, None, later, main))
        main.setDefault(True)

    def _done(self, choice):
        self.choice = choice
        self.accept()


def check_on_startup(win):
    """A few seconds after start, look for a new version (if enabled) and ask about it."""
    def found(rel):
        if isinstance(rel, Exception) or rel is None or rel["tag"] == SETTINGS.get("update_skip"):
            return  # offline or up to date: say nothing
        ask(win, rel)

    if SETTINGS.get("update_check", True):
        QTimer.singleShot(4000, lambda: win.bg.run(update.check, found))
    win.bg.run(update.clean_leftovers)


def check_now(win, done=None):
    """Settings → Check now: also says when there is nothing new or the check failed."""
    def found(rel):
        if done:
            done()
        if isinstance(rel, Exception):
            QMessageBox.warning(win, "Updates", f"Couldn't reach GitHub to check for updates:\n{rel}")
        elif rel is None:
            QMessageBox.information(win, "Updates", f"You have the latest version ({APP_VERSION}).")
        else:
            ask(win, rel)

    win.bg.run(update.check, found)


def ask(win, rel):
    dlg = UpdateDialog(win, rel)
    dlg.exec()
    if dlg.choice == "skip":
        SETTINGS["update_skip"] = rel["tag"]
        SETTINGS.save()
    elif dlg.choice == "page":
        QDesktopServices.openUrl(QUrl(rel["url"]))
    elif dlg.choice == "update":
        start(win, rel)


def start(win, rel):
    asset = update.asset_for(rel)

    def job(ctx):
        try:
            file = update.download(ctx, asset)
            ctx.check()
            return {"cmd": update.install(ctx, file)}
        except Cancelled:
            raise
        except Exception as e:  # shown in a dialog, not only in the log
            return {"error": str(e)}

    def finished(res):
        if res.get("error"):
            QMessageBox.warning(win, "Update", res["error"])
            return
        if res["cmd"]:
            update.launch_detached(res["cmd"])
        if not win.close():  # the user kept it open (e.g. unsaved lyrics): the swap waits until it quits
            QMessageBox.information(win, "Update", "The update is installed when you quit Music Tools.")

    win.jobs.start(f"Updating to {rel['version']}", job, on_done=finished)
