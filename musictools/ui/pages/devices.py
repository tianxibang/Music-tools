import sys
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHeaderView, QLabel, QMessageBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ...core.netsync import security, usb
from ...core.netsync.server import SyncServer, local_addresses
from ...core.util import SETTINGS, human_duration
from .. import theme
from ..main_window import Page
from ..widgets import Card, LogPanel, Toggle, button, hbox, label

STATE_TEXT = {"receiving": "Receiving", "received": "Waiting", "imported": "Added to library",
              "converting": "Converting", "ready": "Ready for phone", "duplicate": "Already in library",
              "failed": "Failed"}
STATE_COLORS = {"ready": theme.OK, "duplicate": theme.MUTED, "failed": theme.ERR}


class _Bridge(QObject):
    """Server callbacks arrive on worker threads; these signals carry them to the UI thread."""
    logged = Signal(str, str)
    changed = Signal(str)


def qr_pixmap(text, size=240):
    import segno
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtGui import QPainter
    svg = segno.make(text, error="m").svg_inline(scale=1, border=2, dark="#000000", light="#ffffff")
    renderer = QSvgRenderer(svg.encode())
    pm = QPixmap(size, size)
    pm.fill(QColor("#ffffff"))
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def _table(headers, widths):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.verticalHeader().setDefaultSectionSize(30)
    for i, w in enumerate(widths):
        t.setColumnWidth(i, w)
    t.horizontalHeader().setStretchLastSection(True)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    return t


def _ago(ts):
    if not ts:
        return "never"
    secs = time.time() - ts
    return "just now" if secs < 60 else f"{human_duration(secs)} ago"


class PairDialog(QDialog):
    """Shows the one-time code and a QR code with the certificate fingerprint for the phone to scan."""

    def __init__(self, parent, server):
        super().__init__(parent)
        self.server = server
        self.session = server.start_pairing()
        self.setWindowTitle("Pair a device")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(12)
        lay.addWidget(label("Open Music Tools on your phone, tap “Pair with PC” and scan this code.", wrap=True))
        qr = QLabel()
        qr.setPixmap(qr_pixmap(server.pairing_uri()))
        qr.setAlignment(Qt.AlignCenter)
        lay.addWidget(qr)
        lay.addWidget(label("Or type this code on the phone:", "muted"))
        code = QLabel(security.format_code(self.session.code))
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code.setAlignment(Qt.AlignCenter)
        f = theme.mono_font()
        f.setPointSizeF(20)
        f.setWeight(QFont.DemiBold)
        code.setFont(f)
        lay.addWidget(code)
        addrs = local_addresses()
        lay.addWidget(label(f"PC address: {', '.join(f'{a}:{server.port}' for a in addrs) or 'unknown'}"
                            f"  ·  certificate {server.identity.fingerprint[:4].upper()}…"
                            f"{server.identity.fingerprint[-4:].upper()}", "hint", wrap=True))
        self.countdown = label("", "hint")
        lay.addWidget(self.countdown)
        close = button("Cancel")
        close.clicked.connect(self.reject)
        lay.addLayout(hbox(None, close))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

    def _tick(self):
        if self.session.used:
            self.accept()
            return
        left = int(self.session.expires - time.time())
        if not self.session.active:
            self.countdown.setText("This code has expired or was entered wrong too often. Close and try again.")
            self.timer.stop()
            return
        self.countdown.setText(f"Code expires in {left // 60}:{left % 60:02d}")

    def done(self, r):
        self.timer.stop()
        if self.server.pairing is self.session:
            self.server.pairing = None  # a closed dialog shouldn't leave a code usable
        super().done(r)


