#!/usr/bin/env bash
# Publish the local delegate skills and agents to the install repo that servers install them from.
#
#   publish.sh ["commit message"]      default message: "sync from ~/.claude <YYYY-MM-DD>"
#
# Clones the repo (depth 1) into a temp dir, mirrors skills/delegate, skills/delegate-retro and four agents from
# $CLAUDE_CONFIG_DIR (default ~/.claude) into it, then commits and pushes when anything changed.
set -euo pipefail

REPO_URL=git@github.com:AlbatrosPromotions/claude-delegate-skill.git
SRC=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
MSG=${1:-"sync from ~/.claude $(date +%F)"}
SKILLS=(delegate delegate-retro)
AGENTS=(haiku-tester sonnet-coder sonnet-editor sonnet-scout)

command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 1; }
for s in "${SKILLS[@]}"; do [ -d "$SRC/skills/$s" ] || { echo "missing $SRC/skills/$s" >&2; exit 1; }; done
for a in "${AGENTS[@]}"; do [ -f "$SRC/agents/$a.md" ] || { echo "missing $SRC/agents/$a.md" >&2; exit 1; }; done

CLONE=$(mktemp -d)
trap 'rm -rf "$CLONE"' EXIT
git clone --quiet --depth 1 "$REPO_URL" "$CLONE"

mkdir -p "$CLONE/skills" "$CLONE/agents"
for s in "${SKILLS[@]}"; do
  # --delete-excluded: files that are never published (metrics.tsv, backups, caches) are also removed from the repo
  rsync -a --delete --delete-excluded --exclude __pycache__ --exclude '*.bak.*' --exclude metrics.tsv \
    "$SRC/skills/$s/" "$CLONE/skills/$s/"
done
for a in "${AGENTS[@]}"; do cp "$SRC/agents/$a.md" "$CLONE/agents/$a.md"; done

if [ -z "$(git -C "$CLONE" status --porcelain)" ]; then
  echo "nothing to publish"
  exit 0
fi
git -C "$CLONE" add -A
git -C "$CLONE" commit --quiet -m "$MSG"
git -C "$CLONE" push --quiet origin HEAD
echo "published $(git -C "$CLONE" rev-parse HEAD) to $REPO_URL"
