"""Audio for the lyrics editor: a song decoded into memory, played, slowed down and scrubbed.

Normal playback and scrubbing read the decoded samples directly, so the position is exact. Slowed-down
playback streams from ffmpeg's atempo filter, which keeps the pitch.
"""

import subprocess
import threading
import time

import numpy as np
from PySide6.QtCore import QIODevice, QObject, Signal
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

from ..core.util import NO_WINDOW, find_tool

SR = 44100
FRAME = 4                   # bytes per stereo 16-bit frame
PEAK_BIN = 441              # waveform resolution: 10 ms
GRAIN = int(SR * 0.07)      # how much one scrub step plays
FADE = int(SR * 0.004)
BUFFER = 0.1                # seconds queued in the audio device


class Song:
    def __init__(self, path, pcm):
        self.path, self.pcm = path, pcm
        self.frames = len(pcm)
        self.duration = self.frames / SR
        self.peaks = _peaks(pcm)


def decode(path):
    """The whole song as 44.1 kHz stereo 16-bit samples (needs ffmpeg)."""
    exe = find_tool("ffmpeg")
    if not exe:
        raise RuntimeError("Playing songs needs ffmpeg (see Settings → External tools).")
    p = subprocess.run([exe, "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-f", "s16le",
                        "-ac", "2", "-ar", str(SR), "-"], capture_output=True, creationflags=NO_WINDOW)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or "ffmpeg failed")
    data = p.stdout[:len(p.stdout) // FRAME * FRAME]
    return Song(path, np.frombuffer(data, dtype=np.int16).reshape(-1, 2))


def _peaks(pcm):
    n = len(pcm) // PEAK_BIN
    if not n:
        return np.zeros(1, dtype=np.float32)
    rows = pcm[:n * PEAK_BIN].reshape(n, PEAK_BIN * 2)
    peak = np.maximum(rows.max(axis=1).astype(np.int32), -rows.min(axis=1).astype(np.int32))
    out = peak.astype(np.float32) / 32768
    top = float(np.percentile(out, 99.5)) or 1.0
    return np.clip(out / top, 0, 1)


def _tempo_filter(rate):
    # atempo goes down to 0.5 per stage
    stages = []
    while rate < 0.5:
        stages.append("atempo=0.5")
        rate /= 0.5
    stages.append(f"atempo={rate:g}")
    return ",".join(stages)


class _Feed(QIODevice):
    """What the audio device pulls samples from."""

    def __init__(self, player):
        super().__init__()
        self.player = player

    def readData(self, maxlen):
        return self.player._pull(maxlen)

    def writeData(self, data):
        return 0

    def bytesAvailable(self):
        return 1 << 16

    def isSequential(self):
        return True


class _Stream:
    """ffmpeg slowing the song down from a position, read on a thread into a buffer."""

    def __init__(self, path, start, rate):
        exe = find_tool("ffmpeg")
        self.proc = subprocess.Popen([exe, "-nostdin", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(path),
                                      "-map", "0:a:0", "-af", _tempo_filter(rate), "-f", "s16le", "-ac", "2",
                                      "-ar", str(SR), "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.done = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            with self.lock:
                full = len(self.buf) > SR * FRAME * 2
            if full:
                time.sleep(0.05)
                continue
            chunk = self.proc.stdout.read(16384)
            if not chunk:
                break
            with self.lock:
                self.buf += chunk
        self.done = True

    def take(self, n):
        with self.lock:
            out = bytes(self.buf[:n])
            del self.buf[:n]
        return out

    def ready(self, n):
        with self.lock:
            return len(self.buf) >= n or self.done

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass


class Player(QObject):
    """Plays a Song at a speed, or short grains of it while scrubbing. Times are in seconds of the song."""
    stateChanged = Signal(bool)     # playing

    def __init__(self, parent=None):
        super().__init__(parent)
        self.song = None
        self.rate = 1.0
        self.mode = "stop"           # stop | play | scrub
        self._pos = 0.0              # where playback (re)started, or the position while stopped
        self._frame = 0              # next source frame to hand out (normal speed)
        self._out = 0                # frames handed to the device since it started
        self._stream = None
        self._grain_frame = self._grain_left = 0
        self._sink = None
        self._feed = _Feed(self)
        self._feed.open(QIODevice.ReadOnly)
        self.volume = 1.0

    # --- public

    def load(self, song):
        self.stop()
        self.song = song
        self._pos = 0.0

    @property
    def playing(self):
        return self.mode == "play"

    def position(self):
        if self.mode != "play" or self._sink is None:
            return self._pos
        queued = max(0, (self._sink.bufferSize() - self._sink.bytesFree()) // FRAME)
        played = max(0, self._out - queued)
        return min(self._pos + played * self.rate / SR, self.duration)

    @property
    def duration(self):
        return self.song.duration if self.song else 0.0

    def play(self, start=None):
        if not self.song:
            return
        pos = self.position() if start is None else start
        if pos >= self.duration - 0.05:
            pos = 0.0
        self._halt()
        self._pos = max(0.0, pos)
        self._frame = int(self._pos * SR)
        self._out = 0
        if self.rate != 1.0:
            self._stream = _Stream(self.song.path, self._pos, self.rate)
            end = time.time() + 1.5
            while not self._stream.ready(int(SR * FRAME * 0.15)) and time.time() < end:
                time.sleep(0.01)
        self.mode = "play"
        self._start_sink()
        self.stateChanged.emit(True)

    def pause(self):
        if self.mode == "play":
            self._pos = self.position()
        self._halt()
        self.stateChanged.emit(False)

    def stop(self):
        self.pause()

    def toggle(self):
        self.pause() if self.playing else self.play()

    def seek(self, t):
        t = min(max(0.0, t), self.duration)
        if self.playing:
            self.play(t)
        else:
            self._pos = t

    def set_rate(self, rate):
        if rate == self.rate:
            return
        playing = self.playing
        pos = self.position()
        self.rate = rate
        if playing:
            self.play(pos)

    def scrub(self, t):
        """Play a short grain at t (call it as the mouse moves)."""
        if not self.song:
            return
        t = min(max(0.0, t), self.duration)
        if self.mode != "scrub":
            was_playing = self.playing
            self._halt()
            self._resume_after_scrub = was_playing
            self.mode = "scrub"
            self._start_sink()
        self._pos = t
        self._grain_frame = int(t * SR)
        self._grain_left = GRAIN

    def end_scrub(self, resume=None):
        """Stop scrubbing; playback carries on from the scrub position if it was playing before."""
        if self.mode != "scrub":
            return
        self._halt()
        if resume if resume is not None else getattr(self, "_resume_after_scrub", False):
            self.play(self._pos)
        else:
            self.stateChanged.emit(False)

    def shutdown(self):
        self._halt()

    # --- device

    def _start_sink(self):
        if self._sink is None:
            fmt = QAudioFormat()
            fmt.setSampleRate(SR)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.Int16)
            self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
            self._sink.setBufferSize(int(SR * BUFFER) * FRAME)
        self._sink.setVolume(self.volume)
        self._sink.start(self._feed)

    def _halt(self):
        self.mode = "stop"
        if self._sink is not None and self._sink.state() != QAudio.StoppedState:
            self._sink.stop()
        if self._stream:
            self._stream.close()
            self._stream = None

    def _pull(self, maxlen):
        frames = max(0, min(maxlen // FRAME, 2048))
        if not frames or not self.song:
            return b""
        if self.mode == "play":
            if self._stream:
                data = self._stream.take(frames * FRAME)
                if not data and self._stream.done:
                    return bytes(frames * FRAME)
            else:
                data = self.song.pcm[self._frame:self._frame + frames].tobytes()
                self._frame += len(data) // FRAME
            self._out += len(data) // FRAME
            return data + bytes(frames * FRAME - len(data))   # past the end: silence
        if self.mode == "scrub" and self._grain_left > 0:
            n = min(frames, self._grain_left)
            chunk = self.song.pcm[self._grain_frame:self._grain_frame + n].astype(np.float32)
            done = GRAIN - self._grain_left
            idx = np.arange(done, done + len(chunk))
            env = np.minimum(1.0, np.minimum(idx / FADE, (GRAIN - idx) / FADE))[:, None]
            self._grain_frame += len(chunk)
            self._grain_left -= n
            out = (chunk * env).astype(np.int16).tobytes()
            return out + bytes(frames * FRAME - len(out))
        return bytes(frames * FRAME)
