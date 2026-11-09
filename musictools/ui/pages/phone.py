from PySide6.QtWidgets import QButtonGroup, QMessageBox, QRadioButton, QStackedWidget, QVBoxLayout, QWidget

from ...core.util import SETTINGS
from ..main_window import Page
from ..widgets import Card, label, vbox
from .devices import DevicesPage
from .follower import FollowerPanel
from .sync import SyncPage

ROLES = [
    ("main", "Main library",
     "Your music lives here. Phones and other computers sync with this PC."),
    ("follower", "Follows another PC",
     "Mirrors the main PC's library (FLAC, Opus or both) and sends it new music and tag edits. Most library "
     "tools are then used on the main PC."),
]

MODES = [
    ("app", "Music Tools phone app (recommended)",
     "Install the app on the phone. Syncs both ways in the background, over Wi-Fi or a USB cable: new FLACs "
     "from the phone go into your library, and the Opus versions come back."),
    ("adb", "Without the app (adb)",
     "Copies the Opus library onto the phone using Android's developer tools. One way only, needs USB or "
     "Wireless debugging on the phone, and you start each sync from here."),
]


def _choices(card, options, current):
    group = QButtonGroup(card)
    for i, (key, title, desc) in enumerate(options):
        radio = QRadioButton(title)
        radio.setChecked(current == key)
        group.addButton(radio, i)
        desc_label = label(desc, "hint", wrap=True)
        desc_label.setContentsMargins(26, 0, 0, 6)
        card.add(vbox(radio, desc_label, spacing=2))
    return group


class PhonePage(Page):
    """Syncing: whether this computer is the main library or follows another PC, and how the phone syncs."""
    title = "Sync"
    subtitle = "Your phone and your other computers."
    icon = "phone"

    def __init__(self, win):
        super().__init__(win)
        role = Card("This computer", icon="disk")
        self.roles = _choices(role, ROLES, SETTINGS["role"])
        self.roles.idToggled.connect(self._role_changed)
        self.add(role)

        self.main_box = QWidget()
        box = QVBoxLayout(self.main_box)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(16)
        card = Card("How should your phone sync?", icon="phone")
        self.group = _choices(card, MODES, SETTINGS["phone_mode"])
        self.group.idToggled.connect(self._mode_changed)
        box.addWidget(card)

        self.app = DevicesPage(win, embedded=True)
        self.adb = SyncPage(win, embedded=True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.app)
        self.stack.addWidget(self.adb)
        box.addWidget(self.stack, 1)
        self.add(self.main_box, 1)
        self.follower_panel = FollowerPanel(win)
        self.add(self.follower_panel, 1)
        self._show_mode()
        self._show_role()
        self.app.bridge.changed.connect(self._update_dashboard)

    def _update_dashboard(self, *_):
        from .dashboard import DashboardPage
        for p in self.win.pages:
            if isinstance(p, DashboardPage):
                p.refresh_phone()

    @property
    def panel(self):
        return self.follower_panel if SETTINGS["role"] == "follower" else self.stack.currentWidget()

    def _role_changed(self, idx, checked):
        if not checked:
            return
        role = ROLES[idx][0]
        if role == SETTINGS["role"]:
            return
        msg = ("This computer will mirror another PC's library. Its own sync server is turned off (pair your phone "
               "with the main PC instead), and library tools like Convert and Import move to the main PC."
               if role == "follower" else
               "This computer becomes a main library again: it stops mirroring the other PC, and your phone and "
               "other computers can sync with it.")
        if QMessageBox.question(self, "This computer", msg + "\n\nContinue?") != QMessageBox.Yes:
            self.roles.blockSignals(True)
            self.roles.button([k for k, _, _ in ROLES].index(SETTINGS["role"])).setChecked(True)
            self.roles.blockSignals(False)
            return
        if role == "follower" and self.app.server.running:
            self.app.enabled.setChecked(False)
        SETTINGS["role"] = role
        SETTINGS.save()
        self._show_role()
        self.win.apply_role()
        self.panel.on_show()

    def _show_role(self):
        follower = SETTINGS["role"] == "follower"
        self.main_box.setVisible(not follower)
        self.follower_panel.setVisible(follower)

    def _mode_changed(self, idx, checked):
        if not checked:
            return
        SETTINGS["phone_mode"] = MODES[idx][0]
        SETTINGS.save()
        self._show_mode()
        self.panel.on_show()
        self._update_dashboard()

    def _show_mode(self):
        self.stack.setCurrentWidget(self.app if SETTINGS["phone_mode"] == "app" else self.adb)

    def on_show(self):
        self.panel.on_show()

    def shutdown(self):
        self.app.shutdown()
        self.follower_panel.shutdown()
