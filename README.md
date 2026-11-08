# Music Tools

A desktop app (Windows, Linux) for a FLAC library that gets synced to an Android phone as Opus.
It replaces `flac2opus.fish`, `sync-music.fish` and `flattenplaylist.fish`, and adds more:

| Page | What it does |
|---|---|
| Dashboard | Library stats, phone status, watch folder, **one-click pipeline** (convert → cover art → loudness → playlists → sync) |
| Convert | FLAC → Opus in parallel, skips finished files, finds orphaned Opus files |
| Sync to phone | adb mirror over **USB or Wi-Fi** (pair / connect / find on network), preview before changing anything |
| Tag editor | Searchable table, bulk-edit tags and covers, rename files from tags, tags from file names. Edits carry over to the Opus copies |
| Health check | Missing tags/art, duplicates (review and move extras to the trash), corrupt files, FLACs made from MP3s (spectrum check) |
| Art & loudness | Fetch missing covers (MusicBrainz), copy covers into Opus, ReplayGain/R128 loudness tags |
| Playlists | .m3u8 playlists stored in `<library>/Playlists`, exported with Opus paths so they sync to the phone |
| Organize | Folder info (size, play time, artists, genres, decades, quality), tag-based sorter, flatten |

## Download

Grab the latest build from [Releases](https://github.com/tianxibang/Music-tools/releases):

- **Windows**: unzip `MusicTools-windows-x64.zip` and run `MusicTools.exe`.
- **Linux**: `tar xzf MusicTools-linux-x64.tar.gz`, then run `MusicTools/install-linux.sh` to add it to your
  app menu (or just start `MusicTools/MusicTools`).

ffmpeg, opusenc and adb aren't included; see [External programs](#external-programs).

## Run from source

```bash
uv sync                 # or: python -m venv .venv && .venv/bin/pip install -e .
uv run python launcher.py
./install-linux.sh      # optional: add it to the app menu
```

## Build a standalone app

```bash
uv sync --group build
uv run --group build python build.py           # dist/MusicTools/  (add --onefile for a single file)
```

PyInstaller builds for the OS it runs on. For the Windows `.exe`, either run `build.py` on Windows,
or let GitHub do it: pushing a version tag builds Windows and Linux and publishes them as a release.

```bash
git tag v2.0 && git push music-tools v2.0
```

The **build** workflow can also be started by hand from the Actions tab; those builds show up as workflow
artifacts instead of a release.

## External programs

Found automatically next to the app, in a `bin/` folder beside it, or on PATH (or set them in Settings):

- **ffmpeg**: loudness, health checks, and conversion if opusenc is missing
- **opusenc** (opus-tools): the preferred Opus encoder
- **adb** (Android platform-tools): phone sync

Fedora: `sudo dnf install opus-tools ffmpeg android-tools` ·
Windows: `winget install Gyan.FFmpeg Google.PlatformTools`, or drop the `.exe`s into `bin/` before running `build.py` to bundle them.
