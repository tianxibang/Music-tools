import time

from PySide6.QtCore import QSignalBlocker, QSize, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QInputDialog, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout)

from ...core import sync
from ...core.util import SETTINGS
from .. import icons, theme
from ..main_window import Page
from ..widgets import Card, FormGrid, LogPanel, Toggle, button, hbox, label

CONFIRM = [("always", "Always ask before syncing"), ("deleting", "Ask only when files will be deleted"),
           ("never", "Never ask")]


class PairDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Pair over Wi-Fi")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        lay.addWidget(label("Pair a phone", "cardTitle"))
        lay.addWidget(label("On the phone: Settings → Developer options → Wireless debugging → "
                            "<b>Pair device with pairing code</b>. Enter the IP address &amp; port and the "
                            "six-digit code it shows. The phone and this computer must be on the same network.",
                            "muted", wrap=True))
        form = FormGrid()
        self.addr = form.add("IP address & port", QLineEdit())
        self.addr.setPlaceholderText("192.168.1.42:37123")
        self.code = form.add("Pairing code", QLineEdit())
        self.code.setPlaceholderText("123456")
        lay.addWidget(form)
        lay.addWidget(label("After pairing, use <b>Connect</b> with the address shown on the main "
                            "Wireless debugging screen (it has a different port).", "hint", wrap=True))
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Pair")
        btns.button(QDialogButtonBox.Ok).setProperty("kind", "primary")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)


