#!/bin/bash
# Produce a checksum for the build receipt; add a detached signature when a
# release key is supplied. No key is stored in this repository.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEV="${KNIX_DEV_DIR:-$(cd "$ROOT/.." && pwd)/knix-m0}"
RECEIPT="$DEV/BUILD-RECEIPT.json"

if [ ! -f "$RECEIPT" ]; then
  echo "missing receipt: $RECEIPT" >&2
  exit 1
fi

openssl dgst -sha256 -out "$DEV/BUILD-RECEIPT.json.sha256" "$RECEIPT"
echo "checksum: $DEV/BUILD-RECEIPT.json.sha256"

if [ -n "${KNIX_SIGNING_KEY:-}" ]; then
  openssl dgst -sha256 -sign "$KNIX_SIGNING_KEY" \
    -out "$DEV/BUILD-RECEIPT.json.sig" "$RECEIPT"
  echo "signature: $DEV/BUILD-RECEIPT.json.sig"
else
  echo "no KNIX_SIGNING_KEY; checksum only (dev preview)"
fi
