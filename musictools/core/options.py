"""Every option the Settings page shows: where it goes, what it says, and which values it takes.

The defaults live in util.DEFAULTS; this adds what the page needs to show and check them. Settings that the
app keeps for itself (the last sync, the paired phone, a skipped update…) aren't listed.
"""

from dataclasses import dataclass, field

SECTIONS = [
    ("general", "Folders & performance", "folder"),
    ("convert", "Conversion", "convert"),
    ("watch", "Automation", "clock"),
    ("phone", "Phone (USB / Wi-Fi debugging)", "phone"),
    ("follow", "Other computers", "wifi"),
    ("import", "Import", "usb"),
    ("naming", "Tags, names & playlists", "tag"),
    ("health", "Health check", "health"),
    ("enhance", "Cover art & loudness", "image"),
    ("lyrics", "Lyrics", "mic"),
    ("updates", "Updates", "refresh"),
    ("tools", "External tools", "settings"),
]


@dataclass
class Option:
    key: str
    section: str
    label: str
    help: str
    kind: str                 # folder | file | bool | int | float | choice | multi | text
    choices: list = field(default_factory=list)  # (value, label) for choice / multi
    min: float = None
    max: float = None
    step: float = 1
    unit: str = ""

    def check(self, value):
        """The value made valid for this option (clamped, of the right type), or ValueError."""
        if self.kind == "bool":
            return bool(value)
        if self.kind in ("int", "float"):
            v = int(round(float(value))) if self.kind == "int" else float(value)
            if self.min is not None:
                v = max(type(v)(self.min), v)
            if self.max is not None:
                v = min(type(v)(self.max), v)
            return v
        if self.kind == "choice":
            values = [c[0] for c in self.choices]
            if value not in values:
                raise ValueError(f"{self.label}: {value!r} isn't one of {values}")
            return value
        if self.kind == "multi":
            values = [c[0] for c in self.choices]
            return [v for v in values if v in set(value)]
        value = str(value).strip()
        if not value and self.kind != "file":  # a tool path may be empty: then it is found automatically
            raise ValueError(f"{self.label} can't be empty")
        return value


CODECS = [("mp3", "MP3"), ("aac", "AAC"), ("opus", "Opus"), ("vorbis", "Ogg Vorbis"), ("eac3", "E-AC-3 (Dolby Atmos)"),
          ("ac3", "AC-3"), ("wma", "WMA")]

