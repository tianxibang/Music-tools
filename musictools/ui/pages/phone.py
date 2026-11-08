from PySide6.QtWidgets import QButtonGroup, QRadioButton, QStackedWidget

from ...core.util import SETTINGS
from ..main_window import Page
from ..widgets import Card, label, vbox
from .devices import DevicesPage
from .sync import SyncPage

MODES = [
    ("app", "Music Tools phone app (recommended)",
     "Install the app on the phone. Syncs both ways in the background, over Wi-Fi or a USB cable: new FLACs "
     "from the phone go into your library, and the Opus versions come back."),
    ("adb", "Without the app (adb)",
     "Copies the Opus library onto the phone using Android's developer tools. One way only, needs USB or "
     "Wireless debugging on the phone, and you start each sync from here."),
]


class PhonePage(Page):
    """One place for the phone: pick how it syncs, then only that method's settings are shown."""
    title = "Phone"
    subtitle = "Keep your phone's music in step with the library."
    icon = "phone"

    def __init__(self, win):
        super().__init__(win)
        card = Card("How should your phone sync?", icon="phone")
        self.group = QButtonGroup(self)
        for i, (key, title, desc) in enumerate(MODES):
            radio = QRadioButton(title)
            radio.setChecked(SETTINGS["phone_mode"] == key)
            self.group.addButton(radio, i)
            desc_label = label(desc, "hint", wrap=True)
            desc_label.setContentsMargins(26, 0, 0, 6)
            card.add(vbox(radio, desc_label, spacing=2))
        self.group.idToggled.connect(self._mode_changed)
        self.add(card)

        self.app = DevicesPage(win, embedded=True)
        self.adb = SyncPage(win, embedded=True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.app)
        self.stack.addWidget(self.adb)
        self.add(self.stack, 1)
        self._show_mode()
        self.app.bridge.changed.connect(self._update_dashboard)

    def _update_dashboard(self, *_):
        from .dashboard import DashboardPage
        for p in self.win.pages:
            if isinstance(p, DashboardPage):
                p.refresh_phone()

    @property
    def panel(self):
        return self.stack.currentWidget()

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
