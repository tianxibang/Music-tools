"""Announcing the sync server on the local network (mDNS / DNS-SD), so phones find it without an IP."""

import socket

from zeroconf import IPVersion, ServiceInfo, Zeroconf

from . import PROTOCOL, SERVICE
from .server import local_addresses


class Advertiser:
    def __init__(self, server):
        self.server = server
        self.zc = None
        self.info = None

    def start(self):
        ident = self.server.identity
        addrs = local_addresses()
        if not addrs:
            raise RuntimeError("no local network address")
        # The TXT record only carries a short fingerprint: enough to tell PCs apart,
        # the phone still checks the full one during pairing.
        self.info = ServiceInfo(
            SERVICE, f"{ident.name} ({ident.id[:6]}).{SERVICE}",
            addresses=[socket.inet_aton(a) for a in addrs], port=self.server.port,
            properties={"id": ident.id, "name": ident.name, "fp": ident.fingerprint[:16], "v": str(PROTOCOL),
                        "pairing": "required" if self.server.require_pairing else "open"},
            server=f"{socket.gethostname().split('.')[0] or 'musictools'}.local.")
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.zc.register_service(self.info, allow_name_change=True)

    def stop(self):
        if self.zc:
            try:
                self.zc.unregister_service(self.info)
            finally:
                self.zc.close()
            self.zc = None
