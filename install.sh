#!/usr/bin/env bash
# install.sh — `delegate` skill, `delegate-retro` skill va 4 ta agentni
# shu repo'dan Claude Code config katalogiga (~/.claude) o'rnatadi.
#
#   git clone git@github.com:AlbatrosPromotions/claude-delegate-skill.git ~/claude-delegate-skill
#   bash ~/claude-delegate-skill/install.sh [--deny] [--hooks]
#
# --deny  : references/settings-deny.json dagi taqiq qoidalarini ~/.claude/settings.json ga qo'shadi
#           (ma'lumotni yo'q qiluvchi buyruqlar: DB reset, force push, git reset --hard ...).
# --hooks : references/settings-hooks.json dagi o'lchov hook'larini qo'shadi (har sessiya narxini avtomatik
#           loglaydi, har subagent narxini lead'ga ko'rsatadi).
# Yangilash:  git -C ~/claude-delegate-skill pull && bash ~/claude-delegate-skill/install.sh
# Yangilanish taklifi: --hooks bilan o'rnatilgan mashinada SessionStart hook kuniga bir marta (fonda, git ls-remote)
#           repo HEAD ni o'rnatilgan commit bilan solishtiradi; yangisi bo'lsa Claude sessiya boshida yangilashni taklif qiladi.
# Claude boshqa katalogdan config o'qisa:  CLAUDE_CONFIG_DIR=/path bash install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
APPLY_DENY=0
APPLY_HOOKS=0
FLAGS=""
for arg in "$@"; do
  case "$arg" in
    --deny) APPLY_DENY=1; FLAGS="$FLAGS --deny" ;;
    --hooks) APPLY_HOOKS=1; FLAGS="$FLAGS --hooks" ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "noma'lum parametr: $arg" >&2; exit 1 ;;
  esac
done

for p in skills/delegate skills/delegate-retro agents; do
  [ -d "$REPO_DIR/$p" ] || { echo "XATO: $REPO_DIR/$p topilmadi (repo to'liq clone qilinganmi?)" >&2; exit 1; }
done

echo "==> Claude config katalogi: $CLAUDE_DIR"
mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/agents"

# Skill'lar: eski nusxa bo'lsa zaxiralab, yangisini ko'chiramiz.
for p in skills/delegate skills/delegate-retro; do
  if [ -d "$CLAUDE_DIR/$p" ]; then
    if diff -rq -x __pycache__ "$REPO_DIR/$p" "$CLAUDE_DIR/$p" >/dev/null 2>&1; then
      echo "==> $p o'zgarmagan, o'tkazib yuborildi"; continue
    fi
    bak="$CLAUDE_DIR/$p.bak.$(date +%Y%m%d%H%M%S)"
    echo "==> mavjud $p -> $bak ga zaxiralandi"
    mv "$CLAUDE_DIR/$p" "$bak"
  fi
  echo "==> $p o'rnatilmoqda"
  cp -R "$REPO_DIR/$p" "$CLAUDE_DIR/$p"
done

