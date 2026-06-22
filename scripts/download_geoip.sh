#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-data/GeoLite2-City.mmdb}"
PRIMARY_URL="https://git.io/GeoLite2-City.mmdb"
FALLBACK_URL="https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb"
DB_DIR="$(dirname "$DB_PATH")"
TMP_FILE="${DB_PATH}.tmp"

mkdir -p "$DB_DIR"
chmod 755 "$DB_DIR"

if [ -s "$DB_PATH" ]; then
  chmod 644 "$DB_PATH"
  echo "GeoIP database already exists: $DB_PATH"
  exit 0
fi

download() {
  url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 15 --output "$TMP_FILE" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$TMP_FILE" "$url"
  else
    echo "Missing downloader: install curl or wget" >&2
    return 127
  fi
}

rm -f "$TMP_FILE"
if ! download "$PRIMARY_URL"; then
  rm -f "$TMP_FILE"
  download "$FALLBACK_URL"
fi

test -s "$TMP_FILE"
mv "$TMP_FILE" "$DB_PATH"
chmod 644 "$DB_PATH"

echo "GeoIP database downloaded: $DB_PATH"
