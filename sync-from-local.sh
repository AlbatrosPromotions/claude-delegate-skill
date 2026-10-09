#!/usr/bin/env bash
# sync-from-local.sh — lokal ~/.claude dagi (manba) delegate skill va agentlarni
# shu repo'ga ko'chiradi. /delegate-retro skill'ni o'zgartirganidan keyin ishlating:
#   bash sync-from-local.sh && git add -A && git commit -m "sync from ~/.claude" && git push
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
for p in skills/delegate skills/delegate-retro; do
  rm -rf "$REPO_DIR/$p"; mkdir -p "$(dirname "$REPO_DIR/$p")"
  cp -R "$SRC/$p" "$REPO_DIR/$p"
done
for a in haiku-tester sonnet-coder sonnet-editor sonnet-scout; do
  cp "$SRC/agents/$a.md" "$REPO_DIR/agents/$a.md"
done
find "$REPO_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
git -C "$REPO_DIR" status --short
