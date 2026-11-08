"""Sync over the home network: the desktop app serves its library to paired phones (and later other PCs).

Protocol summary (all JSON over HTTPS, see server.py for the routes):
- Every install has a device id and a self-signed TLS certificate. Traffic is always encrypted.
- Pairing (when required): the PC shows a one-time code (also as a QR code with its certificate
  fingerprint). The client proves it knows the code with an HMAC bound to the certificate it sees,
  so a man in the middle can't relay the pairing, and gets a bearer token for later requests.
- "Open" mode skips pairing for people who don't want the hassle; requests still have to come
  from the local network and traffic is still encrypted.
"""

PORT = 47321
SERVICE = "_musictools._tcp.local."
PROTOCOL = 1
