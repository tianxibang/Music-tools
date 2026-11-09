"""Python client for the sync protocol (used by the tests now, and by PC-to-PC sync later).

The server's certificate is self-signed, so instead of a CA we pin its SHA-256 fingerprint:
learned from the QR code, or checked through the pairing proof when the code was typed by hand.
"""

import hashlib
import hmac
import http.client
import json
import os
import secrets
import ssl
from urllib.parse import quote, urlencode

from .security import cert_fingerprint, client_proof, server_proof


class SyncError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, host, port, device_id, name="Device", kind="pc", fingerprint=None, token=None):
        self.host, self.port = host, port
        self.device_id, self.name, self.kind = device_id, name, kind
        self.fingerprint = fingerprint.lower() if fingerprint else None
        self.token = token

    def _connect(self, timeout=60):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # verified below by fingerprint instead
        conn = http.client.HTTPSConnection(self.host, self.port, context=ctx, timeout=timeout)
        conn.connect()
        seen = cert_fingerprint(conn.sock.getpeercert(binary_form=True))
        if self.fingerprint and not hmac.compare_digest(seen, self.fingerprint):
            conn.close()
            raise SyncError(0, "The PC's certificate changed. Someone may be intercepting, or the PC was "
                               "reinstalled: pair again if you trust it.")
        return conn, seen

    def request(self, method, path, body=None, headers=None, raw=False):
        conn, _ = self._connect()
        try:
            h = dict(headers or {})
            if self.token:
                h["Authorization"] = f"Bearer {self.token}"
            h["X-Device-Id"] = self.device_id
            if isinstance(body, dict):
                body = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
            conn.request(method, "/api/v1/" + path, body=body, headers=h)
            resp = conn.getresponse()
            data = resp.read()
            if resp.status >= 400:
                try:
                    msg = json.loads(data)["error"]
                except (ValueError, KeyError, TypeError):
                    msg = f"HTTP {resp.status}"
                raise SyncError(resp.status, msg)
            return data if raw else json.loads(data or b"{}")
        finally:
            conn.close()

    def info(self):
        return self.request("GET", "info")

    def pair(self, code):
        """Pair with a code from the PC. Returns the server info; sets token and fingerprint."""
        conn, seen = self._connect()
        conn.close()
        nonce = secrets.token_hex(16)
        res = self.request("POST", "pair", {"device_id": self.device_id, "name": self.name, "kind": self.kind,
                                            "nonce": nonce, "proof": client_proof(code, seen, nonce, self.device_id)})
        if not hmac.compare_digest(res.get("proof", ""), server_proof(code, seen, nonce, self.device_id)):
            raise SyncError(0, "The PC couldn't prove it knows the code; not pairing.")
        self.token, self.fingerprint = res["token"], seen
        return res

    def manifest(self, root="opus"):
        return self.request("GET", f"manifest?{urlencode({'root': root})}")["files"]

    def download(self, rel, dest, size=None, mtime=None, root="opus", progress=None):
        """Stream a file to dest via a temp file; checks the size and sets the PC's modification time."""
        conn, _ = self._connect(timeout=120)
        tmp = dest.with_name(f".{dest.name}.part")
        try:
            h = {"X-Device-Id": self.device_id}
            if self.token:
                h["Authorization"] = f"Bearer {self.token}"
            conn.request("GET", f"/api/v1/files/{quote(rel)}?{urlencode({'root': root})}", headers=h)
            resp = conn.getresponse()
            if resp.status >= 400:
                try:
                    msg = json.loads(resp.read())["error"]
                except (ValueError, KeyError, TypeError):
                    msg = f"HTTP {resp.status}"
                raise SyncError(resp.status, msg)
            dest.parent.mkdir(parents=True, exist_ok=True)
            got = 0
            with open(tmp, "wb") as f:
                while chunk := resp.read(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got)
            if size is not None and got != size:
                raise SyncError(0, f"Download of {rel} was incomplete")
            if mtime is not None:
                os.utime(tmp, (mtime, mtime))
            os.replace(tmp, dest)
        finally:
            conn.close()
            if tmp.exists():
                tmp.unlink()

    def upload(self, path):
        """Send a music file to the PC's inbox, streamed from disk."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                h.update(chunk)
        q = urlencode({"name": path.name, "sha256": h.hexdigest()})
        with open(path, "rb") as f:
            return self.request("PUT", f"inbox?{q}", body=f, headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size)})

    def edit_tags(self, rel, changes, root="library", force=False):
        """changes: {field: (old, new)}. Returns {"applied": [...], "conflicts": {field: value on the PC}}."""
        return self.request("POST", "library/tags", {
            "path": rel, "root": root, "force": force,
            "changes": {k: {"old": old, "new": new} for k, (old, new) in changes.items()}})

    def inbox(self, item_id=None):
        return self.request("GET", "inbox" + (f"/{item_id}" if item_id else ""))

    def acknowledge(self, item_id):
        return self.request("DELETE", f"inbox/{item_id}")

    def unpair(self):
        return self.request("POST", "unpair")
