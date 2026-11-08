# Music Tools

Keep a lossless FLAC library on your PC and an Opus copy of it on your Android phone, in sync both ways.

- **Desktop app** (Windows, Linux): convert, tag, check and organize the FLAC library, and serve it to your phone.
- **Phone app** (Android): mirrors the library as Opus over Wi-Fi or a USB cable, sends FLACs you got on the go
  back to the PC, and lets you manage playlists, choose what to sync and run the PC's tools from the phone.

The PC holds the master copy. A FLAC that lands on your phone goes to the PC, gets filed into the library and
converted, and comes back as Opus; the phone's FLAC is removed once the Opus has arrived.

## Download

Get the latest build from [Releases](https://github.com/tianxibang/Music-tools/releases):

- **Windows**: unzip `MusicTools-windows-x64.zip` and run `MusicTools.exe`.
- **Linux**: `tar xzf MusicTools-linux-x64.tar.gz`, then run `MusicTools/install-linux.sh` to add it to your
  app menu (or just start `MusicTools/MusicTools`).
- **Android**: install `MusicTools-android.apk` (allow installing from your browser or files app when asked).

The desktop app needs ffmpeg and opusenc; see [External programs](#external-programs).

## Getting started

1. Open the desktop app, set your FLAC library and Opus export folders in **Settings**.
2. Go to **Phone**, choose **Music Tools phone app**, and turn on **Turn on sync with the phone app**.
3. Click **Pair a phone…** and scan the QR code with the phone app (or tap the PC in its list and type the code).

That's it: the phone syncs about every 30 minutes on Wi-Fi, or whenever you tap **Sync now**.

## Desktop app

| Page | What it does |
|---|---|
| Dashboard | Library stats, phone status, watch folder, **one-click pipeline** (convert → cover art → loudness → playlists → sync) |
| Convert | FLAC → Opus in parallel, skips finished files, finds orphaned Opus files |
| Phone | How the phone syncs: with the **phone app** (two-way, background, Wi-Fi or USB), or **without the app** (one-way copy over adb) |
| Tag editor | Searchable table, bulk-edit tags and covers, rename files from tags, tags from file names. Edits carry over to the Opus copies |
| Health check | Missing tags or art, duplicates (review them and move extras to the trash), corrupt files, FLACs made from MP3s (spectrum check) |
| Art & loudness | Fetch missing covers (MusicBrainz), copy covers into Opus, ReplayGain/R128 loudness tags |
| Playlists | `.m3u8` playlists stored in `<library>/Playlists`, exported with Opus paths so they sync to the phone |
| Organize | Folder info (size, play time, artists, genres, decades, quality), tag-based sorter, flatten |

## Phone app

| Tab | What it does |
|---|---|
| Sync | Status, **Sync now**, activity log, and files that couldn't sync (retried automatically) |
| Library | Stats for the phone and the PC (songs, size, play time, free space, top artists, genres, decades, source quality). **Choose what to sync**: everything, or only the artists, albums and playlists you pick |
| Playlists | Create, rename, reorder and delete playlists. They're saved on the PC like desktop playlists and reach every device |
| PC | Run the PC's one-click pipeline from the phone and follow its log |
| Settings | Folders, automatic sync, Wi-Fi only, replace FLACs with Opus, keep extra files, unpair |

What a sync does:

1. Sends new FLACs from the phone's `Download` and `Music` folders to the PC, which checks them, skips songs it
   already has (by audio fingerprint, whatever the tags), files the rest with your sort template and converts them.
2. Downloads the Opus versions, then deletes the phone's FLACs, but only if unchanged since they were sent
   (can be turned off).
3. Mirrors the PC's Opus export into `Music/opus_export`, the same folder the adb sync uses. Only Opus and
   playlist files there are ever deleted; anything else you put in that folder is left alone.

The app asks for **All files access**: it manages a music folder shared with other apps and removes FLACs that
other apps saved.

### Wi-Fi or USB

The phone finds the PC on your network automatically. With **USB debugging** on, it also syncs over a cable,
with no network needed: the desktop app links phones plugged in by USB (`adb reverse`), and the app tries the
cable first. Automatic syncs need a network, so with only a cable, tap **Sync now**.

### Security

- **Pairing (default):** a phone pairs once with a one-time code that expires after 5 minutes and locks after 5 wrong
  tries. Traffic is encrypted (TLS with a certificate made on first start), and the phone pins that certificate,
  so nothing else can pretend to be your PC, even when the code is typed rather than scanned.
- **Open mode:** turn off **Require pairing** to skip codes. Any device on your home network can then sync; traffic is
  still encrypted.
- Connections from outside your local network are always refused. Paths from the phone are checked: downloads
  stay inside the Opus export, uploads only reach the library through the PC's inbox (FLAC only, checksum
  verified, filed by the PC itself), and playlist edits stay in the Playlists folder.
- Each phone has its own key, stored on the PC only as a hash. **Remove** on the Phone page cuts it off at once.

### Without the app

Choose **Without the app (adb)** on the Phone page to copy the Opus library to the phone with Android's developer
tools instead: one-way, over USB or Wireless debugging, started from the PC, with a preview before anything changes.

### Headless server

For an always-on PC: `music-tools --serve` runs only the sync server (add `--pair` to print a pairing code).

Phone can't find the PC on Linux? Open the firewall:
`sudo firewall-cmd --add-port=47321/tcp --add-service=mdns --permanent && sudo firewall-cmd --reload`

## External programs

Found automatically next to the app, in a `bin/` folder beside it, or on PATH (or set them in Settings):

- **opusenc** (opus-tools): the preferred Opus encoder
- **ffmpeg**: loudness, health checks, and conversion if opusenc is missing
- **adb** (Android platform-tools): only for syncing over USB, or without the phone app

Fedora: `sudo dnf install opus-tools ffmpeg android-tools` ·
Windows: `winget install Gyan.FFmpeg Google.PlatformTools`, or drop the `.exe`s into `bin/` before running `build.py` to bundle them.

## Development

### Desktop (Python, PySide6)

```bash
uv sync                                   # or: python -m venv .venv && .venv/bin/pip install -e .
uv run python launcher.py                 # run from source
./install-linux.sh                        # optional: add it to the app menu
uv run --group dev pytest -q tests        # tests (sync server, pairing, security checks, inbox, playlists, …)
uv run --group build python build.py      # standalone app in dist/MusicTools/ (add --onefile for one file)
```

PyInstaller only builds for the OS it runs on; GitHub builds Windows and Linux for you (see below).

### Phone (android/, Kotlin + Jetpack Compose)

```bash
cd android
./gradlew assembleDebug                   # app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest               # unit tests
../tests/android_interop.sh               # the app's network code against a real desktop server
```

Needs JDK 17+ and the Android SDK (`ANDROID_HOME`, or `sdk.dir` in `android/local.properties`). The sync protocol
lives in `musictools/core/netsync/` and `android/.../net/`; the tests check that both sides agree.

### Releases

Pushing a version tag builds Windows, Linux and Android, runs the tests, and publishes a GitHub release:

```bash
git tag v3.1 && git push origin v3.1
```

The Android version code follows from the tag (3.1.2 → 30102), so each release installs over the previous one.
Release APKs are signed with the key in `android/keystore.properties` locally, or the `ANDROID_KEYSTORE_BASE64`
and `ANDROID_KEYSTORE_PASSWORD` repository secrets on GitHub (neither is committed; keep a backup of the key,
or phones can't update the app without reinstalling). Running the workflow by hand from the Actions tab builds
everything as downloadable artifacts without making a release.
