"""The lyrics editor's timeline (waveform with draggable lines, ad-libs and words) and its karaoke-style preview."""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QMenu, QScrollBar, QWidget

from ..core.lyrics import stamp
from ..core.util import SETTINGS
from . import theme
from .audio import PEAK_BIN, SR

MIN_LINE = 0.1      # shortest line, in seconds
MIN_WORD = 0.03
EDGE = 5            # px: how close counts as grabbing an edge
BINS_PER_SEC = SR / PEAK_BIN
# fill, outline per singer; ad-libs get their own colour
VOICES = {"v2": ("#14b8a6", "#2dd4bf"), "v3": ("#ec4899", "#f9a8d4")}
ADLIB = ("#d97706", "#fbbf24")
VOICE_NAMES = {"": "—", "v1": "1", "v2": "2", "v3": "3"}


def _qc(color, alpha=None):
    c = QColor(color)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def voice_colors(voice):
    return VOICES.get(voice, (theme.ACCENT, theme.ACCENT_HI))


def _times(l):
    return l.start, l.end, [(w.start, w.end) for w in l.words], [_times(b) for b in l.bg]


def _put(l, t):
    l.start, l.end = t[0], t[1]
    for w, (s, e) in zip(l.words, t[2]):
        w.start, w.end = s, e
    for b, bt in zip(l.bg, t[3]):
        _put(b, bt)


def adlib_end(b):
    """When an ad-lib ends: its own end, its last word's, or a guess."""
    if b.end is not None:
        return b.end
    ends = [w.end for w in b.words if w.end is not None]
    starts = [w.start for w in b.words if w.start is not None]
    return ends[-1] if ends else (starts[-1] + 0.6 if starts else b.start + 1.5)