# Agentlar: fayl-fayl ko'chiriladi, boshqa agentlaringizga tegilmaydi.
for f in "$REPO_DIR"/agents/*.md; do
  name="$(basename "$f")"
  if [ -f "$CLAUDE_DIR/agents/$name" ] && cmp -s "$f" "$CLAUDE_DIR/agents/$name"; then
    echo "==> agents/$name o'zgarmagan"
  else
    echo "==> agents/$name o'rnatilmoqda"
    cp "$f" "$CLAUDE_DIR/agents/$name"
  fi
done

# CLAUDE.md: lead agent skill'ni yuklashi uchun "Subagentlar" bo'limi kerak.
CM="$CLAUDE_DIR/CLAUDE.md"
if [ -f "$CM" ] && grep -q '`delegate` skill' "$CM"; then
  echo "==> CLAUDE.md da Subagentlar bo'limi allaqachon bor, o'zgartirilmadi"
else
  echo "==> CLAUDE.md ga Subagentlar bo'limi qo'shilmoqda"
  [ -f "$CM" ] && [ -s "$CM" ] && printf '\n' >> "$CM"
  cat "$REPO_DIR/CLAUDE.md.section" >> "$CM"
fi

# Tekshiruvlar
echo "==> tekshiruv"
if command -v python3 >/dev/null 2>&1; then
  echo "    python3: $(python3 --version 2>&1)"
  python3 -m py_compile "$CLAUDE_DIR"/skills/delegate/scripts/*.py && echo "    skriptlar kompilyatsiya qilindi (ok)"
  if command -v git >/dev/null 2>&1; then
    if python3 -m unittest discover -s "$CLAUDE_DIR/skills/delegate/scripts/tests" >/tmp/delegate-tests.log 2>&1; then
      echo "    skript testlari: $(grep -E '^Ran ' /tmp/delegate-tests.log) OK"
    else
      echo "    OGOHLANTIRISH: skript testlari o'tmadi, qarang: /tmp/delegate-tests.log"
    fi
  fi
  find "$CLAUDE_DIR/skills/delegate/scripts" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  if [ "$APPLY_DENY" = 1 ]; then
    echo "==> taqiq qoidalari (permissions.deny) qo'shilmoqda"
    python3 "$CLAUDE_DIR/skills/delegate/scripts/apply_deny.py" --settings "$CLAUDE_DIR/settings.json"
  else
    echo "    taqiq qoidalari qo'shilmadi; ko'rish: python3 $CLAUDE_DIR/skills/delegate/scripts/apply_deny.py --dry-run  (yoki install.sh --deny)"
  fi
  if [ "$APPLY_HOOKS" = 1 ]; then
    echo "==> o'lchov hook'lari qo'shilmoqda"
    python3 "$CLAUDE_DIR/skills/delegate/scripts/apply_hooks.py" --settings "$CLAUDE_DIR/settings.json"
  else
    echo "    hook'lar qo'shilmadi; ko'rish: python3 $CLAUDE_DIR/skills/delegate/scripts/apply_hooks.py --dry-run  (yoki install.sh --hooks)"
  fi
  # Yangilanish taklifi: nima o'rnatilgani $CLAUDE_DIR/delegate-install.json ga yoziladi (commit, remote, shu clone, flaglar).
  # SessionStart hook kuniga bir marta fonda (git ls-remote, o'sha deploy key) repo HEAD ni u bilan solishtiradi va yangisi
  # bo'lsa Claude sessiya boshida yangilashni taklif qiladi. O'chirish: DELEGATE_UPDATE_CHECK=0 yoki shu faylni o'chirish.
  echo "==> o'rnatilgan versiya: $(python3 "$CLAUDE_DIR/skills/delegate/scripts/update_check.py" --installed "$REPO_DIR" --flags "$FLAGS" 2>&1 || true)"
else
  echo "    OGOHLANTIRISH: python3 topilmadi: skill matni ishlaydi, lekin scripts/*.py (parity, agent_cost, apply_deny) ishlamaydi"
fi
if command -v claude >/dev/null 2>&1; then
  echo "    claude: $(claude --version 2>&1 | head -1)"
else
  echo "    OGOHLANTIRISH: 'claude' PATH da topilmadi (Claude Code o'rnatilganmi?)"
fi

echo "==> o'rnatilgan fayllar:"
find "$CLAUDE_DIR/skills/delegate" "$CLAUDE_DIR/skills/delegate-retro" -type f -not -path '*/tests/*' | sed "s|^$CLAUDE_DIR/|    |" | sort
for f in "$REPO_DIR"/agents/*.md; do echo "    agents/$(basename "$f")"; done
echo
echo "Tayyor. Tekshirish:  claude  ->  /delegate  (skill yuklanishi kerak)"
