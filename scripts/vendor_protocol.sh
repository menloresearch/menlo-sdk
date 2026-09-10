#!/usr/bin/env bash
# Vendor (or verify) the generated asimov.io Python bindings from asimov-protocol.
#
#   scripts/vendor_protocol.sh v1.1.0          re-vendor at that tag, rewrite VENDORED.md
#   scripts/vendor_protocol.sh --check         re-fetch the tag named in VENDORED.md and
#                                              fail if the vendored tree differs from it
#                                              (ASIMOV_PROTOCOL_SRC=<checkout> skips the fetch)
#
# The SDK prefers an installed `asimov-protocol` package (one descriptor set per process);
# the vendored tree is what makes installing the wheel work without access to the
# protocol repository. The tag in VENDORED.md is the single source of the pin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/src/asimov_sdk/_vendor"
DEST="$VENDOR/asimov_protocol"
NOTE="$VENDOR/VENDORED.md"
REPO="https://github.com/menloresearch/asimov-protocol.git"

pinned_ref() { sed -nE 's/^- Ref: `([^`]+)`.*/\1/p' "$NOTE"; }

fetch() {  # $1=ref $2=dir
  git clone -q --depth 1 --branch "$1" "$REPO" "$2"
}

case "${1:?usage: vendor_protocol.sh <tag> | --check}" in
  --check)
    REF="$(pinned_ref)"; [ -n "$REF" ] || { echo "error: no Ref in $NOTE" >&2; exit 2; }
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    if [ -n "${ASIMOV_PROTOCOL_SRC:-}" ]; then
      # A checkout fetched by the caller (CI fetches it, then drops its credential).
      ln -s "$ASIMOV_PROTOCOL_SRC" "$TMP/proto"
    else
      fetch "$REF" "$TMP/proto"
    fi
    if diff -r -q --exclude=__pycache__ "$TMP/proto/gen/python/src/asimov_protocol" "$DEST" >/dev/null; then
      echo "OK: vendored bindings match asimov-protocol $REF ($(git -C "$TMP/proto" rev-parse --short HEAD))"
    else
      echo "DRIFT: vendored bindings differ from asimov-protocol $REF:" >&2
      diff -r -q --exclude=__pycache__ "$TMP/proto/gen/python/src/asimov_protocol" "$DEST" >&2 || true
      echo "run: scripts/vendor_protocol.sh $REF" >&2
      exit 1
    fi
    ;;
  *)
    REF="$1"
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    fetch "$REF" "$TMP/proto"
    COMMIT="$(git -C "$TMP/proto" rev-parse HEAD)"
    rm -rf "$DEST"; mkdir -p "$DEST"
    cp -R "$TMP/proto/gen/python/src/asimov_protocol/." "$DEST/"
    find "$DEST" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
    cat > "$NOTE" <<MD
# Vendored: asimov-protocol (generated Python bindings)

- Source: https://github.com/menloresearch/asimov-protocol \`gen/python/src/asimov_protocol\`
- Ref: \`$REF\` (commit \`$COMMIT\`)
- Vendored on: $(date -u +%Y-%m-%d) by \`scripts/vendor_protocol.sh\`

Do not hand-edit. \`scripts/vendor_protocol.sh --check\` verifies this tree against the ref
above; \`scripts/vendor_protocol.sh <tag>\` moves the pin. Bump \`PROTOCOL_VERSION\` in
\`robots.py\` only when the wire's \`protocol_version\` changes.
MD
    touch "$VENDOR/__init__.py"
    echo "vendored asimov-protocol @ $REF ($COMMIT) -> $DEST"
    ;;
esac
