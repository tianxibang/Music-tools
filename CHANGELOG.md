# Changelog

Downloads for every version are on the [Releases](https://github.com/tianxibang/Music-tools/releases) page.

## 4.2: Updates and installers (2026-11-13)

### Updates from inside the app
- **The desktop app tells you about new versions.** At startup it checks GitHub, shows what's new and asks:
  **Update now**, **Later** or **Skip this version**. It downloads the right file for how you installed Music Tools,
  checks it against the release's checksum, installs it and restarts. Turn the check off, or check by hand, in
  **Settings → Updates**.
- **The phone app does too**: when it opens (at most twice a day) or from **Settings → App → Check for updates**.
  Android asks you to confirm the update; the first time it also asks you to allow "Install unknown apps" for
  Music Tools.

### Installers
- **Windows setup** (`MusicTools-setup-windows-x64.exe`): installs for your user only, so it needs no admin rights,
  with a Start menu entry and an uninstaller. Updates run silently.
- **Linux packages**: `.rpm` for Fedora and openSUSE, `.deb` for Debian and Ubuntu. Music Tools goes to
  `/opt/music-tools`, with a menu entry and a `music-tools` command. Updates go through dnf or apt, which ask for
  your password.
- The portable `.zip` and `.tar.gz` stay, and update themselves in place.

### A Settings page with everything
- **Every option in one place**, in sections that follow the app, with a search box. Each option says what it
  does and what its default is, and has a reset button. Changes are saved right away, and pages with their own
  controls for an option show the change.
- **Options that used to be fixed:** which formats the phone plays unchanged, when large lossy files get
  converted, the folder names for lossy imports and playlists, how often the watch folder looks, when a follower
  asks before removing files, the Health check's limits (low quality, lossy-source sensitivity, the words that mark
  another version such as demo or live), the size of downloaded covers, the loudness target, the pause between
  LRCLIB requests (at least a second), how long before songs without a match are looked up again, and the timeline's
  snap distance.
- **Export, Import and Reset all**, for moving your settings to another computer. Pairings and the phone connection
  are never part of them.
- A value in the settings file that can't be used (edited by hand, say) falls back to its default.

### Fixes
- Switches sometimes showed "off" for a setting that was on, after a page reloaded its values.
- Release pages now get their notes from this changelog.

## 4.1.1: Dolby Atmos and other formats (2026-11-13)

### Convert decides by what's inside the file, not by its extension
- **More formats become Opus.** Until now only FLACs were converted; every other file in the library was copied
  to the phone as it was, including Dolby Atmos / E-AC-3 `.m4a` downloads that phones can't play. Now a file is
  converted when it is lossless (FLAC, ALAC, WAV, …), when phones play its codec badly (E-AC-3, AC-3, WMA, …),
  when it has more than two channels (downmixed to stereo), or when it is a lossy file far above the Opus bitrate
  (more than 1.5× it). Small MP3/AAC files, and Opus files, are still copied as they are.
- Cover art and embedded lyrics are carried over from those files too, and they get R128 loudness tags and
  cover art from the pipeline like FLACs do.
- A copy left behind by an earlier version (the `.m4a` next to its new `.opus`) is removed when the song is
  converted; **Find orphaned files** on the Convert page now lists every file in the export without a source,
  not only `.opus` files.
- Converting anything that isn't FLAC or WAV needs ffmpeg, and Dolby Atmos needs a full build of it (Fedora's
  `ffmpeg-free` can't decode E-AC-3; the message says so).
- Renaming, tag edits and playlist entries follow the same rule, so a converted `.m4a` keeps its `.opus` in step.

## 4.1: Lyrics editor (2026-11-11)

### New: the Lyrics page
- **Write lyrics** for any song, paste them, or find them online on [LRCLIB](https://lrclib.net) (often already
  synced).
- **Sync them to the song, line by line or word by word.** Play the song and press **Enter** as each line (or word)
  starts. The next line is lined up automatically, so you can tap through the whole song. Press **E** where a line
  ends before the next one, so it isn't highlighted during an instrumental part.
- **Slow playback**: 0.75×, 0.5× and 0.25× make tapping easier, and the pitch stays the same. A **tap delay** setting
  makes up for your reaction time.
- **A timeline with the song's waveform**: drag lines, words and ad-libs, or their edges, and hear the audio under
  the mouse while you drag. Shift+drag moves all later lines too, and edges snap to the playhead. Undo/redo, "Shift
  all…" and a karaoke-style preview are included.
- **Duets and ad-libs**: set which singer sings each line, and add background vocals or ad-libs with their own
  timing. The preview shows them like Apple Music does: singers left and right, ad-libs under their line.
- **Instrumental songs** can be marked as such (`[instrumental:true]`), so they don't count as missing lyrics.
- **Choose where lyrics are saved**: in the music file, in a `.lrc` file next to it, or both.
- **Lyrics for many songs at once**: *Find for many songs…* looks up every song in the list and saves the sure
  matches. Lyrics it replaces are backed up first. The new **Added online** tab lists what happened to each song,
  so you can check it, use a match that wasn't certain, or undo it. It asks LRCLIB gently (at most once a second),
  waits when asked to, and lists any song it couldn't look up.
- **Lyrics files…** moves lyrics out of the music files into `.lrc` files, or the other way round, for many songs at
  once.

### Lyrics everywhere
- Lyrics go into the Opus copies, embedded and as `.lrc` files, and move along when files are renamed or moved.
- The phone app syncs `.lrc` files together with their songs.
- Lyrics edited on a computer that follows the main PC are sent to it like tag edits.
- Existing lyrics keep every detail when you edit them: word timings, singers, ad-libs and extra tags.

