import os
import time

from PySide6.QtWidgets import QCheckBox, QGridLayout, QWidget

from ...core import convert, pipeline, playlists, sync
from ...core.util import SETTINGS, human_size
from ..main_window import Page
from ..widgets import Card, LogPanel, StatCard, Toggle, button, hbox, label, vbox


def _folder_size(folder):
    total = 0
    for dirpath, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Your library at a glance, and one button to bring the phone up to date."
    icon = "dashboard"

    def __init__(self, win):
        super().__init__(win)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.stats = {
            "flac": StatCard("music", "FLAC tracks"),
            "lib_size": StatCard("disk", "Lossless library"),
            "opus_size": StatCard("phone", "Opus export"),
            "pending": StatCard("convert", "Waiting to convert"),
            "orphans": StatCard("alert", "Orphaned Opus files"),
            "playlists": StatCard("playlist", "Playlists"),
        }
        for i, card in enumerate(self.stats.values()):
            grid.addWidget(card, i // 3, i % 3)
        self.add(grid)

        # phone + watcher
        self.phone = Card("Phone", icon="phone")
        self.phone_dot = label("●", "dot")
        self.phone_name = label("Checking…")
        self.phone_info = label("", "muted")
        self.last_sync = label("", "muted")
        refresh = button("Refresh", "ghost", "refresh")
        refresh.clicked.connect(self.refresh_phone)
        goto = button("Open Sync", icon="phone")
        goto.clicked.connect(self._open_sync)
        self.phone.head.addWidget(refresh)
        self.phone.add(hbox(self.phone_dot, self.phone_name, None, spacing=8))
        self.phone.add(self.phone_info)
        self.phone.add(self.last_sync)
        self.phone.add(hbox(goto, None))

        watch = Card("Watch folder", "Automatically convert new FLACs that appear in the library folder.",
                     icon="eye")
        self.watch_toggle = Toggle("Watch the library", SETTINGS["watch"])
        self.watch_toggle.toggled.connect(self.win.set_watch)
        self.watch_sync = Toggle("Also sync to the phone when it's connected", SETTINGS["watch_sync"])
        self.watch_sync.toggled.connect(lambda v: (SETTINGS.__setitem__("watch_sync", v), SETTINGS.save()))
        self.watch_label = label("Off", "muted", wrap=True)
        watch.add(self.watch_toggle)
        watch.add(self.watch_sync)
        watch.add(self.watch_label)
        self.add(hbox(self.phone, watch, spacing=14))

        # pipeline
        card = Card("One-click pipeline", "Runs the selected steps in order. Only new or changed files are touched.",
                    icon="sparkle")
        steps = QWidget()
        sg = QGridLayout(steps)
        sg.setContentsMargins(0, 4, 0, 4)
        sg.setHorizontalSpacing(28)
        sg.setVerticalSpacing(10)
        self.step_boxes = {}
        for i, (key, text) in enumerate(pipeline.STEPS):
            cb = QCheckBox(text)
            cb.setChecked(key in SETTINGS["pipeline_steps"])
            cb.toggled.connect(self._save_steps)
            sg.addWidget(cb, i // 3, i % 3)
            self.step_boxes[key] = cb
        card.add(steps)
        self.run_btn = self.lock(button("Run pipeline", "big", "play"))
        self.run_btn.clicked.connect(self.run_pipeline)
        card.add(hbox(self.run_btn, None))
        self.log = LogPanel(self.jobs, 200)
        card.add(self.log)
        self.add(card)
        self.body.addStretch(1)

        self.refresh()
        self.refresh_phone()

    def on_show(self):
        self.refresh()

    def _open_sync(self):
        from .sync import SyncPage
        self.win.go(SyncPage)

    def _save_steps(self):
        SETTINGS["pipeline_steps"] = [k for k, cb in self.step_boxes.items() if cb.isChecked()]
        SETTINGS.save()

    def update_watch(self, text):
        self.watch_label.setText(text)

    def refresh(self):
        lib, opus = SETTINGS.library, SETTINGS.opus

        def work():
            st = convert.status(lib, opus) if lib.is_dir() else {"flac": 0, "pending": [], "orphans": []}
            return {**st, "lib_size": _folder_size(lib), "opus_size": _folder_size(opus),
                    "playlists": len(playlists.list_playlists(lib))}

        self.bg.run(work, self._show_stats)

    def _show_stats(self, st):
        if isinstance(st, Exception):
            return
        self.stats["flac"].set(f"{st['flac']:,}")
        self.stats["lib_size"].set(human_size(st["lib_size"]))
        self.stats["opus_size"].set(human_size(st["opus_size"]))
        self.stats["pending"].set(len(st["pending"]))
        self.stats["orphans"].set(len(st["orphans"]))
        self.stats["playlists"].set(st["playlists"])

    def refresh_phone(self):
        self.phone_name.setText("Checking…")

        def work():
            if not sync.adb():
                return None
            devs = [d for d in sync.devices() if d.ready]
            if not devs:
                return []
            return [(devs[0], sync.storage(devs[0].serial))]

        self.bg.run(work, self._show_phone)

    def _show_phone(self, res):
        last = SETTINGS["last_sync"]
        self.last_sync.setText(f"Last sync: {time.strftime('%d %b %Y, %H:%M', time.localtime(last))}"
                               if last else "Never synced from this computer")
        if res is None or isinstance(res, Exception):
            state, name, info = "warn", "adb not found", "Install Android platform-tools, or set adb in Settings."
        elif not res:
            state, name, info = "off", "No phone connected", "Plug in with USB debugging on, or pair over Wi-Fi."
        else:
            dev, space = res[0]
            state, name = "ok", dev.label
            info = f"{space[0]} free of {space[1]}" if space else dev.serial
        self.phone_dot.setProperty("state", state)
        self.phone_dot.style().unpolish(self.phone_dot)
        self.phone_dot.style().polish(self.phone_dot)
        self.phone_name.setText(name)
        self.phone_info.setText(info)

    def run_pipeline(self):
        steps = [k for k, cb in self.step_boxes.items() if cb.isChecked()]
        if not steps:
            return

        def done(_):
            self.refresh()
            self.refresh_phone()
            self.store.reload()

        self.jobs.start("Pipeline", pipeline.run, SETTINGS, steps, log=self.log, on_done=done)
