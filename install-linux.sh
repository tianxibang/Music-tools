#!/usr/bin/env bash
# Adds Music Tools to the app menu (GNOME, KDE, …).
# Uses the built binary in dist/ if there is one, otherwise runs from source with the .venv.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

if [[ -x "$here/dist/MusicTools/MusicTools" ]]; then
    exec_line="$here/dist/MusicTools/MusicTools"
elif [[ -x "$here/dist/MusicTools" ]]; then
    exec_line="$here/dist/MusicTools"
elif [[ -x "$here/.venv/bin/python" ]]; then
    exec_line="$here/.venv/bin/python $here/launcher.py"
else
    echo "Nothing to launch yet: run 'uv sync' (or uv run --group build python build.py) first." >&2
    exit 1
fi

icon_dir="$HOME/.local/share/icons/hicolor/256x256/apps"
mkdir -p "$icon_dir" "$HOME/.local/share/applications"
cp "$here/assets/icon.png" "$icon_dir/music-tools.png"
cat > "$HOME/.local/share/applications/music-tools.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Music Tools
Comment=Convert, tag, check and sync your music library
Exec=$exec_line
Icon=$icon_dir/music-tools.png
Terminal=false
Categories=AudioVideo;Audio;Utility;
StartupWMClass=music-tools
EOF
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "Installed: look for “Music Tools” in your app menu."