class Timeline(QWidget):
    """Waveform, ruler, a lane of line blocks, a lane of ad-libs (when there are any) and, in word mode, a
    lane of word blocks.

    Drag a block to move it, its edges to change when it starts or ends; Shift moves everything after it
    too, Ctrl grabs the end of a line (or word) where it touches the next one. The playhead scrubs when dragged.
    """
    edited = Signal()                 # a change to the lyrics is finished (one undo step)
    lineClicked = Signal(object)      # Line
    playFrom = Signal(float)
    seeked = Signal(float)
    addAdlib = Signal(object, float)  # line, time: the page asks for the words

    RULER, WAVE, LANE, BLANE, WLANE = 20, 64, 30, 22, 26

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.doc = None
        self.peaks = None
        self.duration = 0.0
        self.pps = 90.0               # pixels per second
        self.offset = 0.0             # seconds at the left edge
        self.words = False
        self.selected = None
        self.playhead = 0.0
        self.follow = True
        self._drag = None
        self.message = "Pick a song to see its waveform."
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.scrollbar = QScrollBar(Qt.Horizontal)
        self.scrollbar.valueChanged.connect(self._scrolled)
        self._fix_height()

    # --- data

    def set_song(self, peaks, duration):
        self.peaks, self.duration = peaks, duration
        self.offset = 0.0
        self.message = ""
        self._update_scroll()
        self.update()

    def set_doc(self, doc):
        self.doc = doc
        if self.selected is not None and doc is not None and not any(l is self.selected for l in doc.lines):
            self.selected = None
        self._fix_height()
        self.update()

    def set_words(self, on):
        self.words = on
        self._fix_height()
        self.update()

    @property
    def adlibs(self):
        return bool(self.doc) and any(l.bg for l in self.doc.lines)

    def _fix_height(self):
        h = self.RULER + self.WAVE + 8 + self.LANE + 6
        h += (6 + self.BLANE) if self.adlibs else 0
        h += (8 + self.WLANE) if self.words else 0
        self.setFixedHeight(h)

    def timed(self):
        return self.doc.timed() if self.doc else []

    # --- coordinates

    def x(self, t):
        return (t - self.offset) * self.pps

    def t(self, x):
        return self.offset + x / self.pps

    @property
    def visible(self):
        return self.width() / self.pps

    def _lane_y(self):
        return self.RULER + self.WAVE + 8

    def _blane_y(self):
        return self._lane_y() + self.LANE + 6

    def _wlane_y(self):
        return (self._blane_y() + self.BLANE if self.adlibs else self._lane_y() + self.LANE) + 8

    def zoom(self, factor, anchor_x=None):
        anchor_x = self.width() / 2 if anchor_x is None else anchor_x
        at = self.t(anchor_x)
        fit = self.width() / max(self.duration, 1)
        self.pps = min(max(self.pps * factor, min(fit, 8)), 1200)
        self.offset = at - anchor_x / self.pps
        self._clamp_offset()
        self._update_scroll()
        self.update()

    def show_time(self, t, margin=0.2):
        """Scroll so t is in view."""
        if not (self.offset + self.visible * 0.05 <= t <= self.offset + self.visible * 0.95):
            self.offset = t - self.visible * margin
            self._clamp_offset()
            self._update_scroll()
            self.update()

    def _clamp_offset(self):
        self.offset = max(0.0, min(self.offset, max(0.0, self.duration - self.visible)))

    def _update_scroll(self):
        self.scrollbar.blockSignals(True)
        self.scrollbar.setRange(0, int(max(0.0, self.duration - self.visible) * 100))
        self.scrollbar.setPageStep(int(self.visible * 100))
        self.scrollbar.setSingleStep(max(1, int(self.visible * 10)))
        self.scrollbar.setValue(int(self.offset * 100))
        self.scrollbar.blockSignals(False)

    def _scrolled(self, v):
        self.offset = v / 100
        self.update()

    def resizeEvent(self, e):
        self._clamp_offset()
        self._update_scroll()
        super().resizeEvent(e)

    def set_playhead(self, t, playing=False):
        self.playhead = t
        if playing and self.follow and self._drag is None and not (self.offset <= t <= self.offset + self.visible * 0.9):
            self.offset = t - self.visible * 0.1
            self._clamp_offset()
            self._update_scroll()
        self.update()

    # --- painting

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), _qc(theme.INPUT))
        if not self.duration:
            p.setPen(_qc(theme.FAINT))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        self._paint_ruler(p, w)
        timed = self.timed()
        self._paint_wave(p, w, timed)
        self._paint_lines(p, timed)
        if self.adlibs:
            self._paint_adlibs(p, timed)
        if self.words:
            self._paint_words(p, timed)
        x = self.x(self.playhead)
        if -2 <= x <= w + 2:
            p.setPen(QPen(_qc("#ffffff", 220), 1.4))
            p.drawLine(QPointF(x, 0), QPointF(x, h))
            p.setBrush(_qc("#ffffff"))
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 7)]))
        x_end = self.x(self.duration)
        if x_end < w:
            p.fillRect(QRectF(x_end, 0, w - x_end, h), _qc(theme.BG))

    def _paint_ruler(self, p, w):
        p.fillRect(QRectF(0, 0, w, self.RULER), _qc(theme.CARD))
        step = next((s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120) if s * self.pps >= 70), 300)
        f = p.font()
        f.setPointSizeF(7.5)
        p.setFont(f)
        t = (self.offset // step) * step
        while t <= self.offset + self.visible + step:
            x = self.x(t)
            p.setPen(_qc(theme.BORDER_HI))
            p.drawLine(QPointF(x, self.RULER - 6), QPointF(x, self.RULER))
            for k in range(1, 4):
                sx = self.x(t + step * k / 4)
                p.drawLine(QPointF(sx, self.RULER - 3), QPointF(sx, self.RULER))
            p.setPen(_qc(theme.MUTED))
            m, s = divmod(t, 60)
            label = f"{int(m)}:{s:04.1f}" if step < 1 else f"{int(m)}:{int(round(s)):02}"
            p.drawText(QPointF(x + 3, 11), label)
            t += step

    def _paint_wave(self, p, w, timed):
        top, hgt = self.RULER, self.WAVE
        mid = top + hgt / 2
        # sung parts are shaded, so instrumental gaps stand out
        for i, l in enumerate(timed):
            end = l.end_at(timed[i + 1] if i + 1 < len(timed) else None)
            end = end if end is not None else min(l.start + 4, self.duration)
            x0, x1 = self.x(l.start), self.x(end)
            if x1 >= 0 and x0 <= w:
                p.fillRect(QRectF(x0, top, x1 - x0, hgt),
                           _qc(voice_colors(l.voice)[0], 26 if l is not self.selected else 50))
        if self.peaks is None or not len(self.peaks):
            return
        # the loudest bin under each pixel column
        edges = ((self.offset + np.arange(int(w) + 2) / self.pps) * BINS_PER_SEC).astype(np.int64)
        edges = np.clip(edges, 0, len(self.peaks) - 1)
        vals = np.maximum.reduceat(self.peaks, edges)[:-1]
        path = QPainterPath()
        for x, v in enumerate(vals):
            if self.t(x) > self.duration:
                break
            a = max(0.5, float(v) * (hgt / 2 - 3))
            path.moveTo(x + 0.5, mid - a)
            path.lineTo(x + 0.5, mid + a)
        p.setPen(QPen(_qc(theme.ACCENT_HI, 170), 1))
        p.drawPath(path)

    def _block(self, p, x0, x1, y, hgt, text, colors, sel, radius=5, size=8.8):
        r = QRectF(x0 + 0.5, y, max(2.0, x1 - x0 - 1.5), hgt)
        fill, line = colors
        p.setPen(QPen(_qc(line if sel else fill), 1.2 if sel else 1))
        p.setBrush(_qc(fill, 200 if sel else 90))
        p.drawRoundedRect(r, radius, radius)
        if r.width() > 14:
            f = p.font()
            f.setPointSizeF(size)
            p.setFont(f)
            p.setPen(_qc("#ffffff" if sel else theme.TEXT))
            p.drawText(r.adjusted(5, 0, -3, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       QFontMetricsF(f).elidedText(text, Qt.ElideRight, r.width() - 8))
        return r

    def _paint_lines(self, p, timed):
        y, hgt = self._lane_y(), self.LANE
        for i, l in enumerate(timed):
            nxt = timed[i + 1] if i + 1 < len(timed) else None
            end = l.end_at(nxt)
            open_end = end is None
            end = end if end is not None else min(l.start + 4, max(self.duration, l.start + 0.5))
            x0, x1 = self.x(l.start), self.x(end)
            if nxt and l.end is not None:
                self._paint_gap(p, l.end, nxt.start, y, hgt)
            if x1 < -50 or x0 > self.width() + 50:
                continue
            r = self._block(p, x0, x1, y, hgt, l.text, voice_colors(l.voice), l is self.selected)
            if open_end:   # shown until the next line, which never comes: fade out
                p.fillRect(QRectF(r.right() - 16, y + 1, 16, hgt - 2), _qc(theme.INPUT, 120))

    def _paint_gap(self, p, a, b, y, hgt):
        x0, x1 = self.x(a), self.x(b)
        if x1 - x0 < 34 or b - a < 1.5 or x1 < 0 or x0 > self.width():
            return
        p.setPen(QPen(_qc(theme.FAINT), 1, Qt.DashLine))
        p.drawLine(QPointF(x0 + 4, y + hgt / 2), QPointF(x1 - 4, y + hgt / 2))
        label = f"♪ {b - a:.1f} s"
        f = p.font()
        f.setPointSizeF(8.5)
        p.setFont(f)
        tw = QFontMetricsF(f).horizontalAdvance(label) + 10
        if x1 - x0 > tw + 10:
            r = QRectF((x0 + x1 - tw) / 2, y + 5, tw, hgt - 10)
            p.fillRect(r, _qc(theme.INPUT))
            p.setPen(_qc(theme.MUTED))
            p.drawText(r, Qt.AlignCenter, label)

    def _paint_adlibs(self, p, timed):
        y = self._blane_y()
        for l in timed:
            for b in l.bg:
                if b.start is None:
                    continue
                x0, x1 = self.x(b.start), self.x(adlib_end(b))
                if x1 < -20 or x0 > self.width() + 20:
                    continue
                self._block(p, x0, x1, y, self.BLANE, b.text, ADLIB, l is self.selected, 4, 8.0)

    def _word_spans(self, line, nxt):
        """[(index, start, end)] for the timed words of a line."""
        out = []
        for j, w in enumerate(line.words):
            if w.start is None:
                continue
            e = line.word_end(j, nxt)
            out.append((j, w.start, e if e is not None else w.start + 0.6))
        return out

    def _paint_words(self, p, timed):
        y, hgt = self._wlane_y(), self.WLANE
        for i, l in enumerate(timed):
            nxt = timed[i + 1] if i + 1 < len(timed) else None
            if self.x(l.start) > self.width() + 50:
                break
            fill, line = voice_colors(l.voice)
            for j, s, e in self._word_spans(l, nxt):
                x0, x1 = self.x(s), self.x(e)
                if x1 < -20 or x0 > self.width() + 20:
                    continue
                sel = l is self.selected
                r = QRectF(x0 + 0.5, y, max(2.0, x1 - x0 - 1.5), hgt)
                p.setPen(QPen(_qc(theme.BORDER_HI), 1))
                p.setBrush(_qc(fill, 110 if sel else 45))
                p.drawRoundedRect(r, 4, 4)
                if r.width() > 12:
                    f = p.font()
                    f.setPointSizeF(8.2)
                    p.setFont(f)
                    p.setPen(_qc(theme.TEXT))
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               QFontMetricsF(f).elidedText(l.words[j].text.strip(), Qt.ElideRight, r.width() - 6))

    # --- hit testing

    def _hit(self, pos, ctrl=False):
        """What's under the mouse: ("line", part, line) / ("word", part, line, j) / ("bg", part, adlib, line)."""
        timed = self.timed()
        px, py = pos.x(), pos.y()
        if self.words and self._wlane_y() <= py <= self._wlane_y() + self.WLANE:
            best = None
            for i, l in enumerate(timed):
                nxt = timed[i + 1] if i + 1 < len(timed) else None
                spans = self._word_spans(l, nxt)
                for k, (j, s, e) in enumerate(spans):
                    x0, x1 = self.x(s), self.x(e)
                    if abs(px - x0) <= EDGE:
                        return ("word", "start", l, j)
                    last = k == len(spans) - 1
                    if abs(px - x1) <= EDGE and (last or ctrl or l.words[j].end is not None):
                        best = best or ("word", "end", l, j)
                    elif x0 < px < x1:
                        best = best or ("word", "body", l, j)
            return best
        if self.adlibs and self._blane_y() <= py <= self._blane_y() + self.BLANE:
            hit = None
            for l in timed:
                for b in l.bg:
                    if b.start is None:
                        continue
                    x0, x1 = self.x(b.start), self.x(adlib_end(b))
                    if abs(px - x0) <= EDGE:
                        return ("bg", "start", b, l)
                    if abs(px - x1) <= EDGE:
                        return ("bg", "end", b, l)
                    if x0 < px < x1:
                        hit = ("bg", "body", b, l)
            return hit
        if not (self._lane_y() <= py <= self._lane_y() + self.LANE):
            return None
        hit = None
        for i, l in enumerate(timed):
            nxt = timed[i + 1] if i + 1 < len(timed) else None
            end = l.end_at(nxt)
            x0 = self.x(l.start)
            x1 = self.x(end if end is not None else min(l.start + 4, self.duration))
            touching = nxt is not None and end is not None and abs(end - nxt.start) < 1e-6
            if abs(px - x1) <= EDGE and (not touching or ctrl):
                return ("line", "end", l)
            if abs(px - x0) <= EDGE:
                prev = timed[i - 1] if i else None
                if ctrl and prev is not None and prev.end_at(l) is not None and abs(prev.end_at(l) - l.start) < 1e-6:
                    return ("line", "end", prev)
                return ("line", "start", l)
            if x0 < px < x1:
                hit = ("line", "body", l)
        return hit

    @staticmethod
    def _owner(hit):
        return hit[3] if hit[0] == "bg" else hit[2]

    # --- mouse

    def mousePressEvent(self, e):
        if not self.duration or e.button() != Qt.LeftButton:
            return
        pos = e.position()
        hit = self._hit(pos, bool(e.modifiers() & Qt.ControlModifier))
        t = self.t(pos.x())
        self._was_playing = self.player.playing
        self._resume_at = self.player.position()
        if hit is None:
            self._drag = {"kind": "seek"}
            self._seek_to(t)
            return
        line = self._owner(hit)
        self.selected = line
        self.lineClicked.emit(line)
        self._drag = {"kind": hit[0], "part": hit[1], "line": line, "item": hit[2],
                      "j": hit[3] if hit[0] == "word" else None, "t0": t, "moved": False,
                      "ripple": bool(e.modifiers() & Qt.ShiftModifier),
                      "orig": [(l, _times(l)) for l in self.timed()]}
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        d = self._drag
        if d is None:
            hit = self._hit(pos, bool(e.modifiers() & Qt.ControlModifier)) if self.duration else None
            self.setCursor(Qt.SizeHorCursor if hit and hit[1] in ("start", "end")
                           else Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            return
        t = self.t(pos.x())
        if d["kind"] == "seek":
            self._seek_to(t)
            return
        if not d["moved"] and abs(t - d["t0"]) * self.pps < 3:
            return
        d["moved"] = True
        for l, times in d["orig"]:
            _put(l, times)
        edit_t = self._apply(d, t - d["t0"])
        if edit_t is not None:
            self.player.scrub(edit_t)
            if not 0 <= self.x(edit_t) <= self.width():
                self.show_time(edit_t, 0.5)
        self.update()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if d is None:
            return
        if d["kind"] == "seek":
            self.player.end_scrub()
            self.seeked.emit(self.playhead)
            return
        self.player.end_scrub(resume=False)
        if self._was_playing:
            self.player.play(self._resume_at)
        if d["moved"]:
            self.edited.emit()
        self.update()

    def mouseDoubleClickEvent(self, e):
        hit = self._hit(e.position())
        if hit:
            start = hit[2].start if hit[0] == "bg" else self._owner(hit).start
            self.playFrom.emit(max(0.0, start - 0.5))

    def wheelEvent(self, e):
        if not self.duration:
            return
        dy = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            self.zoom(1.2 if dy > 0 else 1 / 1.2, e.position().x())
        else:
            self.offset -= dy / 120 * self.visible * 0.15
            self._clamp_offset()
            self._update_scroll()
            self.update()
        e.accept()

    def contextMenuEvent(self, e):
        hit = self._hit(e.pos())
        if not hit:
            return
        line = self._owner(hit)
        self.selected = line
        self.lineClicked.emit(line)
        menu = QMenu(self)
        ph = self.playhead
        if hit[0] == "bg":
            b = hit[2]
            menu.addAction("Remove this ad-lib", lambda: self._remove_adlib(line, b))
        else:
            menu.addAction(f"Start here ({stamp(ph)})", lambda: self._set(line, "start", ph))
            menu.addAction(f"End here ({stamp(ph)})", lambda: self._set(line, "end", ph))
            if line.end is not None:
                menu.addAction("Show until the next line (no gap)", lambda: self._set(line, "end", None))
            menu.addSeparator()
            singer = menu.addMenu("Sung by")
            for v, name in (("", "Not set"), ("v1", "Singer 1"), ("v2", "Singer 2"), ("v3", "Singer 3")):
                act = singer.addAction(name, lambda v=v: self._set_voice(line, v))
                act.setCheckable(True)
                act.setChecked(line.voice == v)
            menu.addAction(f"Add an ad-lib at {stamp(ph)}…", lambda: self.addAdlib.emit(line, ph))
        menu.addSeparator()
        menu.addAction("Play from here", lambda: self.playFrom.emit(max(0.0, line.start - 0.5)))
        menu.exec(e.globalPos())

    def _set_voice(self, line, voice):
        line.voice = voice
        self.edited.emit()
        self.update()

    def _remove_adlib(self, line, b):
        line.bg = [x for x in line.bg if x is not b]
        self.edited.emit()
        self._fix_height()
        self.update()

    def _set(self, line, what, t):
        """Set a line's start or end, within its neighbours. Returns False if it doesn't fit."""
        timed = self.timed()
        i = next(k for k, l in enumerate(timed) if l is line)
        prev = timed[i - 1] if i else None
        nxt = timed[i + 1] if i + 1 < len(timed) else None
        if what == "start":
            lo = prev.start + MIN_LINE if prev else 0.0
            end = line.end_at(nxt)
            hi = (end if end is not None else self.duration) - MIN_LINE
            if not lo <= t <= hi:
                return False
            if prev and prev.end is not None and prev.end > t:
                prev.end = t
            _move_start(line, t)
        else:
            if t is not None:
                last_word = max((w.start for w in line.words if w.start is not None), default=line.start)
                if t < max(line.start, last_word) + MIN_LINE or (nxt and t > nxt.start):
                    return False
                if nxt and abs(nxt.start - t) < 1e-6:
                    t = None
            line.end = t
        self.edited.emit()
        self.update()
        return True

    def _seek_to(self, t):
        t = min(max(0.0, t), self.duration)
        self.playhead = t
        self.player.scrub(t)
        self.update()

    def _snap(self, t):
        return self.playhead if abs((t - self.playhead) * self.pps) <= SETTINGS["timeline_snap"] else t

    def _apply(self, d, dt):
        """Apply a drag of dt seconds to the original times. Returns the time being edited (to scrub)."""
        timed = self.timed()
        line = d["line"]
        i = next(k for k, l in enumerate(timed) if l is line)
        prev = timed[i - 1] if i else None
        nxt = timed[i + 1] if i + 1 < len(timed) else None
        lo = (prev.end if prev and prev.end is not None else prev.start + MIN_LINE) if prev else 0.0
        if d["kind"] == "bg":
            return self._apply_adlib(d["item"], d["part"], dt)
        if d["kind"] == "word":
            j = d["j"]
            w = line.words[j]
            if d["part"] == "end":
                after = next((v.start for v in line.words[j + 1:] if v.start is not None), None)
                hi = after if after is not None else (nxt.start if nxt else self.duration)
                e0 = line.word_end(j, nxt)
                e0 = e0 if e0 is not None else w.start + 0.6
                t = min(max(self._snap(e0 + dt), w.start + MIN_WORD), hi)
                # dragged back onto the next word: no pause between them
                w.end = None if after is not None and (after - t) * self.pps < SETTINGS["timeline_snap"] else t
                return t
            if j != _first_timed(line):
                before = max((v.start for v in line.words[:j] if v.start is not None), default=line.start)
                after = next((v.start for v in line.words[j + 1:] if v.start is not None), None)
                end = w.end if w.end is not None else line.end_at(nxt)
                hi = (after if after is not None else (end if end is not None else self.duration)) - MIN_WORD
                t = min(max(self._snap(w.start + dt), before + MIN_WORD), hi)
                w.start = t
                for v in line.words[:j]:       # the word before can't end after this one starts
                    if v.end is not None and v.end > t:
                        v.end = t
                return t
            part = "start"                     # the first word's start is the line's
        else:
            part = d["part"]
        if part == "body":
            if d["ripple"]:
                dt = max(dt, lo - line.start)
                dt = min(dt, self.duration - timed[-1].start - MIN_LINE)
                for l in timed[i:]:
                    l.shift(dt)
                return line.start
            if line.end is not None:
                hi = (nxt.start if nxt else self.duration) - line.end
            else:
                hi = (nxt.start - MIN_LINE if nxt else self.duration) - line.start
            dt = min(max(dt, lo - line.start), hi)
            snapped = self._snap(line.start + dt) - line.start
            if lo - line.start <= snapped <= hi:
                dt = snapped
            line.shift(dt)
            return line.start
        if part == "start":
            after = next((w.start for w in line.words[1:] if w.start is not None), None)
            end = line.end_at(nxt)
            hi = (after if after is not None else (end if end is not None else self.duration)) - MIN_LINE
            t = min(max(self._snap(line.start + dt), lo), hi)
            _move_start(line, t)
            return t
        # end
        end0 = line.end_at(nxt)
        end0 = end0 if end0 is not None else min(line.start + 4, self.duration)
        last_word = max((w.start for w in line.words if w.start is not None), default=line.start)
        hi = nxt.start if nxt else self.duration
        t = min(max(self._snap(end0 + dt), max(line.start, last_word) + MIN_LINE), hi)
        line.end = None if nxt and (hi - t) * self.pps < SETTINGS["timeline_snap"] else t
        return t

    def _apply_adlib(self, b, part, dt):
        start, end = b.start, adlib_end(b)
        if part == "body":
            dt = min(max(dt, -start), self.duration - end)
            snapped = self._snap(start + dt) - start
            if -start <= snapped <= self.duration - end:
                dt = snapped
            b.shift(dt)
            return b.start
        if part == "start":
            after = next((w.start for w in b.words[1:] if w.start is not None), None)
            hi = (after if after is not None else end) - MIN_WORD
            t = min(max(self._snap(start + dt), 0.0), hi)
            b.start = t
            if b.words:
                b.words[0].start = t
                if b.words[0].end is not None and b.words[0].end <= t:
                    b.words[0].end = None
            return t
        last = max((w.start for w in b.words if w.start is not None), default=start)
        t = min(max(self._snap(end + dt), last + MIN_WORD), self.duration)
        b.end = t
        if b.words:
            b.words[-1].end = t
        return t


def _first_timed(line):
    return next((j for j, w in enumerate(line.words) if w.start is not None), 0)


def _move_start(line, t):
    """Change when a line starts; its first word goes along, later words stay put."""
    line.start = t
    if line.words:
        line.words[0].start = t
        if line.words[0].end is not None and line.words[0].end <= t:
            line.words[0].end = None
        for w in line.words[1:]:
            if w.start is not None and w.start <= t:
                w.start = w.end = None


class Preview(QWidget):
    """How the lyrics will look while the song plays: the current line lit up (word by word when timed),
    duets split left and right, ad-libs under the line they belong to."""

    def __init__(self):
        super().__init__()
        self.doc = None
        self.t = 0.0
        self.setMinimumHeight(96)

    def set_time(self, doc, t):
        self.doc, self.t = doc, t
        self.update()

    def _state(self):
        timed = self.doc.timed() if self.doc else []
        cur = None
        for i, l in enumerate(timed):
            if l.start > self.t:
                break
            cur = i
        if cur is None:
            return timed, None, 0
        l = timed[cur]
        end = l.end_at(timed[cur + 1] if cur + 1 < len(timed) else None)
        in_gap = end is not None and self.t >= end
        return timed, cur, in_gap

    def _align(self, line):
        if not self.doc.duet:
            return Qt.AlignHCenter
        return Qt.AlignRight if line.voice == "v2" else Qt.AlignLeft

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(_qc(theme.BORDER), 1))
        p.setBrush(_qc("#0b0d11"))
        p.drawRoundedRect(r, 10, 10)
        if not self.doc or not self.doc.synced:
            p.setPen(_qc(theme.FAINT))
            p.drawText(r, Qt.AlignCenter, "Instrumental: no lyrics to show." if self.doc and self.doc.instrumental
                       else "The preview shows the lyrics as they'd scroll by once lines have times.")
            return
        timed, cur, in_gap = self._state()
        h = r.height()
        inner = r.adjusted(16, 0, -16, 0)
        small = QFont(self.font())
        small.setPointSizeF(10)
        big = QFont(self.font())
        big.setPointSizeF(16)
        big.setWeight(QFont.DemiBold)
        prev_line = timed[cur] if cur is not None and in_gap else (timed[cur - 1] if cur else None)
        if cur is None:
            now, upcoming = None, timed[0] if timed else None
        elif in_gap:
            now, upcoming = None, timed[cur + 1] if cur + 1 < len(timed) else None
        else:
            now, upcoming = timed[cur], timed[cur + 1] if cur + 1 < len(timed) else None
        p.setFont(small)
        p.setPen(_qc(theme.FAINT))
        fm = QFontMetricsF(small)
        if prev_line:
            p.drawText(QRectF(inner.left(), r.top() + 6, inner.width(), h * 0.22),
                       self._align(prev_line) | Qt.AlignVCenter,
                       fm.elidedText(prev_line.text, Qt.ElideRight, inner.width()))
        if upcoming:
            p.drawText(QRectF(inner.left(), r.bottom() - h * 0.22 - 4, inner.width(), h * 0.22),
                       self._align(upcoming) | Qt.AlignVCenter,
                       fm.elidedText(upcoming.text, Qt.ElideRight, inner.width()))
        mid = QRectF(inner.left(), r.top() + h * 0.24, inner.width(), h * 0.34)
        if now is None:
            p.setFont(big)
            p.setPen(_qc(theme.MUTED))
            p.drawText(mid, Qt.AlignCenter, "♪  ♪  ♪")
            return
        self._paint_line(p, now, upcoming, big, mid, self._align(now))
        bgs = [b for b in now.bg if b.start is not None and b.start - 1.0 <= self.t]
        if bgs:
            f = QFont(self.font())
            f.setPointSizeF(11)
            f.setItalic(True)
            sub = QRectF(inner.left(), mid.bottom(), inner.width(), h * 0.17)
            self._paint_line(p, bgs[-1], None, f, sub, self._align(now), ADLIB[1])

    def _paint_line(self, p, line, nxt, font, rect, align, lit="#ffffff"):
        fm = QFontMetricsF(font)
        while fm.horizontalAdvance(line.text) > rect.width() and font.pointSizeF() > 9:
            font.setPointSizeF(font.pointSizeF() - 1)
            fm = QFontMetricsF(font)
        p.setFont(font)
        timed_words = [w for w in line.words if w.start is not None]
        if not timed_words or (len(timed_words) < 2 and timed_words[0].end is None):
            sung = line.start is not None and self.t >= line.start
            p.setPen(_qc(lit if sung else theme.FAINT))
            p.drawText(rect, align | Qt.AlignVCenter, fm.elidedText(line.text, Qt.ElideRight, rect.width()))
            return
        # word by word: each word fills with colour while it's sung
        pieces = [w.text for w in line.words]
        widths = [fm.horizontalAdvance(t) for t in pieces]
        total = sum(widths)
        x = (rect.left() if align == Qt.AlignLeft else rect.right() - total if align == Qt.AlignRight
             else rect.center().x() - total / 2)
        base = rect.center().y() + (fm.ascent() - fm.descent()) / 2
        for j, (text, wd) in enumerate(zip(pieces, widths)):
            w = line.words[j]
            s = w.start if w.start is not None else next(
                (v.start for v in reversed(line.words[:j]) if v.start is not None), line.start)
            e = line.word_end(j, nxt)
            e = e if e is not None else (s or 0) + 0.6
            s = s if s is not None else 0.0
            frac = 1.0 if self.t >= e else 0.0 if self.t <= s else (self.t - s) / max(e - s, 1e-3)
            p.setPen(_qc(theme.FAINT))
            p.drawText(QPointF(x, base), text)
            if frac > 0:
                p.save()
                p.setClipRect(QRectF(x, rect.top(), wd * frac, rect.height()))
                p.setPen(_qc(lit))
                p.drawText(QPointF(x, base), text)
                p.restore()
            x += wd
