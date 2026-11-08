#!/usr/bin/env bash
# Runs the Android app's network client against a real desktop sync server in a throwaway config.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'kill $server 2>/dev/null || true; rm -rf "$tmp"' EXIT
mkdir -p "$tmp/config/music-tools" "$tmp/lib" "$tmp/opus"
port=47398
cat > "$tmp/config/music-tools/settings.json" <<JSON
{"library": "$tmp/lib", "opus": "$tmp/opus", "net_port": $port}
JSON
ffmpeg -nostdin -loglevel error -f lavfi -i sine=frequency=330:duration=3 -metadata title="Interop" \
    -metadata artist="Phone" -metadata album="Test" "$tmp/song.flac"
cd "$root"
XDG_CONFIG_HOME="$tmp/config" .venv/bin/python -u -m musictools --serve --pair > "$tmp/server.log" 2>&1 &
server=$!
for _ in $(seq 50); do grep -q "Pairing code" "$tmp/server.log" && break; sleep 0.2; done
code="$(grep -o '[0-9A-Z]\{4\}-[0-9A-Z]\{4\}-[0-9A-Z]\{4\}' "$tmp/server.log")"
cd android
MT_HOST=127.0.0.1 MT_PORT=$port MT_CODE="$code" MT_FLAC="$tmp/song.flac" \
    ./gradlew --console=plain -q testDebugUnitTest --tests '*ServerInteropTest*' --rerun-tasks
echo "interop OK"
