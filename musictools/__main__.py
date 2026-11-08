"""Entry point: python -m musictools"""

import os
import sys


def main():
    if os.name == "nt":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("musictools.app")
        except Exception:
            pass

    from PySide6.QtWidgets import QApplication

    from .core.util import APP_NAME, config_dir
    from .ui import icons, theme
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("music-tools")
    app.setStyle("Fusion")
    app.setPalette(theme.palette())
    app.setFont(theme.app_font())
    app.setWindowIcon(icons.app_icon())

    # QSS can only load images from files, so write the few it needs next to the settings
    qss = theme.STYLESHEET
    for name, color, stroke in (("check", "#ffffff", 3), ("chev_down", theme.MUTED, 2.5), ("chev_up", theme.MUTED, 2.5)):
        f = config_dir() / f"{name}.svg"
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(icons.svg(name, color, stroke), encoding="utf-8")
        except OSError:
            pass
        qss = qss.replace(f":/{name}.svg", f.as_posix())
    app.setStyleSheet(qss)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