### Fixes
- A follower computer now says when the main PC ignores a change, because it runs an older Music Tools. Before,
  sending just did nothing.
- Pairing a Windows PC no longer shows an address that can't be reached.
- A follower's sync log shows what it downloads and why.

## 4.0: Sync between computers (2026-11-09)

- **A second computer can follow the main PC** (*Sync → Follows another PC*). It pairs like the phone and keeps
  its own copy of the FLAC library, the Opus export or both, every 30 minutes or when you ask. It only removes files
  a sync put there, asks before big removals, and reuses copies that were already there.
- **Send changes back**: music dropped in a *New music* folder, and tag edits made on the follower, go to the main
  PC by hand or automatically. If a tag was changed on both computers, you choose which to keep.
- On a follower, tools that would change the library are read-only, with a note saying where to make the change.
- The main PC files all incoming music, from phones and computers, with the same rules as the drive import.
- The **Phone** page is now the **Sync** page, and *Pair a device…* pairs both phones and computers.

## 3.3: Import from a drive (2026-11-09)

- **New Import page**: scan a drive or folder and compare every song with your library:
  - New songs are added and filed with your sort template; WAV and AIFF become FLAC.
  - Better copies replace worse ones (e.g. a FLAC instead of your MP3), keeping your tags and cover. Optionally,
    "lossless" files are checked for being MP3s in disguise first.
  - New lossy songs (MP3, AAC, …) go into a separate `Lossy` folder.
  - Other versions (demo, live, …) are listed for you to decide.
  - **Import suggested** does it in one click, or use **Review…** to choose. The drive is only read, and every
    copy is verified.
- Lossy songs in the library are now copied to the phone as they are, instead of being ignored.

## 3.2: Better duplicate removal (2026-11-09)

- **Duplicates are told apart from other versions.** A demo, live, acoustic, remix or edit isn't a copy anymore.
  Before, the album version could be removed while the demo was kept.
- Each song is listed once with all its files, labelled *Best copy*, *Copy* or *Other version*. Only extra copies
  are ticked, and "possible" duplicates are listed separately with nothing ticked.
- Removing a duplicate also removes its Opus copy and takes it out of playlists, so it leaves the phone too.
- **Fixed:** removing duplicates crashed before deleting anything, without showing an error.
- **Fixed:** Japanese and other non-Latin titles were all treated as the same song.
- Licensed under the GNU GPL v3. README screenshots and a security policy were added.

## 3.1: Phone app polish (2026-11-08)

- Library stats split multi-genre tags and show the quality of the original lossless files.
- The PC tab's log reads top to bottom without blank lines.
- Small wording fixes ("1 song", "just now").
- Phone app builds of the same version (local or from GitHub) can now replace each other.

## 3.0: Phone app features (2026-11-08)

- **Choose what syncs to the phone**: pick artists, albums and playlists instead of everything.
- **Playlists on the phone**: create, rename, reorder, add and remove songs. They're saved on the PC as normal
  playlists.
- **Library stats** for the phone and the PC: top artists, genres, decades, quality.
- **PC tab**: run the PC's one-click pipeline from the phone and follow its log.
- **Fixed:** a failed upload (e.g. a Wi-Fi hiccup) was skipped forever and the app said everything was up to date.
  Failed files are now retried, and the Sync screen lists what couldn't sync and why.

## 2.3: One Phone page (2026-11-08)

- *Sync to phone* and *Devices* are merged into one **Phone** page. It starts with a choice: the Music Tools phone
  app (two-way, in the background, Wi-Fi or USB) or without the app (one-way over adb). Only that method's settings
  are shown, and the dashboard and the pipeline follow your choice.

## 2.2: The phone app (2026-11-08)

- **New Android app** that syncs with the PC over Wi-Fi:
  - Pair it by scanning a QR code or typing a code.
  - FLACs you get on the phone go to the PC, are converted, and come back as Opus. The phone's FLAC is removed only
    once the Opus has arrived.
  - The phone keeps a copy of the PC's Opus music, and syncs in the background every 30 minutes on Wi-Fi.
- **Sync over a USB cable** when there's no network.

## 2.1: Sync server (2026-11-08)

- New **Devices** page: the PC can serve its music to phones on the home network. Connections are encrypted and
  devices pair with a one-time code (or skip pairing on a trusted network).
- `--serve` runs the sync server without the window, e.g. on an always-on PC.

## 2.0: The desktop app (2026-11-08)

The first release of Music Tools as an app, replacing the old `flac2opus`, `sync-music` and `flattenplaylist`
scripts:

- **Dashboard**: library stats, phone status, a watch folder, and a **one-click pipeline** (convert → cover art →
  loudness → playlists → sync).
- **Convert**: FLAC to Opus in parallel, skipping finished files, and finding leftover Opus files.
- **Sync to phone**: copies the music over USB or Wi-Fi with adb, with a preview before anything changes.
- **Tag editor**: search, edit tags and covers in bulk, rename files from tags, and read tags from file names.
  Edits carry over to the Opus copies.
- **Health check**: missing tags or covers, duplicates (review them, then move the extras to the trash), broken
  files, and FLACs made from MP3s.
- **Art & loudness**: fetch missing covers, copy covers into the Opus files, add loudness (ReplayGain/R128) tags.
- **Playlists** that sync to the phone.
- **Organize**: folder info, a tag-based sorter, and flattening folders.
- Windows and Linux downloads are published on GitHub for every version.