OPTIONS = [
    # --- folders & performance
    Option("library", "general", "FLAC library", "Your lossless master copies.", "folder"),
    Option("opus", "general", "Opus export", "Generated copies for the phone. Mirrors the library's folder "
           "layout.", "folder"),
    Option("jobs", "general", "Parallel jobs", "How many files are converted or measured at once. More is "
           "faster until the processor is busy.", "int", min=1, max=64),

    # --- conversion
    Option("bitrate", "convert", "Opus bitrate", "Quality of the Opus copies. 128 kbps is transparent for most "
           "people; 96 saves space.", "int", min=32, max=512, unit="kbps"),
    Option("phone_codecs", "convert", "Formats the phone plays as they are",
           "Lossy files in these formats are copied to the export unchanged (unless they're large, see below). "
           "Everything else is converted to Opus. Lossless files are always converted.", "multi", CODECS),
    Option("keep_lossy_below", "convert", "Convert large lossy files",
           "A lossy file whose bitrate is more than this many times the Opus bitrate is converted too: the copy "
           "gets much smaller and little is lost. Below that it is copied (another lossy pass only costs "
           "quality).", "float", min=1.0, max=10.0, step=0.1, unit="× the Opus bitrate"),

    # --- automation
    Option("watch_interval", "watch", "Watch folder: look every",
           "How often the library is checked for new FLACs when the folder watch is on (Dashboard).", "int",
           min=5, max=3600, unit="seconds"),
    Option("watch_sync", "watch", "Watch folder: sync the phone too",
           "After converting new files, also sync the phone.", "bool"),

    # --- phone (adb)
    Option("remote", "phone", "Music folder on the phone", "Where the Opus export goes on the phone when "
           "syncing over USB or Wi-Fi debugging.", "text"),
    Option("sync_confirm", "phone", "Ask before syncing", "When to show what a sync will copy and delete "
           "first.", "choice", [("always", "Always"), ("deleting", "Only when files will be deleted"),
                                 ("never", "Never")]),
    Option("keep", "phone", "Keep extra files on the phone", "Don't delete songs from the phone that aren't in "
           "the export any more.", "bool"),
    Option("wifi_autoconnect", "phone", "Reconnect over Wi-Fi", "Connect to the last phone over Wi-Fi "
           "debugging when the Phone page opens.", "bool"),

    # --- other computers
    Option("net_port", "follow", "Sync server port", "The port the phone app and other computers connect to. "
           "Restart the sync server after changing it.", "int", min=1024, max=65535),
    Option("follow_get", "follow", "A follower gets", "What a computer that follows this one downloads.",
           "choice", [("flac", "The FLAC library"), ("opus", "The Opus versions"), ("both", "Both")]),
    Option("follow_auto", "follow", "Follower: sync automatically", "Check the main PC for changes regularly.",
           "bool"),
    Option("follow_interval", "follow", "Follower: check every", "How often a follower looks for changes on the "
           "main PC.", "int", min=5, max=1440, unit="minutes"),
    Option("follow_send_auto", "follow", "Follower: send changes automatically", "Send tag and lyrics edits "
           "made on a follower to the main PC without asking.", "bool"),
    Option("follow_new_music", "follow", "Follower: new music folder", "Where a follower puts songs to send to "
           "the main PC.", "folder"),
    Option("follow_big_delete", "follow", "Follower: ask before removing more than",
           "A sync that would remove more files than this (and over 10 % of them) asks first.", "int",
           min=1, max=100000, unit="files"),

    # --- import
    Option("import_check_fakes", "import", "Look for fake lossless files", "Check FLACs being imported for "
           "the cut-off highs of a lossy source (slower).", "bool"),
    Option("lossy_folder", "import", "Folder for lossy imports", "Subfolder of the library that MP3/AAC "
           "imports go into.", "text"),

    # --- naming
    Option("rename_template", "naming", "File name template", "Used by Tags → Rename. Fields: {artist} {title} "
           "{album} {albumartist} {track} {disc} {year} {genre}, e.g. {track:02}.", "text"),
    Option("sort_template", "naming", "Folder layout template", "Used by Organize → Sort into folders.", "text"),
    Option("playlists_folder", "naming", "Playlists folder", "Subfolder of the library (and the export) that "
           "holds playlists.", "text"),

    # --- health check
    Option("health_min_rate", "health", "Low quality: lossless below", "Lossless files with a lower sample "
           "rate are listed as low quality.", "int", min=8000, max=192000, step=100, unit="Hz"),
    Option("health_min_bitrate", "health", "Low quality: lossy below", "Lossy files with a lower bitrate are "
           "listed as low quality.", "int", min=32, max=512, unit="kbps"),
    Option("lossy_cliff_db", "health", "Lossy-source sensitivity", "How sharp the drop in the highs must be "
           "for a FLAC to count as made from an MP3/AAC. Lower finds more, with more false alarms.", "int",
           min=10, max=60, unit="dB"),
    Option("version_words", "health", "Words that mark another version",
           "A title with one of these (demo, live, …) is another version of a song, not a duplicate of it. "
           "Separate them with commas.", "text"),

    # --- cover art & loudness
    Option("cover_size", "enhance", "Downloaded cover size", "Size of covers fetched from the Cover Art Archive.",
           "choice", [(250, "250 px"), (500, "500 px"), (1200, "1200 px")]),
    Option("loudness_target", "enhance", "Loudness target", "The loudness ReplayGain and R128 tags aim for. "
           "-18 LUFS is the ReplayGain 2 standard. Songs tagged earlier keep their tags until you redo them "
           "with “Redo all” in Enhance.", "float", min=-30, max=-5, step=0.5, unit="LUFS"),

    # --- lyrics
    Option("lyrics_save", "lyrics", "Save lyrics", "Where edited and downloaded lyrics are saved.", "choice",
           [("embed", "In the music file"), ("file", "In a .lrc file next to it"), ("both", "Both")]),
    Option("lyrics_tap_offset", "lyrics", "Tap delay", "Taken off every tap while syncing, to make up for "
           "reaction time.", "float", min=0, max=1, step=0.01, unit="seconds"),
    Option("lrclib_gap", "lyrics", "Pause between LRCLIB requests", "LRCLIB is a free service: at least one "
           "second between requests.", "float", min=1, max=10, step=0.5, unit="seconds"),
    Option("lyrics_recheck_days", "lyrics", "Look again for songs not found after", "Songs LRCLIB had no sure "
           "match for aren't looked up again sooner than this.", "int", min=0, max=365, unit="days"),
    Option("timeline_snap", "lyrics", "Snap to the playhead within", "How close an edge has to be dragged to "
           "the playhead to snap to it.", "int", min=0, max=30, unit="px"),

    # --- updates
    Option("update_check", "updates", "Check for updates at startup", "Look for a new version on GitHub when "
           "Music Tools starts. You're always asked before anything is installed.", "bool"),

    # --- external tools
    Option("tool_opusenc", "tools", "opusenc", "opus-tools: the best Opus encoder (optional if ffmpeg is "
           "installed). Leave empty to find it automatically.", "file"),
    Option("tool_ffmpeg", "tools", "ffmpeg", "Conversion of other formats, loudness, health checks. Leave "
           "empty to find it automatically.", "file"),
    Option("tool_adb", "tools", "adb", "Android platform-tools: phone sync over USB / Wi-Fi debugging. Leave "
           "empty to find it automatically.", "file"),
]

BY_KEY = {o.key: o for o in OPTIONS}
