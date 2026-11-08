"""Device identity, TLS certificate, pairing codes and the paired-device registry."""

import datetime
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..util import config_dir

# Crockford base32 without I, L, O, U: easy to read off a screen and type on a phone
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12              # 60 bits; the HMAC proof can't be brute-forced before the code expires
CODE_TTL = 300
MAX_ATTEMPTS = 5


def state_dir():
    d = config_dir() / "netsync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_private(path, data):
    """Write a file only the current user can read."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


class Identity:
    """This install's device id, name and TLS certificate (created on first use)."""

    def __init__(self, folder=None):
        self.folder = Path(folder or state_dir())
        self.cert_path = self.folder / "cert.pem"
        self.key_path = self.folder / "key.pem"
        info = self.folder / "device.json"
        try:
            data = json.loads(info.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not data.get("id"):
            data = {"id": uuid.uuid4().hex, "name": socket.gethostname() or "Music Tools"}
            self.folder.mkdir(parents=True, exist_ok=True)
            info.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.id, self.name = data["id"], data["name"]
        if not (self.cert_path.is_file() and self.key_path.is_file()):
            self._make_cert()
        self.fingerprint = cert_fingerprint(self.cert_path.read_bytes())

    def _make_cert(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Music Tools {self.id[:8]}")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .sign(key, hashes.SHA256()))
        _write_private(self.key_path, key.private_bytes(serialization.Encoding.PEM,
                                                        serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    def ssl_context(self):
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert_path, self.key_path)
        return ctx


def cert_fingerprint(cert):
    """SHA-256 of the DER certificate, lowercase hex. Accepts PEM or DER bytes."""
    if cert.startswith(b"-----BEGIN"):
        import ssl
        cert = ssl.PEM_cert_to_DER_cert(cert.decode("ascii"))
    return hashlib.sha256(cert).hexdigest()


# ------------------------------------------------------------------------ pairing


def new_code():
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def normalize_code(code):
    """Accept lowercase, spaces/dashes and the usual look-alikes when a code is typed by hand."""
    code = code.upper().replace("-", "").replace(" ", "")
    return code.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def format_code(code):
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _proof(code, role, fingerprint, nonce, device_id):
    msg = f"mt-pair-v1|{role}|{fingerprint.lower()}|{nonce}|{device_id}".encode()
    return hmac.new(normalize_code(code).encode(), msg, hashlib.sha256).hexdigest()


def client_proof(code, server_fingerprint, nonce, device_id):
    """What a client sends: binds the code to the certificate it actually sees."""
    return _proof(code, "client", server_fingerprint, nonce, device_id)


def server_proof(code, server_fingerprint, nonce, device_id):
    """What the server answers: shows the client it knows the code too."""
    return _proof(code, "server", server_fingerprint, nonce, device_id)


class PairingSession:
    """One code, valid for CODE_TTL seconds, usable once, closed after MAX_ATTEMPTS wrong proofs."""

    def __init__(self):
        self.code = new_code()
        self.expires = time.time() + CODE_TTL
        self.attempts = 0
        self.used = False

    @property
    def active(self):
        return not self.used and self.attempts < MAX_ATTEMPTS and time.time() < self.expires

    def check(self, proof, fingerprint, nonce, device_id):
        if not self.active:
            return False
        ok = hmac.compare_digest(proof, client_proof(self.code, fingerprint, nonce, device_id))
        if ok:
            self.used = True
        else:
            self.attempts += 1
        return ok


# ------------------------------------------------------------------------ paired devices


@dataclass
class Device:
    id: str
    name: str
    kind: str = "phone"            # phone | pc
    token_hash: str = ""
    paired: float = 0
    last_seen: float = 0
    last_ip: str = ""
    extra: dict = field(default_factory=dict)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Registry:
    """Paired devices, stored with only a hash of their token."""

    def __init__(self, path=None):
        self.path = Path(path or state_dir() / "devices.json")
        self._lock = threading.Lock()
        self.devices = {}
        try:
            for d in json.loads(self.path.read_text(encoding="utf-8")):
                dev = Device(**d)
                self.devices[dev.id] = dev
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        with self._lock:
            data = json.dumps([asdict(d) for d in self.devices.values()], indent=2)
        _write_private(self.path, data)

    def add(self, device_id, name, kind):
        """Pair (or re-pair) a device and return its new token. Only the hash is stored."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.devices[device_id] = Device(device_id, name[:80] or "Device", kind if kind in ("phone", "pc")
                                             else "phone", hash_token(token), paired=time.time())
        self.save()
        return token

    def remove(self, device_id):
        with self._lock:
            self.devices.pop(device_id, None)
        self.save()

    def authenticate(self, token):
        if not token:
            return None
        h = hash_token(token)
        with self._lock:
            for dev in self.devices.values():
                if hmac.compare_digest(dev.token_hash, h):
                    return dev
        return None

    def seen(self, dev, ip):
        dev.last_seen, dev.last_ip = time.time(), ip
