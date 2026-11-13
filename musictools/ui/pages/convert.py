from PySide6.QtWidgets import QMessageBox, QSpinBox, QComboBox

from ...core import convert
from ...core.util import SETTINGS
from ..main_window import Page
from ..widgets import Card, FormGrid, LogPanel, Toggle, button, hbox, label


class ConvertPage(Page):
    title = "Convert"
    subtitle = "Turn the FLAC library into a compact Opus copy for the phone. Already converted files are skipped."
    icon = "convert"
    follower_note = "The main PC converts; the Opus files arrive with the next sync."

    def __init__(self, win):
        super().__init__(win)
        card = Card("Encoder", icon="convert")
        form = FormGrid()
        self.paths = label("", wrap=True)
        form.add("Folders", self.paths, "Change these on the Settings page.")
        self.bitrate = QComboBox()
        self.bitrate.setEditable(True)
        for b in (64, 96, 128, 160, 192, 256):
            self.bitrate.addItem(f"{b} kbps", b)
        self.bitrate.setCurrentText(f"{SETTINGS['bitrate']} kbps")
        self.bitrate.setFixedWidth(140)
        self.bitrate.currentTextChanged.connect(self._save)
        form.add("Bitrate", self.bitrate, "128 kbps Opus is transparent for most people; 96 saves space.")
        self.jobs_spin = QSpinBox()
        self.jobs_spin.setRange(1, 128)
        self.jobs_spin.setValue(SETTINGS["jobs"])
        self.jobs_spin.setFixedWidth(140)
        self.jobs_spin.valueChanged.connect(self._save)
        form.add("Parallel jobs", self.jobs_spin)
        self.encoder = label("")
        form.add("Encoder", self.encoder)
        self.dry = Toggle("Dry run — only show what would happen")
        form.add("", self.dry)
        card.add(form)

        self.go = button("Convert", "primary", "play")
        self.go.clicked.connect(self.run)
        self.prune = button("Find orphaned files", icon="trash",
                            tip="Files in the export whose song was deleted or renamed, or that are converted now")
        self.prune.clicked.connect(self.find_orphans)
        self.lock(self.go, self.prune)
        card.add(hbox(self.go, self.prune, None))
        self.add(card)

        self.log = LogPanel(self.jobs, 260)
        self.add(self.log, 1)
        self.on_show()

    def on_show(self):
        self.paths.setText(f"{SETTINGS['library']}  →  {SETTINGS['opus']}")
        kind, exe = convert.encoder()
        if kind == "opusenc":
            self.encoder.setText(f"<span style='color:#34d399'>●</span>&nbsp; opusenc &nbsp;<span style='color:#8a91a3'>{exe}</span>")
        elif kind == "ffmpeg":
            self.encoder.setText(f"<span style='color:#fbbf24'>●</span>&nbsp; ffmpeg &nbsp;<span style='color:#8a91a3'>"
                                 f"(opusenc not found — works fine, opus-tools is slightly better)</span>")
        else:
            self.encoder.setText("<span style='color:#f87171'>●</span>&nbsp; No encoder found — install opus-tools or ffmpeg")

    def _save(self):
        try:
            SETTINGS["bitrate"] = int(self.bitrate.currentText().split()[0])
        except (ValueError, IndexError):
            pass
        SETTINGS["jobs"] = self.jobs_spin.value()
        SETTINGS.save()

    def run(self):
        self._save()
        self.jobs.start("Converting", convert.convert, SETTINGS.library, SETTINGS.opus, SETTINGS["bitrate"],
                        SETTINGS["jobs"], self.dry.isChecked(), log=self.log, on_done=lambda _: self.store.reload())

    def find_orphans(self):
        def done(orphans):
            if not orphans or self.dry.isChecked():
                return
            if QMessageBox.question(self, "Delete orphans", f"Delete these {len(orphans)} file(s) from the export?") \
                    == QMessageBox.Yes:
                def delete(ctx):
                    for o in orphans:
                        o.unlink(missing_ok=True)
                    ctx.log(f"Deleted {len(orphans)} file(s).", "ok")
                self.jobs.start("Deleting orphans", delete, log=self.log)

        self.jobs.start("Finding orphans", convert.find_orphans, SETTINGS.library, SETTINGS.opus, log=self.log,
                        on_done=done)
