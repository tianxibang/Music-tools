"""Build a standalone app with PyInstaller.

    python build.py            # -> dist/MusicTools/ (folder build, starts fast)
    python build.py --onefile  # -> dist/MusicTools(.exe) single file

PyInstaller can't cross-compile: run this on Windows to get the .exe, on Linux for the Linux build.
Optional: put opusenc / ffmpeg / adb binaries in a "bin" folder here and they're bundled next to the app.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Qt modules the app doesn't use; leaving them out shrinks the build a lot
EXCLUDES = ["PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
            "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
            "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest", "tkinter", "matplotlib", "scipy"]


def main():
    # PyInstaller only bundles what this interpreter can import; without this check a wrong python
    # (e.g. the system one) produces an app that builds fine but crashes on launch
    for mod in ("PySide6", "mutagen", "numpy", "PyInstaller"):
        try:
            __import__(mod)
        except ImportError:
            sys.exit(f"{mod} isn't installed for {sys.executable}.\n"
                     "Build from the project venv instead:  uv run --group build python build.py")
    onefile = "--onefile" in sys.argv
    icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "MusicTools",
           "--icon", str(icon), "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
           "--specpath", str(ROOT / "build"), "--onefile" if onefile else "--onedir"]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ROOT / "launcher.py"))
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)

    tools = ROOT / "bin"
    if tools.is_dir() and any(tools.iterdir()):
        target = ROOT / "dist" / ("bin" if onefile else "MusicTools/bin")
        shutil.copytree(tools, target, dirs_exist_ok=True)
        print(f"Bundled tools from {tools} -> {target}")
    print(f"\nDone: {ROOT / 'dist'}")


if __name__ == "__main__":
    main()
