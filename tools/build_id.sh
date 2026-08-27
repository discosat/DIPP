#!/bin/sh
# Emit a single-line build identifier for -DBUILD_ID.
# Format: <git-describe> [<submodule>=<sha8> ...]
# Degrades to "nogit" rather than failing, so builds never break on this.
set -u
cd "$(dirname "$0")/.." || exit 0

desc=$(git describe --always --dirty --tags 2>/dev/null) || desc=""
[ -n "$desc" ] || desc="nogit"

subs=$(git submodule status --cached 2>/dev/null \
       | awk '{gsub(/^[-+U ]/,"",$1); printf "%s=%s ", $2, substr($1,1,8)}' \
       | sed 's/ *$//')

if [ -n "$subs" ]; then
    printf '%s [%s]' "$desc" "$subs"
else
    printf '%s' "$desc"
fi
