# claude-delegate-skill

Claude Code uchun `delegate` skill'i (ishni subagentlarga sifat yo'qotmasdan bo'lish), `delegate-retro` skill'i va u tayangan 4 ta agent: `sonnet-scout`, `sonnet-coder`, `sonnet-editor`, `haiku-tester`.

Manba (source of truth): lokal `~/.claude` (repo `AlbatrosPromotions/claude-config`). Bu repo undan olingan, serverlarga o'rnatish uchun nusxa.

## Tarkib

```
agents/                 sonnet-scout, sonnet-coder, sonnet-editor, haiku-tester
skills/delegate/        SKILL.md, references/, scripts/ (agent_cost, i18n_parity, i18n_diff), metrics.tsv
skills/delegate-retro/  SKILL.md
CLAUDE.md.section       install.sh global CLAUDE.md ga qo'shadigan "Subagentlar" bo'limi
install.sh              serverda: ~/.claude ga o'rnatadi (idempotent, eski nusxani zaxiralaydi)
sync-from-local.sh      Mac'da: ~/.claude dagi yangi versiyani repo'ga olib keladi
```

## Serverga o'rnatish

Talablar: Claude Code (`claude`), `git`, `python3` (skriptlar uchun). Repo private, shuning uchun server GitHub'ga kira olishi kerak (quyida).

```bash
git clone git@github.com:AlbatrosPromotions/claude-delegate-skill.git ~/claude-delegate-skill
bash ~/claude-delegate-skill/install.sh
```

Skript nima qiladi:
- `skills/delegate`, `skills/delegate-retro` ni `~/.claude/skills/` ga ko'chiradi (eski nusxa bo'lsa `*.bak.<vaqt>` ga zaxiralaydi);
- 4 ta agent faylini `~/.claude/agents/` ga ko'chiradi (boshqa agentlaringizga tegmaydi);
- `~/.claude/CLAUDE.md` ga "Subagentlar" bo'limini qo'shadi (bor bo'lsa tegmaydi);
- `settings.json`, credentials, `projects/` ga tegmaydi.

Claude boshqa katalogdan config o'qisa: `CLAUDE_CONFIG_DIR=/path bash install.sh`.

Tekshirish: serverda `claude` ni ochib `/delegate` yozing, skill yuklanishi kerak. Agentlar `claude agents` yoki Agent tool ro'yxatida ko'rinadi.

### Server GitHub'ga qanday kiradi (deploy key, read-only)

Serverda:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "server-claude-delegate"
cat ~/.ssh/id_ed25519.pub
```

Chiqqan public key'ni repo'ga qo'shing: GitHub → repo → Settings → Deploy keys → Add (read-only yetarli). Yoki Mac'da:

```bash
gh repo deploy-key add /path/to/server_id_ed25519.pub --repo AlbatrosPromotions/claude-delegate-skill --title server
```

Muqobil: serverda `gh auth login` qilib, HTTPS orqali clone qilish.

## Yangilash

Serverda:

```bash
git -C ~/claude-delegate-skill pull && bash ~/claude-delegate-skill/install.sh
```

Mac'da (`/delegate-retro` skill'ni o'zgartirgandan keyin):

```bash
bash sync-from-local.sh && git add -A && git commit -m "sync from ~/.claude" && git push
```

## Eslatmalar

- `sonnet-coder` va `haiku-tester` ning `tools:` ro'yxatida `mcp__Claude_Browser` bor. Bu tool faqat Claude desktop ilovasida mavjud; serverda Claude Code uni e'tiborsiz qoldiradi va agent qolgan toollar bilan ishlaydi.
- `metrics.tsv` va `references/lessons.md` lokal o'lchovlar tarixi: retro ularga tayanadi, shuning uchun repo'da saqlanadi.
