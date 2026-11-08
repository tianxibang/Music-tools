"""The sync server: HTTPS on the local network, used by the phone app.

Routes (JSON unless noted, everything under /api/v1):
  GET    /info                  no auth: name, id, fingerprint, whether pairing is required
  POST   /pair                  pairing with a one-time code, returns a token
  POST   /unpair                a device removes itself
  GET    /manifest              files of the Opus export: [{path, size, mtime}]
  GET    /files/<path>          download one export file (binary, supports Range)
  PUT    /inbox?name=&sha256=   upload a FLAC for the library (binary body)
  GET    /inbox                 this device's inbox items
  GET    /inbox/<id>            one item: state, opus_path when ready
  DELETE /inbox/<id>            acknowledge (the phone has the Opus)
"""

import ipaddress
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from ..util import APP_VERSION
from . import PORT, PROTOCOL
from .library import Inbox, manifest, safe_join
from .security import Identity, PairingSession, Registry, server_proof

MAX_JSON = 64 * 1024
DEVICE_ID = re.compile(r"^[0-9a-f]{16,64}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def is_local(ip):
    """Private, loopback or link-local address (what a home network uses)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_private or addr.is_loopback or addr.is_link_local


def local_addresses():
    """IPv4 addresses of this machine on private networks, best guess first."""
    found = []
    try:  # the address used for outgoing traffic is almost always the right one
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        import ifaddr
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                if isinstance(ip.ip, str) and not ip.ip.startswith("127.") and is_local(ip.ip):
                    found.append(ip.ip)
    except Exception:
        pass
    return list(dict.fromkeys(ip for ip in found if is_local(ip) and not ip.startswith("127.")))


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, sync):
        self.sync = sync
        super().__init__(addr, handler)

    def get_request(self):
        sock, addr = super().get_request()
        # TLS handshake happens in the handler thread, so a slow client can't block accept()
        return self.sync.ssl.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


class SyncServer:
    def __init__(self, settings, log=None, on_change=None, port=None):
        self.settings = settings
        self.log = log or (lambda msg, level="info": print(msg, flush=True))
        self.on_change = on_change or (lambda what: None)
        self.port = port if port is not None else settings.get("net_port", PORT)
        self.identity = Identity()
        self.ssl = self.identity.ssl_context()
        self.registry = Registry()
        self.inbox = None
        self.pairing = None
        self.httpd = None
        self.discovery = None

    @property
    def require_pairing(self):
        return self.settings.get("net_require_pairing", True)

    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        if self.httpd:
            return
        self.inbox = Inbox(self.settings, self.log)
        self.httpd = _HTTPServer(("0.0.0.0", self.port), _Handler, self)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.log(f"Sync server running on port {self.port} "
                 f"({'pairing required' if self.require_pairing else 'open: any device on this network'})", "ok")
        try:
            from .discovery import Advertiser
            self.discovery = Advertiser(self)
            self.discovery.start()
        except Exception as e:  # discovery is a convenience; connecting by address still works
            self.discovery = None
            self.log(f"Network discovery unavailable ({e}); connect by IP address instead.", "warn")
        self.on_change("server")

    def stop(self):
        if self.discovery:
            self.discovery.stop()
            self.discovery = None
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.inbox:
            self.inbox.stop()
            self.inbox = None
        self.pairing = None
        self.log("Sync server stopped.")
        self.on_change("server")

    def start_pairing(self):
        self.pairing = PairingSession()
        return self.pairing

    def pairing_uri(self):
        """What the QR code holds: everything the phone needs, including the certificate to pin."""
        hosts = ",".join(local_addresses()) or "127.0.0.1"
        return (f"musictools://pair?v={PROTOCOL}&h={hosts}&p={self.port}&fp={self.identity.fingerprint}"
                f"&c={self.pairing.code}&id={self.identity.id}")

    def info(self):
        return {"app": "music-tools", "version": APP_VERSION, "protocol": PROTOCOL, "id": self.identity.id,
                "name": self.identity.name, "fingerprint": self.identity.fingerprint,
                "pairing": "required" if self.require_pairing else "open"}


class _Handler(BaseHTTPRequestHandler):
    server_version = "MusicTools"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60

    @property
    def sync(self):
        return self.server.sync

    def log_message(self, fmt, *args):  # quiet: the app log shows what matters
        pass

    def handle(self):
        try:
            self.connection.do_handshake()
            super().handle()
        except (OSError, ValueError):  # failed handshake, dropped or timed-out connection
            pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # --- plumbing

    def _dispatch(self, method):
        try:
            if not is_local(self.client_address[0]):
                raise ApiError(403, "Only devices on the local network can connect")
            url = urlsplit(self.path)
            path = unquote(url.path)
            if not path.startswith("/api/v1/"):
                raise ApiError(404, "Not found")
            parts = path[len("/api/v1/"):].split("/", 1)
            route, rest = parts[0], parts[1] if len(parts) > 1 else ""
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if (method, route) == ("GET", "info"):
                return self._json(self.sync.info())
            if (method, route) == ("POST", "pair"):
                return self._pair()
            device = self._auth()
            handler = {("POST", "unpair"): self._unpair, ("GET", "manifest"): self._manifest,
                       ("GET", "files"): self._file, ("PUT", "inbox"): self._upload,
                       ("GET", "inbox"): self._inbox_get, ("DELETE", "inbox"): self._inbox_ack}.get((method, route))
            if not handler:
                raise ApiError(404, "Not found")
            handler(device, rest, query)
        except ApiError as e:
            self._json({"error": str(e)}, e.status)
        except (ConnectionError, TimeoutError):
            self.close_connection = True
        except Exception as e:
            self.sync.log(f"Sync server error: {e}", "error")
            try:
                self._json({"error": "Internal error"}, 500)
            except OSError:
                self.close_connection = True

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _length(self):
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise ApiError(411, "Content-Length required") from None
        if n < 0:
            raise ApiError(400, "Bad Content-Length")
        return n

    def _body(self):
        n = self._length()
        if n > MAX_JSON:
            raise ApiError(413, "Request too large")
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "Invalid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Invalid JSON")
        return data

    def _auth(self):
        """The calling device's id. Paired devices use their token; open mode accepts a declared id."""
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        dev = self.sync.registry.authenticate(token)
        if dev:
            self.sync.registry.seen(dev, self.client_address[0])
            return dev.id
        if self.sync.require_pairing:
            raise ApiError(401, "Not paired with this PC")
        device_id = self.headers.get("X-Device-Id", "").lower()
        if not DEVICE_ID.match(device_id):
            raise ApiError(400, "X-Device-Id header required")
        return device_id

    # --- routes

    def _pair(self):
        data = self._body()
        session = self.sync.pairing
        device_id = str(data.get("device_id", "")).lower()
        nonce = str(data.get("nonce", ""))
        if not DEVICE_ID.match(device_id) or not re.fullmatch(r"[0-9a-f]{32,64}", nonce):
            raise ApiError(400, "Bad pairing request")
        if not session or not session.active:
            raise ApiError(403, "No pairing in progress. Click “Pair a device” on the PC first.")
        fp = self.sync.identity.fingerprint
        if not session.check(str(data.get("proof", "")), fp, nonce, device_id):
            time.sleep(1)  # slow down guessing on top of the attempt limit
            left = max(0, 5 - session.attempts)
            raise ApiError(403, f"Wrong code ({left} tries left)" if left else "Too many wrong codes; start again")
        name = str(data.get("name", "")).strip() or "Device"
        token = self.sync.registry.add(device_id, name, str(data.get("kind", "phone")))
        self.sync.registry.seen(self.sync.registry.devices[device_id], self.client_address[0])
        self.sync.registry.save()
        self.sync.pairing = None
        self.sync.log(f"Paired with {name}.", "ok")
        self.sync.on_change("devices")
        self._json({"token": token, "proof": server_proof(session.code, fp, nonce, device_id),
                    **self.sync.info()})

    def _unpair(self, device, rest, query):
        self.sync.registry.remove(device)
        self.sync.on_change("devices")
        self._json({"ok": True})

    def _manifest(self, device, rest, query):
        self._json({"files": manifest(self.sync.settings.opus), "generated": int(time.time())})

    def _file(self, device, rest, query):
        root = self.sync.settings.opus
        path = safe_join(root, rest)
        if not path or not path.is_file():
            raise ApiError(404, "No such file")
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
        if m:
            start = int(m.group(1))
            end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _upload(self, device, rest, query):
        if rest:
            raise ApiError(404, "Not found")
        n = self._length()
        try:
            item = self.sync.inbox.receive(device, query.get("name", ""), query.get("sha256", ""), n, self.rfile)
        except ValueError as e:
            self.close_connection = True  # the body may be half-read
            raise ApiError(400, str(e)) from None
        self.sync.on_change("inbox")
        self._json(_item(item))

    def _inbox_get(self, device, rest, query):
        if not rest:
            return self._json({"items": [_item(i) for i in self.sync.inbox.list(device)]})
        item = self.sync.inbox.get(rest, device)
        if not item:
            raise ApiError(404, "No such item")
        self._json(_item(item))

    def _inbox_ack(self, device, rest, query):
        if not self.sync.inbox.acknowledge(rest, device):
            raise ApiError(404, "No such item")
        self.sync.on_change("inbox")
        self._json({"ok": True})


def _item(item):
    return {"id": item.id, "name": item.name, "state": item.state, "detail": item.detail,
            "library_path": item.library_path, "opus_path": item.opus_path}

