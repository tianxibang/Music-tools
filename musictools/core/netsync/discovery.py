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


def browse(seconds=3.0):
    """Music Tools PCs announcing themselves on the network: [{name, id, host, port, fp, pairing}]."""
    import time

    from zeroconf import ServiceBrowser

    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if not info or not info.parsed_addresses():
                return
            props = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
            if props.get("v") != str(PROTOCOL):
                return
            found[name] = {"name": props.get("name", name), "id": props.get("id", ""), "host": info.parsed_addresses()[0],
                           "port": info.port, "fp": props.get("fp", ""), "pairing": props.get("pairing", "required")}

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            found.pop(name, None)

    zc = Zeroconf(ip_version=IPVersion.V4Only)
    try:
        ServiceBrowser(zc, SERVICE, Listener())
        time.sleep(seconds)
    finally:
        zc.close()
    return list(found.values())
