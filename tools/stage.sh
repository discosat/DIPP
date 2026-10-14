#!/usr/bin/env bash
# Build a deployable, self-describing run-set.
#
#   tools/stage.sh <run-set-name> [config-manifest]
#
# Produces staging/<run-set>/ containing the stripped binaries that get
# deployed, the unstripped ones kept for symbols, the generated storage.vmem,
# the board-side runner, and a MANIFEST.json recording SHA-256 of every artifact
# alongside BUILD_ID and the submodule SHAs.
#
# Deliberately does NOT call ./configure: that does `rm -r builddir` and forces a
# full rebuild every time, which makes staging slow enough to discourage doing it
# per run-set -- and staging per run-set is the entire point.
set -euo pipefail

SDK=/opt/fus-imx-disco/5.15-kirkstone/environment-setup-armv8a-poky-linux
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULES="$(cd "$HERE/../tf-dipp-module" && pwd)"
STRIP=aarch64-linux-gnu-strip

# stage.sh <run-set-name> [config-manifest] --cost-model hints|<cost_model.json>
# The cost seed's source is required: "hints" (one constant line per slot at
# its YAML hint) or a fitted cost_model.json (tools/seed_cost_store.py).
COST_MODEL=""
POS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --cost-model) COST_MODEL="${2:?--cost-model needs hints or a cost_model.json}"; shift 2;;
        *) POS+=("$1"); shift;;
    esac
done
SET_NAME="${POS[0]:?usage: stage.sh <run-set-name> [config-manifest] --cost-model hints|<cost_model.json>}"
CONFIG_MANIFEST="${POS[1]:-$HERE/experiments/board/flight-config/manifest.yaml}"
[ -n "$COST_MODEL" ] || { echo "stage.sh: --cost-model hints|<cost_model.json> is required" >&2; exit 2; }
[ "$COST_MODEL" = hints ] || [ -f "$COST_MODEL" ] || { echo "stage.sh: cost model not found: $COST_MODEL" >&2; exit 2; }
OUT="$HERE/staging/$SET_NAME"

[ -f "$SDK" ] || { echo "SDK not found: $SDK" >&2; exit 1; }

# Capture the host interpreter BEFORE sourcing the SDK: the SDK puts its own
# python3 on PATH, and that one has no PyYAML, so storage.vmem generation fails
# with a bare ModuleNotFoundError halfway through staging.
HOST_PYTHON="$(command -v python3)"

echo "=== staging '$SET_NAME' ==="
rm -rf "$OUT"; mkdir -p "$OUT/symbols"

# --- build -----------------------------------------------------------------
# shellcheck disable=SC1090
set +u; . "$SDK"; set -u
echo "--- building daemon ---"
# BUILD_ID is computed by run_command() at meson CONFIGURE time, not build time,
# so a plain ninja leaves the binary reporting whatever identity it had when
# builddir was last set up. Observed 38 commits stale, which silently defeats
# reading the build back from the daemon's own startup banner. Reconfigure so
# the staged binary reports the truth.
meson setup --reconfigure "$HERE/builddir" "$HERE" >/dev/null
ninja -C "$HERE/builddir" >/dev/null
echo "--- building modules ---"
# libjxl.pc declares Requires.private: libhwy but the sysroot has no libhwy;
# the overlay states that hwy is statically linked in. See the overlay README.
PKG_CONFIG_PATH="$MODULES/tools/pkgconfig-overlay:${PKG_CONFIG_PATH:-}" \
    ninja -C "$MODULES/builddir" >/dev/null