class DevicesPage(Page):
    title = "Devices"
    subtitle = "Let the Music Tools phone app sync with this PC over Wi-Fi: new FLACs from the phone are " \
               "added to the library, and the Opus versions go back to the phone."
    icon = "wifi"

    def __init__(self, win):
        super().__init__(win)
        self.bridge = _Bridge()
        self.bridge.logged.connect(lambda msg, level: self.log.append(f"{time.strftime('%H:%M')}  {msg}", level))
        self.bridge.changed.connect(self._changed)
        self.server = SyncServer(SETTINGS, log=lambda msg, level="info": self.bridge.logged.emit(msg, level),
                                 on_change=self.bridge.changed.emit)

        card = Card("Sync server", icon="wifi")
        self.enabled = Toggle("Let devices sync with this PC", SETTINGS["net_enabled"])
        self.enabled.toggled.connect(self._toggle_server)
        self.status = label("", "muted", wrap=True)
        card.add(hbox(self.enabled, None))
        card.add(self.status)
        self.secure = Toggle("Require pairing (recommended)", SETTINGS["net_require_pairing"])
        self.secure.toggled.connect(self._toggle_secure)
        card.add(self.secure)
        card.add(label("With pairing, only devices you approved with a code can see or add music. Turn it off "
                       "to let any device on your home network sync without a code; connections are still "
                       "encrypted and outside networks are always refused.", "hint", wrap=True))
        self.usb = Toggle("Also sync over a USB cable (needs USB debugging on the phone)", SETTINGS["net_usb"])
        self.usb.toggled.connect(self._toggle_usb)
        card.add(self.usb)
        self.usb_status = label("", "hint")
        card.add(self.usb_status)
        if sys.platform.startswith("linux"):
            card.add(label(f"Phone can't find the PC? Your firewall may block it: "
                           f"sudo firewall-cmd --add-port={SETTINGS['net_port']}/tcp --add-service=mdns --permanent "
                           f"&& sudo firewall-cmd --reload", "hint", wrap=True))
        self.add(card)

        devs = Card("Paired devices", icon="phone")
        self.dev_table = _table(["Device", "Type", "Last seen", "Address"], [260, 90, 140])
        self.dev_table.setMinimumHeight(150)
        devs.add(self.dev_table)
        self.pair_btn = button("Pair a device…", "primary", "plus")
        self.pair_btn.clicked.connect(self.pair)
        remove = button("Remove", "danger", "trash", "Unpair: the device has to pair again to sync")
        remove.clicked.connect(self.remove)
        devs.add(hbox(self.pair_btn, remove, None))
        self.add(devs)

        inbox = Card("Arrived from devices", icon="down")
        self.inbox_table = _table(["File", "Status", "In library as"], [280, 150])
        self.inbox_table.setMinimumHeight(150)
        inbox.add(self.inbox_table)
        inbox.add(label("Files stay here until the phone has picked up the Opus version.", "hint"))
        self.add(inbox)

        self.log = LogPanel(self.jobs, 120)
        self.log.view.setMaximumHeight(180)
        self.add(self.log)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start(3000)
        self.usb_timer = QTimer(self)
        self.usb_timer.timeout.connect(self._check_usb)
        self.usb_timer.start(5000)
        self._usb_checking = False
        if SETTINGS["net_enabled"]:
            QTimer.singleShot(0, lambda: self._toggle_server(True))
        self._refresh()

    def on_show(self):
        self._refresh()

    def shutdown(self):
        if self.server.running:
            self.server.stop()
            if SETTINGS["net_usb"]:
                usb.unlink_usb_phones(self.server.port)

    # --- server

    def _toggle_server(self, on):
        SETTINGS["net_enabled"] = on
        SETTINGS.save()
        try:
            if on and not self.server.running:
                self.server.start()
            elif not on and self.server.running:
                self.server.stop()
        except OSError as e:
            self.log.append(f"Couldn't start the sync server: {e}", "error")
            self.enabled.blockSignals(True)
            self.enabled.setChecked(False)
            self.enabled.blockSignals(False)
            SETTINGS["net_enabled"] = False
        self._refresh()

    def _toggle_usb(self, on):
        SETTINGS["net_usb"] = on
        SETTINGS.save()
        if not on and self.server.running:
            port = self.server.port
            self.bg.run(lambda: usb.unlink_usb_phones(port))
        self._check_usb()

    def _check_usb(self):
        """Keep the cable link up for phones plugged in by USB (they may come and go)."""
        if not (self.server.running and SETTINGS["net_usb"]):
            self.usb_status.setText("")
            return
        if self._usb_checking:
            return
        self._usb_checking = True
        port = self.server.port

        def done(res):
            self._usb_checking = False
            phones = res if isinstance(res, list) else []
            self.usb_status.setText(f"Linked by USB: {', '.join(phones)}" if phones else
                                    "No phone plugged in by USB.")
        self.bg.run(lambda: usb.link_usb_phones(port), done)

    def _toggle_secure(self, on):
        if not on and QMessageBox.warning(
                self, "Turn off pairing?", "Any device on your home network will be able to download your "
                "Opus library and send music to it, without a code.\n\nOnly do this on a network you trust.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            self.secure.blockSignals(True)
            self.secure.setChecked(True)
            self.secure.blockSignals(False)
            return
        SETTINGS["net_require_pairing"] = on
        SETTINGS.save()
        if self.server.running:  # the announced mode changes; restart the announcement
            self.server.stop()
            self.server.start()
        self._refresh()
        self._check_usb()

    def pair(self):
        if not self.server.running:
            self.enabled.setChecked(True)
            if not self.server.running:
                return
        PairDialog(self, self.server).exec()
        self._refresh()

    def remove(self):
        row = self.dev_table.currentRow()
        if row < 0:
            return
        dev_id = self.dev_table.item(row, 0).data(Qt.UserRole)
        name = self.dev_table.item(row, 0).text()
        if QMessageBox.question(self, "Remove device", f"Unpair “{name}”? It will have to pair again to sync.") \
                == QMessageBox.Yes:
            self.server.registry.remove(dev_id)
            self.log.append(f"Removed {name}.", "warn")
            self._refresh()

    def _changed(self, what):
        if what == "inbox":
            self.store.reload()  # new files in the library
        self._refresh()

    def _refresh(self):
        s = self.server
        if s.running:
            addrs = ", ".join(f"{a}:{s.port}" for a in local_addresses()) or f"port {s.port}"
            mode = "pairing required" if s.require_pairing else "open to the home network"
            self.status.setText(f"Running on {addrs} as “{s.identity.name}” ({mode}).")
        else:
            self.status.setText("Off. Turn it on to sync with the phone app.")

        devices = sorted(s.registry.devices.values(), key=lambda d: d.name.lower())
        self.dev_table.setRowCount(len(devices))
        for r, d in enumerate(devices):
            name = QTableWidgetItem(d.name)
            name.setData(Qt.UserRole, d.id)
            cells = [name, QTableWidgetItem("Phone" if d.kind == "phone" else "PC"),
                     QTableWidgetItem(_ago(d.last_seen)),
                     QTableWidgetItem("USB" if d.last_ip in ("127.0.0.1", "::1") else d.last_ip)]
            for c, item in enumerate(cells):
                self.dev_table.setItem(r, c, item)

        items = sorted(s.inbox.list(), key=lambda i: -i.created) if s.inbox else []
        names = {d.id: d.name for d in devices}
        self.inbox_table.setRowCount(len(items))
        for r, i in enumerate(items):
            f = QTableWidgetItem(i.name)
            f.setToolTip(f"From {names.get(i.device, 'unpaired device')}")
            st = QTableWidgetItem(STATE_TEXT.get(i.state, i.state))
            st.setForeground(QColor(STATE_COLORS.get(i.state, theme.TEXT)))
            if i.detail:
                st.setToolTip(i.detail)
            for c, item in enumerate((f, st, QTableWidgetItem(i.library_path))):
                self.inbox_table.setItem(r, c, item)
