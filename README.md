# claude-delegate-skill

Claude Code uchun `delegate` skill'i (ishni subagentlarga sifat yo'qotmasdan va limitni tejab bo'lish), `delegate-retro` skill'i va u tayangan 4 ta agent: `sonnet-scout`, `sonnet-coder`, `sonnet-editor`, `haiku-tester`.

Manba (source of truth): lokal `~/.claude` (private repo `AlbatrosPromotions/claude-config`). Bu repo undan `scripts/publish.sh` bilan ko'chirilgan nusxa, serverlarga o'rnatish uchun.

## Tarkib

```
agents/                          sonnet-scout, sonnet-coder, sonnet-editor, haiku-tester (tool allowlist + maxTurns)
skills/delegate/SKILL.md         qoidalar (~2k token, lead kontekstiga yuklanadi)
skills/delegate/references/      costs.md (narxlar, o'lchovlar), enforcement.md + settings-deny.json (taqiq ro'yxati),
                                 brief-example.md (brief namunalari), lessons.md (retro tarixi)
skills/delegate/scripts/         agent_cost.py (sessiya narxi, $, limit kalibratsiyasi), i18n_parity.py, i18n_diff.py,
                                 hooks.py (avtomatik o'lchov), apply_deny.py, apply_hooks.py (settings.json ga qo'shadi),
                                 update_check.py (yangilanish tekshiruvi), publish.sh (Mac -> shu repo), tests/ (unittest)
skills/delegate-retro/SKILL.md   /delegate-retro
CLAUDE.md.section                install.sh global CLAUDE.md ga qo'shadigan "Subagentlar" bo'limi
install.sh                       serverda: ~/.claude ga o'rnatadi (idempotent, eski nusxani zaxiralaydi)
```

## Serverga o'rnatish

Talablar: Claude Code (`claude`), `git`, `python3` (3.8+). Repo private, shuning uchun server GitHub'ga kira olishi kerak (quyida).

```bash
git clone git@github.com:AlbatrosPromotions/claude-delegate-skill.git ~/claude-delegate-skill
bash ~/claude-delegate-skill/install.sh --deny --hooks
```

Skript nima qiladi:
- `skills/delegate`, `skills/delegate-retro` ni `~/.claude/skills/` ga ko'chiradi (eski nusxa bo'lsa `*.bak.<vaqt>` ga zaxiralaydi);
- 4 ta agent faylini `~/.claude/agents/` ga ko'chiradi (boshqa agentlaringizga tegmaydi);
- `~/.claude/CLAUDE.md` ga "Subagentlar" bo'limini qo'shadi (bor bo'lsa tegmaydi);
- skript testlarini ishga tushiradi;
- `--deny` bilan: `references/settings-deny.json` dagi taqiq qoidalarini `~/.claude/settings.json` → `permissions.deny` ga qo'shadi (DB reset, force push, `git reset --hard`, `rm -rf ~` kabi buyruqlar; `references/enforcement.md`). Boshqa sozlamalarga tegmaydi.
- `--hooks` bilan: `references/settings-hooks.json` dagi to'rtta hook'ni qo'shadi (`scripts/hooks.py`): SessionStart lead'ga limitni o'qishni eslatadi; SessionEnd har jiddiy sessiyani `~/.claude/delegate-metrics.tsv` ga loglaydi va `~/.claude/delegate-report.txt` (bosqichlar solishtiruv jadvali, `agent_cost.py --table` bilan bir xil) ni yangilaydi; PostToolUse(Agent) va UserPromptSubmit har subagent tugashi bilan uning narxini lead kontekstiga bir qator qilib beradi. SessionStart yana repo'da yangi versiya chiqqan bo'lsa yangilashni taklif qiladi (quyida "Yangilanish taklifi").
- `settings.json` ning qolgan qismi, credentials, `projects/` o'zgarmaydi.

Claude boshqa katalogdan config o'qisa: `CLAUDE_CONFIG_DIR=/path bash install.sh`.

Tekshirish: serverda `claude` ni ochib `/delegate` yozing, skill yuklanishi kerak. Agentlar Agent tool ro'yxatida ko'rinadi.

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

O'rnatishda ishlatilgan flaglarni (`--deny --hooks`) yangilashda ham bering: `install.sh` ikkalasini idempotent qo'shadi, yangi hook yoki taqiq qoidasi shunda yetib boradi.

### Yangilanish taklifi

`install.sh` nima o'rnatganini `~/.claude/delegate-install.json` ga yozadi (repo commit'i, remote, clone yo'li, flaglar). `--hooks` bilan o'rnatilgan mashinada SessionStart hook kuniga bir marta fonda `update_check.py --check` ni ishga tushiradi: u `git ls-remote` bilan (o'sha deploy key, hook'ning o'zi tarmoqda kutmaydi) repo HEAD ni o'rnatilgan commit bilan solishtiradi, natijani `~/.claude/delegate-update.json` ga yozadi; clone hali turgan bo'lsa yangi commitlar ro'yxatini ham oladi. Repo oldinda bo'lsa keyingi sessiya boshida Claude bir qator bilan xabar beradi va yuqoridagi buyruqni (o'sha clone yo'li va flaglar bilan) taklif qiladi; buyruqni faqat siz rozi bo'lsangiz ishga tushiradi. Yangilangandan keyin yozuv yangilanadi va taklif yo'qoladi.

O'chirish: `DELEGATE_UPDATE_CHECK=0` muhit o'zgaruvchisi yoki `~/.claude/delegate-install.json` ni o'chirish. Mac'da (manba `~/.claude`) bu fayl yo'q, shuning uchun u yerda tekshiruv ham yo'q. Qo'lda ko'rish: `python3 ~/.claude/skills/delegate/scripts/update_check.py --check && python3 ~/.claude/skills/delegate/scripts/update_check.py --notice`.

Mac'da (`/delegate-retro` skill'ni o'zgartirgandan keyin; retro buni o'zi taklif qiladi):

```bash
bash ~/.claude/skills/delegate/scripts/publish.sh "delegate: nima o'zgardi"
```

## Byudjet haqida

Skill Max obuna limiti uchun sozlangan: limit API narxlariga taxminan proporsional sarflanadi, shuning uchun `agent_cost.py` har sessiya uchun API-ekvivalent $ ko'rsatadi, retro esa bosqichlarni $ bo'yicha solishtiradi. Asosiy tejash: lead kontekstini kichik tutish (bir bosqich = bir sessiya, hajmli o'qish scout/coder'ga), qisqa worker'lar (maxTurns), tekshiruvni skriptlar bilan. Tafsilot: `skills/delegate/references/costs.md`.

## Eslatmalar

- Agentlarda interaktiv brauzer tool'i yo'q: UI skript (screenshot/regression) bilan tekshiriladi; bu eng katta token sarfi manbai edi.
- Bosqich oldi va keyin limitni o'qing (desktop: `get_usage` tool, terminal: `/usage`) va haftalik foizni handoff yozuviga qo'ying; retro ularni `--usage-before/--usage-after` bilan loglaydi va $ ↔ limit kursini (`limit calibration`) hisoblaydi.
