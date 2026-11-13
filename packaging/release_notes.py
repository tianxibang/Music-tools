"""Release page text from CHANGELOG.md, used by CI when a tag is pushed:

    python packaging/release_notes.py 4.2 v4.1.1 notes.md   # prints the release title, writes the notes

The changelog section "## 4.2: Name (date)" becomes "Music Tools 4.2: Name" with the downloads table under it.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/tianxibang/Music-tools"
DOWNLOADS = [
    ("🪟 Windows", "MusicTools-setup-windows-x64.exe", "run it to install (no admin rights needed); later versions "
                                                     "install from inside the app"),
    ("🪟 Windows, portable", "MusicTools-windows-x64.zip", "unzip it and run `MusicTools.exe`"),
    ("🐧 Fedora, openSUSE", "MusicTools-linux-x64.rpm", "`sudo dnf install ./MusicTools-linux-x64.rpm`"),
    ("🐧 Debian, Ubuntu", "MusicTools-linux-x64.deb", "`sudo apt install ./MusicTools-linux-x64.deb`"),
    ("🐧 Linux, portable", "MusicTools-linux-x64.tar.gz", "unpack it, then run `MusicTools/install-linux.sh` to add "
                                                         "it to your app menu"),
    ("🤖 Android", "MusicTools-android.apk", "install it (allow installing from your browser or files app when "
                                            "asked)"),
]


def section(changelog, version):
    """(name, date, body) of the changelog entry for this version."""
    m = re.search(rf"^## {re.escape(version)}: (.+?) \((\d{{4}}-\d\d-\d\d)\)\n(.*?)(?=^## |\Z)", changelog,
                  re.M | re.S)
    if not m:
        raise SystemExit(f'CHANGELOG.md has no "## {version}: Name (date)" section')
    return m.group(1), m.group(2), m.group(3).strip()


def notes(version, previous, changelog):
    name, date, body = section(changelog, version)
    tag = f"v{version}"
    rows = "\n".join(f"| {who} | [`{f}`]({REPO}/releases/download/{tag}/{f}) | {how} |" for who, f, how in DOWNLOADS)
    text = (f"# {name}\n*Music Tools {version} · released {date}*\n\n{body}\n\n---\n\n### 📦 Downloads\n\n"
            f"| | File | How |\n|---|---|---|\n{rows}\n\n"
            f"The desktop app needs ffmpeg and opusenc: see [External programs]({REPO}#external-programs).\n")
    if previous:
        text += f"\n**Full changelog**: [{previous}…{tag}]({REPO}/compare/{previous}...{tag})\n"
    return f"Music Tools {version}: {name}", text


if __name__ == "__main__":
    version, previous, out = sys.argv[1], sys.argv[2], sys.argv[3]
    title, text = notes(version, previous, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    Path(out).write_text(text, encoding="utf-8")
    print(title)
