"""Job context: how long-running core functions report progress and get cancelled.

Core code never touches the UI. It receives a Context and calls log/progress/ask on it;
the UI subclasses Context to forward those calls as Qt signals.
"""

import subprocess
import threading

from .util import NO_WINDOW


class Cancelled(Exception):
    pass


class Context:
    def __init__(self):
        self.cancel_event = threading.Event()
        self._procs = set()
        self._lock = threading.Lock()

    # --- overridden by the UI
    def log(self, msg, level="info"):
        print(msg)

    def progress(self, done, total, text=""):
        pass

    def ask(self, title, msg):
        return False

    # --- cancellation
    @property
    def cancelled(self):
        return self.cancel_event.is_set()

    def check(self):
        if self.cancel_event.is_set():
            raise Cancelled

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            for p in self._procs:
                try:
                    p.kill()
                except OSError:
                    pass

    def popen(self, cmd, binary=False):
        """Run cmd, killable via cancel(). Returns (returncode, stdout, stderr)."""
        kw = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW, **kw)
        with self._lock:
            self._procs.add(p)
        try:
            out, err = p.communicate()
        finally:
            with self._lock:
                self._procs.discard(p)
        if binary:
            err = err.decode("utf-8", "replace")
        return p.returncode, out, err
