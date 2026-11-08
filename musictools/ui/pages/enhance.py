from ...core import art, loudness
from ...core.util import SETTINGS
from ..main_window import Page
from ..widgets import Card, LogPanel, Toggle, button, hbox, label


class EnhancePage(Page):
    title = "Art & loudness"
    subtitle = "Fill in missing cover art and even out volume between tracks."
    icon = "image"

    def __init__(self, win):
        super().__init__(win)
        art_card = Card("Cover art", "Missing covers are looked up on MusicBrainz / Cover Art Archive by album, "
                        "then by song. One lookup per album.", icon="image")
        self.art_status = label("", "muted")
        art_card.add(self.art_status)
        fetch = button("Fetch missing covers", "primary", "search")
        fetch.clicked.connect(self.fetch)
        copy = button("Copy covers into Opus files", icon="image",
                      tip="Embeds each FLAC's cover into its Opus copy when the Opus file has none")
        copy.clicked.connect(self.copy)
        self.lock(fetch, copy)
        art_card.add(hbox(fetch, copy, None))

        loud_card = Card("Loudness normalization",
                         "Measures each track (EBU R128) and writes ReplayGain tags to the FLACs and R128 gain to the "
                         "Opus files. Players that support it (Poweramp, Musicolet, foobar2000, VLC…) then play "
                         "everything at the same volume. The audio itself is not changed.", icon="volume")
        self.loud_status = label("", "muted")
        loud_card.add(self.loud_status)
        self.force = Toggle("Re-measure everything (ignore existing ReplayGain tags)")
        loud_card.add(self.force)
        analyze = self.lock(button("Analyze && tag", "primary", "volume"))
        analyze.clicked.connect(self.analyze)
        loud_card.add(hbox(analyze, None))

        self.add(hbox(art_card, loud_card, spacing=14))
        self.log = LogPanel(self.jobs, 300)
        self.add(self.log, 1)
        self.store.changed.connect(self._update_status)
        self.store.updated.connect(lambda _: self._update_status())

    def on_show(self):
        self._update_status()

    def _update_status(self):
        tracks = self.store.tracks
        if not self.store.loaded_once:
            self.art_status.setText("Loading library…")
            return
        no_art = sum(1 for t in tracks if not t.has_art)
        self.art_status.setText(f"{no_art} of {len(tracks)} tracks have no cover art." if no_art
                                else f"All {len(tracks)} tracks have cover art. ✓")
        flac = [t for t in tracks if t.codec == "flac"]
        no_rg = sum(1 for t in flac if t.rg_gain is None)
        self.loud_status.setText(f"{no_rg} of {len(flac)} FLACs have no ReplayGain yet." if no_rg
                                 else f"All {len(flac)} FLACs have ReplayGain. Opus files are checked when you run it.")

    def fetch(self):
        tracks = self.store.tracks
        self.jobs.start("Fetching covers", art.fetch_missing, tracks, SETTINGS.library, SETTINGS.opus,
                        log=self.log, on_done=lambda _: self.store.reload())

    def copy(self):
        self.jobs.start("Copying covers", art.copy_to_opus, self.store.tracks, SETTINGS.library, SETTINGS.opus,
                        log=self.log)

    def analyze(self):
        self.jobs.start("Loudness", loudness.tag_library, self.store.tracks, SETTINGS.library, SETTINGS.opus,
                        self.force.isChecked(), SETTINGS["jobs"], log=self.log, on_done=lambda _: self._update_status())
