# Security policy

Music Tools runs a sync server on your home network, so security problems matter. Thanks for helping.

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Instead, report it privately through
[GitHub's private vulnerability reporting](https://github.com/tianxibang/Music-tools/security/advisories/new)
(the **Security** tab → **Report a vulnerability**).

Include what you found, how to reproduce it, and which version you used. You'll get an answer as soon as I can
manage; this is a hobby project, so please allow a few days. Once a fix is released, you're welcome to be
credited in the release notes.

## Supported versions

Only the latest release gets security fixes. Please check that the problem still exists there.

## Scope

In scope, for example:

- the sync server in the desktop app (`musictools/core/netsync/`): pairing, authentication, the TLS certificate
  pinning, and anything that lets a device on the network read, write or delete files it shouldn't
- the phone app's network code (`android/app/src/main/java/.../net/`) and how it handles what the PC sends
- the release builds and the GitHub workflow that produces them

Out of scope:

- **open mode** behaving as documented: with "Require pairing" turned off, any device on the local network may sync
- attacks that need the PC or an unlocked phone in hand, or root/admin access to either
- problems in third-party tools Music Tools calls (ffmpeg, opusenc, adb); please report those upstream
