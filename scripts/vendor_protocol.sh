#!/usr/bin/env bash
# Re-vendor the generated asimov.io Python bindings from asimov-protocol at a tag.
#   scripts/vendor_protocol.sh v1.1.0
# The SDK prefers an installed `asimov-protocol` package (same descriptors, one copy);
# the vendored tree is the fallback that makes `pip install asimov-sdk` work anywhere.
set -euo pipefail
REF="${1:?usage: vendor_protocol.sh <tag-or-commit>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/src/asimov_sdk/_vendor/asimov_protocol"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git clone -q --depth 1 --branch "$REF" https://github.com/menloresearch/asimov-protocol.git "$TMP/proto"
COMMIT="$(git -C "$TMP/proto" rev-parse HEAD)"
rm -rf "$DEST"; mkdir -p "$DEST"
cp -R "$TMP/proto/gen/python/src/asimov_protocol/." "$DEST/"
cat > "$ROOT/src/asimov_sdk/_vendor/VENDORED.md" <<MD
# Vendored: asimov-protocol (generated Python bindings)

- Source: https://github.com/menloresearch/asimov-protocol \`gen/python/src/asimov_protocol\`
- Ref: \`$REF\` (commit \`$COMMIT\`)
- Vendored on: $(date -u +%Y-%m-%d) by \`scripts/vendor_protocol.sh\`

Do not hand-edit. Re-run the script to move to a new tag; bump \`PROTOCOL_VERSION\` in
\`robots.py\` only when the wire's \`protocol_version\` changes.
MD
touch "$ROOT/src/asimov_sdk/_vendor/__init__.py"
echo "vendored asimov-protocol @ $REF ($COMMIT) -> $DEST"
