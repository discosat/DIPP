#!/bin/bash
# J2K rungs, then the JXL re-verification, in one process.
#
# Deliberately sequential rather than two chained background waiters. The
# obvious "wait until the other sweep's process is gone" loop matches its OWN
# command line under pgrep -f and waits forever -- the same self-matching shape
# that made campaign.py's completion wait return on its own echo. One script,
# one serial console, no coordination needed.
set -u
cd "$(dirname "$0")/.." || exit 1
./tools/j2k_rungs.sh
./tools/enc_verify.sh
echo "=== ALL DONE ==="