class SyncPage(Page):
    title = "Sync to phone"
    subtitle = "Mirror the Opus library onto an Android phone over USB or Wi-Fi."
    icon = "phone"

    def __init__(self, win, embedded=False):
        super().__init__(win, embedded)
        dev_card = Card("Phone connection", "Needs USB debugging (cable) or Wireless debugging (Wi-Fi) "
                        "turned on in the phone's Developer options.", icon="phone")
        self.dev_list = QListWidget()
        self.dev_list.setIconSize(QSize(18, 18))
        self.dev_list.setFixedHeight(132)
        self.dev_list.currentItemChanged.connect(self._pick)
        refresh = button("Refresh", icon="refresh")
        refresh.clicked.connect(self.refresh)
        pair = button("Pair Wireless debugging…", icon="wifi")
        pair.clicked.connect(self.pair)
        connect = button("Connect…", icon="link")
        connect.clicked.connect(self.connect)
        discover = button("Find on network", icon="search")
        discover.clicked.connect(self.discover)
        self.wifi_auto = Toggle("Reconnect to the last Wi-Fi phone automatically", SETTINGS["wifi_autoconnect"])
        self.wifi_auto.toggled.connect(lambda v: (SETTINGS.__setitem__("wifi_autoconnect", v), SETTINGS.save()))
        buttons = QVBoxLayout()
        buttons.setSpacing(8)
        for b in (refresh, pair, connect, discover):
            buttons.addWidget(b)
        buttons.addStretch(1)
        dev_card.add(hbox(self.dev_list, buttons, spacing=12))
        dev_card.add(self.wifi_auto)
        self.dev_status = label("", "muted", wrap=True)
        dev_card.add(self.dev_status)

        opt = Card("Copy music to the phone", icon="refresh")
        form = FormGrid()
        self.local = label(SETTINGS["opus"])
        form.add("From", self.local)
        self.remote = QLineEdit(SETTINGS["remote"])
        self.remote.editingFinished.connect(self._save)
        form.add("Phone folder", self.remote)
        self.keep = Toggle("Keep files on the phone that aren't in the library", SETTINGS["keep"])
        self.keep.toggled.connect(self._save)
        form.add("", self.keep, "Off = true mirror: songs you deleted locally are removed from the phone too.")
        self.confirm = QComboBox()
        for key, text in CONFIRM:
            self.confirm.addItem(text, key)
        self.confirm.setCurrentIndex([k for k, _ in CONFIRM].index(SETTINGS["sync_confirm"]))
        self.confirm.currentIndexChanged.connect(self._save)
        form.add("Confirmation", self.confirm)
        opt.add(form)
        self.preview_btn = button("Preview changes", icon="eye")
        self.preview_btn.clicked.connect(lambda: self.run(True))
        self.sync_btn = button("Sync now", "primary", "play")
        self.sync_btn.clicked.connect(lambda: self.run(False))
        self.lock(self.preview_btn, self.sync_btn, pair, connect)
        opt.add(hbox(self.preview_btn, self.sync_btn, None))

        self.add(dev_card)
        self.add(opt)
        self.log = LogPanel(self.jobs, 260)
        self.add(self.log, 1)
        self._autoconnect_tried = False

    def on_show(self):
        self.local.setText(SETTINGS["opus"])
        self.refresh()

    def load_settings(self):
        """Show values changed on the Settings page."""
        with QSignalBlocker(self.remote), QSignalBlocker(self.keep), QSignalBlocker(self.confirm):
            self.remote.setText(SETTINGS["remote"])
            self.keep.setChecked(SETTINGS["keep"])
            self.confirm.setCurrentIndex([k for k, _ in CONFIRM].index(SETTINGS["sync_confirm"]))

    def _save(self):
        SETTINGS["remote"] = self.remote.text().strip() or "/sdcard/Music/opus_export"
        SETTINGS["keep"] = self.keep.isChecked()
        SETTINGS["sync_confirm"] = self.confirm.currentData()
        SETTINGS.save()

    def _pick(self, item, _prev=None):
        if item and item.data(Qt.UserRole):
            SETTINGS["serial"] = item.data(Qt.UserRole)
            SETTINGS.save()

    # --- devices

    def refresh(self):
        if not sync.adb():
            self.dev_list.clear()
            self.dev_status.setText("adb wasn't found. Install Android platform-tools "
                                    "(Fedora: sudo dnf install android-tools) or set its path in Settings.")
            return
        self.dev_status.setText("Looking for devices…")
        auto = (SETTINGS["wifi_autoconnect"] and SETTINGS["wifi_addr"] and not self._autoconnect_tried)
        self._autoconnect_tried = True

        def work():
            devs = sync.devices()
            if auto and not any(d.ready for d in devs):
                sync.connect(SETTINGS["wifi_addr"])
                devs = sync.devices()
            return devs

        self.bg.run(work, self._show_devices)

    def _show_devices(self, devs):
        if isinstance(devs, Exception):
            self.dev_status.setText(str(devs))
            return
        self.dev_list.clear()
        for d in devs:
            state = {"device": "ready", "unauthorized": "tap Allow on the phone", "offline": "offline"}.get(
                d.state, d.state)
            item = QListWidgetItem(icons.icon("wifi" if d.wireless else "usb",
                                              theme.OK if d.ready else theme.WARN), f"{d.label}   —   {state}")
            item.setData(Qt.UserRole, d.serial if d.ready else None)
            self.dev_list.addItem(item)
            if d.serial == SETTINGS["serial"] and d.ready:
                self.dev_list.setCurrentItem(item)
        if not devs:
            self.dev_status.setText("No devices. Plug the phone in with USB debugging enabled, or pair over Wi-Fi.")
        else:
            ready = sum(d.ready for d in devs)
            self.dev_status.setText(f"{ready} ready device(s)." + ("" if ready else
                                    " Unlock the phone and accept the USB debugging prompt."))
        if self.dev_list.currentItem() is None:
            for i in range(self.dev_list.count()):
                if self.dev_list.item(i).data(Qt.UserRole):
                    self.dev_list.setCurrentRow(i)
                    break

    def pair(self):
        dlg = PairDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        addr, code = dlg.addr.text().strip(), dlg.code.text().strip()
        if not addr or not code:
            return
        self.dev_status.setText(f"Pairing with {addr}…")
        self.bg.run(lambda: sync.pair(addr, code), self._paired)

    def _paired(self, res):
        if isinstance(res, Exception):
            res = (False, str(res))
        ok, msg = res
        self.dev_status.setText(msg)
        if ok:
            QMessageBox.information(self, "Paired", "Paired! Now press Connect and enter the IP address & port "
                                    "from the main Wireless debugging screen.")
            self.connect()

    def connect(self, addr=None):
        if not addr:
            addr, ok = QInputDialog.getText(self, "Connect over Wi-Fi",
                                            "IP address & port from the Wireless debugging screen:",
                                            text=SETTINGS["wifi_addr"])
            if not ok or not addr.strip():
                return
        addr = addr.strip()
        self.dev_status.setText(f"Connecting to {addr}…")

        def done(res):
            ok, msg = res if not isinstance(res, Exception) else (False, str(res))
            if ok:
                SETTINGS["wifi_addr"] = addr
                SETTINGS.save()
            self.dev_status.setText(msg)
            self.refresh()

        self.bg.run(lambda: sync.connect(addr), done)

    def discover(self):
        if not sync.adb():
            return
        self.dev_status.setText("Searching the network for phones with Wireless debugging on…")

        def done(found):
            if isinstance(found, Exception) or not found:
                self.dev_status.setText("Nothing found. Open Wireless debugging on the phone (and pair it first).")
                return
            names = [f"{name}  ({addr})" for name, addr in found]
            choice, ok = QInputDialog.getItem(self, "Phones on the network", "Connect to:", names, 0, False)
            if ok:
                self.connect(found[names.index(choice)][1])

        self.bg.run(sync.mdns_services, done)

    # --- sync

    def run(self, preview):
        self._save()
        local, remote, keep, serial = SETTINGS.opus, SETTINGS["remote"], SETTINGS["keep"], SETTINGS["serial"]
        policy = SETTINGS["sync_confirm"]

        def job(ctx):
            plan = sync.make_plan(ctx, local, remote, keep, serial, dry=preview)
            if plan is None:
                return
            if not plan.to_copy and not plan.to_delete:
                ctx.log("Phone is already up to date.", "ok")
                SETTINGS["last_sync"] = time.time()
                return
            if preview:
                ctx.log("(preview only — nothing was changed)", "muted")
                return
            msg = f"Copy {len(plan.to_copy)} file(s) to the phone"
            msg += f" and delete {len(plan.to_delete)} file(s) from it?" if plan.to_delete else "?"
            if (policy == "always" or (policy == "deleting" and plan.to_delete)) and not ctx.ask("Sync", msg):
                ctx.log("Cancelled.", "warn")
                return
            if sync.execute(ctx, plan):
                SETTINGS["last_sync"] = time.time()
            SETTINGS.save()

        self.jobs.start("Preview" if preview else "Syncing", job, log=self.log)
