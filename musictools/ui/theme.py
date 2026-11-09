"""Dark theme: palette tokens and the application stylesheet."""

from PySide6.QtGui import QColor, QFont, QPalette

BG = "#0d0f13"
SIDEBAR = "#111419"
CARD = "#171a21"
RAISED = "#1e222b"
INPUT = "#13161c"
BORDER = "#262b36"
BORDER_HI = "#353c4a"
TEXT = "#e8eaf0"
MUTED = "#8a91a3"
FAINT = "#5c6375"
ACCENT = "#8b5cf6"
ACCENT_HI = "#a07bf8"
ACCENT_LO = "#7445e8"
ACCENT_SOFT = "rgba(139, 92, 246, 0.16)"
OK = "#34d399"
WARN = "#fbbf24"
ERR = "#f87171"

LEVEL_COLORS = {"info": TEXT, "ok": OK, "warn": WARN, "error": ERR, "head": ACCENT_HI, "muted": MUTED}

FONT_FAMILIES = ["Inter", "Segoe UI Variable Text", "Segoe UI", "Cantarell", "Noto Sans", "Ubuntu", "sans-serif"]
MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Fira Code", "DejaVu Sans Mono", "monospace"]


def app_font():
    f = QFont()
    f.setFamilies(FONT_FAMILIES)
    f.setPointSizeF(10)
    return f


def mono_font():
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setPointSizeF(9)
    f.setStyleHint(QFont.Monospace)
    return f


def palette():
    p = QPalette()
    for role, color in [
        (QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, INPUT),
        (QPalette.AlternateBase, CARD), (QPalette.Text, TEXT), (QPalette.Button, RAISED),
        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, RAISED), (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, FAINT),
        (QPalette.Link, ACCENT_HI),
    ]:
        p.setColor(role, QColor(color))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(FAINT))
    p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(FAINT))
    return p


STYLESHEET = f"""
* {{ outline: none; }}
QWidget {{ color: {TEXT}; }}
QMainWindow, #content {{ background: {BG}; }}
QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER_HI}; padding: 6px 8px; border-radius: 6px; }}

/* ---- sidebar ---- */
#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
#brand {{ font-size: 15pt; font-weight: 700; }}
#brandSub {{ color: {MUTED}; font-size: 8.5pt; }}
#nav {{ background: transparent; border: none; }}
#nav::item {{ padding: 9px 12px; margin: 2px 10px; border-radius: 8px; color: {MUTED}; }}
#nav::item:hover {{ background: {RAISED}; color: {TEXT}; }}
#nav::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
#jobBox {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
#jobTitle {{ font-weight: 600; font-size: 9pt; }}
#jobText {{ color: {MUTED}; font-size: 8.5pt; }}

/* ---- page chrome ---- */
#pageTitle {{ font-size: 20pt; font-weight: 700; }}
#pageSub {{ color: {MUTED}; font-size: 10pt; }}
#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 14px; }}
#cardTitle {{ font-size: 11pt; font-weight: 650; }}
#cardSub, .muted, #muted {{ color: {MUTED}; }}
#statValue {{ font-size: 19pt; font-weight: 700; }}
#statLabel {{ color: {MUTED}; font-size: 9pt; }}
#statIcon {{ background: {ACCENT_SOFT}; border-radius: 10px; }}
#chip {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 10px; padding: 2px 9px; color: {MUTED}; font-size: 8.5pt; }}
#dot[state="ok"] {{ color: {OK}; }}
#dot[state="warn"] {{ color: {WARN}; }}
#dot[state="off"] {{ color: {FAINT}; }}
#hint {{ color: {FAINT}; font-size: 8.5pt; }}

/* ---- buttons ---- */
QPushButton {{
    background: {RAISED}; border: 1px solid {BORDER_HI}; border-radius: 8px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: #252a35; border-color: #444c5c; }}
QPushButton:pressed {{ background: #1a1e26; }}
QPushButton:disabled {{ color: {FAINT}; background: {CARD}; border-color: {BORDER}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: white; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HI}; border-color: {ACCENT_HI}; }}
QPushButton[kind="primary"]:pressed {{ background: {ACCENT_LO}; }}
QPushButton[kind="primary"]:disabled {{ background: #3b3355; border-color: #3b3355; color: #9a93b3; }}
QPushButton[kind="danger"] {{ color: {ERR}; }}
QPushButton[kind="danger"]:hover {{ border-color: {ERR}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; color: {MUTED}; }}
QPushButton[kind="ghost"]:hover {{ background: {RAISED}; color: {TEXT}; }}
QPushButton[kind="big"] {{ background: {ACCENT}; border: none; color: white; padding: 12px 22px; font-size: 11pt; border-radius: 10px; }}
QPushButton[kind="big"]:hover {{ background: {ACCENT_HI}; }}
QPushButton[kind="big"]:disabled {{ background: #3b3355; color: #9a93b3; }}

/* ---- inputs ---- */
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {INPUT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 9px; selection-background-color: {ACCENT}; min-height: 20px;
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {BORDER_HI}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled {{ color: {FAINT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QSpinBox::up-arrow {{ image: url(:/chev_up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url(:/chev_down.svg); width: 10px; height: 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url(:/chev_down.svg); width: 11px; height: 11px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {RAISED}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 4px; selection-background-color: {ACCENT_SOFT}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {BORDER_HI}; background: {INPUT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(:/check.svg); }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {BORDER_HI}; background: {INPUT}; }}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ border-color: {ACCENT}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 #ffffff, stop:0.32 #ffffff, stop:0.42 {ACCENT}, stop:1 {ACCENT}); }}

/* ---- log / progress ---- */
#log {{ background: #0b0d11; border: 1px solid {BORDER}; border-radius: 10px; padding: 8px; }}
QProgressBar {{ background: {RAISED}; border: none; border-radius: 3px; max-height: 6px; min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_LO}, stop:1 {ACCENT_HI}); border-radius: 3px; }}

/* ---- tables & lists ---- */
QTableView, QListWidget, QTreeView {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: transparent; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
    alternate-background-color: #1a1d25;
}}
QTableView::item, QListWidget::item {{ padding: 4px 6px; border: none; }}
QTreeView::item {{ padding: 3px 4px; border: none; }}
QTreeView::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {BORDER_HI}; background: {INPUT}; }}
QTreeView::indicator:hover {{ border-color: {ACCENT}; }}
QTreeView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(:/check.svg); }}
QTableView::item:selected, QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QListWidget::item:hover {{ background: {RAISED}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 8px; font-weight: 600; font-size: 8.5pt;
}}
QTableCornerButton::section {{ background: {CARD}; border: none; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: none; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px; margin-right: 4px; border-radius: 8px; font-weight: 600; }}
QTabBar::tab:hover {{ background: {RAISED}; color: {TEXT}; }}
QTabBar::tab:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}

/* ---- scrollbars ---- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: #2c3240; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #3b4253; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: transparent; width: 10px; }}
QMenu {{ background: {RAISED}; border: 1px solid {BORDER_HI}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; }}
QDialog {{ background: {BG}; }}
QMessageBox {{ background: {CARD}; }}
"""