# --- collect ---------------------------------------------------------------
cp "$HERE/builddir/dipp" "$OUT/"
cp "$HERE/builddir/dipp_ctl" "$OUT/"
cp "$MODULES/builddir/emulate_camera" "$OUT/"
cp "$MODULES/builddir"/*.so "$OUT/"
cp "$HERE/tests/board/run.sh" "$OUT/" 2>/dev/null || true
cp "$HERE/tests/board/feed_trace.sh" "$OUT/"
cp "$HERE/tools/envelope_sampler.sh" "$OUT/"
# Battery generation profiles and platform presets (run.sh --platform); the
# daemon refuses to start without them, so a set without them is not staged.
cp "$HERE"/analysis/power-profile/*.profile "$OUT/"
cp "$HERE"/experiments/platforms/*.env "$OUT/"

# Keep symbols before stripping: the stripped copy is what ships, but a
# disassembly-level question later needs the unstripped one, and rebuilding it
# from a different toolchain state is exactly the provenance hole Stage 0 closed.
for f in "$OUT"/dipp "$OUT"/dipp_ctl "$OUT"/emulate_camera "$OUT"/*.so; do
    cp "$f" "$OUT/symbols/$(basename "$f").debug"
    $STRIP --strip-all "$f"
done

echo "--- generating storage.vmem ---"
"$HOST_PYTHON" "$HERE/tools/make_storage_vmem.py" build "$CONFIG_MANIFEST" -o "$OUT/storage.vmem" >/dev/null

# The cost store is keyed by each slot's config hash, so the seed is rebuilt
# from the same manifest every time: a stale seed would silently miss.
echo "--- generating cost.seed ($COST_MODEL) ---"
if [ "$COST_MODEL" = hints ]; then SEED_SRC=(--hints); else SEED_SRC=(--model "$COST_MODEL"); fi
"$HOST_PYTHON" "$HERE/tools/seed_cost_store.py" --manifest "$CONFIG_MANIFEST" "${SEED_SRC[@]}" \
    -o "$OUT/cost.seed" >/dev/null
mv "$OUT/cost.seed.json" "$OUT/symbols/cost.seed.json"   # provenance, not deployed

# --- manifest --------------------------------------------------------------
BUILD_ID="$("$HERE/tools/build_id.sh")"
SUBMODULES="$(git -C "$HERE" submodule status --cached | awk '{gsub(/^[-+U ]/,"",$1); printf "%s=%s ", $2, $1}')"
MOD_HEAD="$(git -C "$MODULES" rev-parse HEAD)"
DIPP_HEAD="$(git -C "$HERE" rev-parse HEAD)"
DIRTY_DIPP="$(git -C "$HERE" status --porcelain | wc -l)"
DIRTY_MOD="$(git -C "$MODULES" status --porcelain | wc -l)"

{
    echo "{"
    echo "  \"run_set\": \"$SET_NAME\","
    echo "  \"staged_utc\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"build_id\": \"$BUILD_ID\","
    echo "  \"dipp_head\": \"$DIPP_HEAD\","
    echo "  \"modules_head\": \"$MOD_HEAD\","
    echo "  \"dipp_uncommitted\": $DIRTY_DIPP,"
    echo "  \"modules_uncommitted\": $DIRTY_MOD,"
    echo "  \"submodules\": \"$(echo "$SUBMODULES" | sed 's/ *$//')\","
    echo "  \"config_manifest\": \"$CONFIG_MANIFEST\","
    echo "  \"artifacts\": {"
    first=1
    for f in "$OUT"/*; do
        [ -f "$f" ] || continue
        [ "$(basename "$f")" = "MANIFEST.json" ] && continue
        [ $first -eq 0 ] && echo ","
        first=0
        printf '    "%s": {"sha256": "%s", "bytes": %s}' \
            "$(basename "$f")" "$(sha256sum "$f" | cut -d' ' -f1)" "$(stat -c%s "$f")"
    done
    echo
    echo "  }"
    echo "}"
} > "$OUT/MANIFEST.json"

if [ "$DIRTY_DIPP" -ne 0 ] || [ "$DIRTY_MOD" -ne 0 ]; then
    echo "WARNING: staging from a dirty tree (dipp=$DIRTY_DIPP modules=$DIRTY_MOD uncommitted)" >&2
    echo "         results from this run-set will not be reproducible from a commit." >&2
fi

tar czf "$OUT.tar.gz" -C "$OUT" --exclude=symbols .
echo "=== staged ==="
echo "  $OUT/          ($(ls "$OUT" | wc -l) files, symbols kept separately)"
echo "  $OUT.tar.gz    ($(stat -c%s "$OUT.tar.gz") bytes)"
echo "  build_id: $BUILD_ID"
