"""Small stroke icons drawn as inline SVG (24×24 grid), tinted at render time."""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_PATHS = {
    "dashboard": '<rect x="3" y="3" width="7.5" height="9" rx="2"/><rect x="13.5" y="3" width="7.5" height="5" rx="2"/>'
                 '<rect x="13.5" y="11" width="7.5" height="10" rx="2"/><rect x="3" y="15" width="7.5" height="6" rx="2"/>',
    "convert": '<path d="M4 9h13"/><path d="M13 5l4 4-4 4"/><path d="M20 15H7"/><path d="M11 11l-4 4 4 4"/>',
    "phone": '<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M10.5 18.5h3"/>',
    "tag": '<path d="M3 4.5v6.3a1.5 1.5 0 0 0 .44 1.06l8.7 8.7a1.5 1.5 0 0 0 2.12 0l6.3-6.3a1.5 1.5 0 0 0 0-2.12'
           'l-8.7-8.7A1.5 1.5 0 0 0 10.8 3H4.5A1.5 1.5 0 0 0 3 4.5z"/><circle cx="7.5" cy="7.5" r="1.4"/>',
    "health": '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="1.8"/>'
             '<path d="M21 15.5l-4.5-4.5L6 21"/>',
    "playlist": '<path d="M3 6h12"/><path d="M3 11h12"/><path d="M3 16h7"/><circle cx="16.5" cy="17.5" r="2.5"/>'
                '<path d="M19 17.5V7l2.5 1"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8A2.5 2.5 0 0 1 18.5 20h-13'
              'A2.5 2.5 0 0 1 3 17.5z"/>',
    "settings": '<path d="M4 6h9"/><path d="M17 6h3"/><circle cx="15" cy="6" r="2"/><path d="M4 12h3"/>'
                '<path d="M11 12h9"/><circle cx="9" cy="12" r="2"/><path d="M4 18h11"/><path d="M19 18h1"/>'
                '<circle cx="17" cy="18" r="2"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
    "disk": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.66 3.58 3 8 3s8-1.34 8-3V6"/>'
            '<path d="M4 12v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "alert": '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/>'
               '<path d="M20 20v-4h-4"/>',
    "play": '<path d="M7 4.5v15l12-7.5z"/>',
    "wifi": '<path d="M2.5 9a14 14 0 0 1 19 0"/><path d="M5.5 12.5a9.5 9.5 0 0 1 13 0"/><path d="M8.7 16a5 5 0 0 1 6.6 0"/>'
            '<path d="M12 19.5h.01"/>',
    "usb": '<circle cx="12" cy="19" r="2"/><path d="M12 17V4"/><path d="M9 7l3-3 3 3"/><path d="M12 14l-4-2.5V9"/>'
           '<path d="M12 12l4-2V8"/><rect x="15" y="6.5" width="2" height="2"/><circle cx="8" cy="8" r="1"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 '
             '2-2l1-12"/><path d="M9 7V4h6v3"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "chev_down": '<path d="M6 9l6 6 6-6"/>',
    "chev_up": '<path d="M6 15l6-6 6 6"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "sparkle": '<path d="M12 3l1.9 5.6L19.5 10l-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.4z"/><path d="M19 16l.8 2.2L22 19'
               'l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
    "volume": '<path d="M4 9.5v5h3.5L12 19V5L7.5 9.5z"/><path d="M15.5 9a4 4 0 0 1 0 6"/><path d="M18 6.5a7.5 7.5 0 0 1 0 11"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "up": '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>',
    "link": '<path d="M10 14a4.5 4.5 0 0 0 6.4 0l3.2-3.2a4.5 4.5 0 0 0-6.4-6.4L12 5.6"/>'
            '<path d="M14 10a4.5 4.5 0 0 0-6.4 0l-3.2 3.2a4.5 4.5 0 0 0 6.4 6.4l1.2-1.2"/>',
}

_FILLED = {"play", "stop"}
_cache = {}


def svg(name, color, stroke=1.8):
    fill = color if name in _FILLED else "none"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{_PATHS[name]}</svg>')


def pixmap(name, color, size=18, ratio=2.0):
    key = (name, color, size, ratio)
    if key not in _cache:
        renderer = QSvgRenderer(QByteArray(svg(name, color).encode()))
        img = QImage(int(size * ratio), int(size * ratio), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, img.width(), img.height()))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(ratio)
        _cache[key] = pm
    return _cache[key]


def icon(name, color, active=None, size=18):
    ic = QIcon()
    ic.addPixmap(pixmap(name, color, size), QIcon.Normal, QIcon.Off)
    if active:
        ic.addPixmap(pixmap(name, active, size), QIcon.Selected, QIcon.Off)
        ic.addPixmap(pixmap(name, active, size), QIcon.Active, QIcon.Off)
    return ic


def app_icon_svg():
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#a07bf8"/><stop offset="1" stop-color="#6d3be0"/></linearGradient></defs>'
            '<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#g)"/>'
            '<g fill="none" stroke="#fff" stroke-width="16" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M100 178V74l88-16v104"/></g>'
            '<circle cx="78" cy="180" r="24" fill="#fff"/><circle cx="166" cy="164" r="24" fill="#fff"/></svg>')


def app_icon():
    renderer = QSvgRenderer(QByteArray(app_icon_svg().encode()))
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        img = QImage(s, s, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, s, s))
        p.end()
        ic.addPixmap(QPixmap.fromImage(img))
    return ic
