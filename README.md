# Music Tools

A desktop app (Windows, Linux) for a FLAC library that gets synced to an Android phone as Opus.
It replaces `flac2opus.fish`, `sync-music.fish` and `flattenplaylist.fish`, and adds more:

| Page | What it does |
|---|---|
| Dashboard | Library stats, phone status, watch folder, **one-click pipeline** (convert → cover art → loudness → playlists → sync) |
| Convert | FLAC → Opus in parallel, skips finished files, finds orphaned Opus files |
| Sync to phone | adb mirror over **USB or Wi-Fi** (pair / connect / find on network), preview before changing anything |
| Tag editor | Searchable table, bulk-edit tags and covers, rename files from tags, tags from file names. Edits carry over to the Opus copies |
| Health check | Missing tags/art, duplicates, corrupt files, FLACs made from MP3s (spectrum check) |
| Art & loudness | Fetch missing covers (MusicBrainz), copy covers into Opus, ReplayGain/R128 loudness tags |
| Playlists | .m3u8 playlists stored in `<library>/Playlists`, exported with Opus paths so they sync to the phone |
| Organize | Folder info (size, play time, artists, genres, decades, quality), tag-based sorter, flatten |

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
or push to GitHub and run the **build** workflow (Actions tab), which builds Windows and Linux together.

## External programs

Found automatically next to the app, in a `bin/` folder beside it, or on PATH (or set them in Settings):

- **ffmpeg**: loudness, health checks, and conversion if opusenc is missing
- **opusenc** (opus-tools): the preferred Opus encoder
- **adb** (Android platform-tools): phone sync

Fedora: `sudo dnf install opus-tools ffmpeg android-tools` ·
Windows: `winget install Gyan.FFmpeg Google.PlatformTools`, or drop the `.exe`s into `bin/` before running `build.py` to bundle them.
