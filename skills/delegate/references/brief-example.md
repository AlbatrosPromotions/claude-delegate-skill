# Brief example: multilingual KB update (sonnet-editor)

Based on a real EDMS brief (2026-10-07). The original produced a correct change, but its worker reported "I did not read the files back for typos". The parts marked NEW close that gap.

```
GOAL
Rewrite every sentence in the uz/ru/en help articles that describes the statement/memo/order
view as a window or dialog, so it describes the new document page. Minimal edits; no restructuring.

WHERE
Worktree: <abs path>/api/resources/knowledge  (uz/*.md, ru/*.md, en/*.md, same slugs)
Known places (uz line numbers; ru/en have the same sentences): acting.md:8, document-edit.md:34, ...
Also search the rest of the articles for the same meaning.
Do NOT touch: images, index.php, whats-new.php; sentences about OTHER windows
(signature dialog, approve/reject dialogs, file viewer, ...).

CONTEXT (what changed in the product)
<6–10 bullets: the new URL, page layout, tabs with their exact uz/ru/en names, where the
decision buttons are now, where help «?» is now, phone layout>

RULES
- Uzbek Latin: oʻ/gʻ with U+02BB «ʻ», apostrophe U+02BC «ʼ»; never ASCII ' or `. Quotes «…».
- Glossary: Ariza / Заявление / Application; Buyruq / Приказ / Order; ... (reuse each
  language's existing wording; where unsure, grep how that language already says it)
- The three languages must say the same thing. Front matter, kb:/img: links stay valid.
- No git state changes.

ACCEPTANCE                                                   <- NEW
- python3 ~/.claude/skills/delegate/scripts/i18n_parity.py <root> --changed --uz-okina ʻ
  run before (--save /tmp/kb-base.json) and after (--against /tmp/kb-base.json): no new findings.
- Read back every changed line: git diff -U0 --word-diff=plain -- uz ru en
- grep -nEi 'oyna|окн|window|dialog' over the changed files: only out-of-scope windows remain.

OUTPUT
Max 25 lines: files changed; one line per slug; checks run with results;
"Open questions" with path:line for anything you left unchanged on purpose.
```

Why it works:
- The worker starts with the line numbers the lead already found, so it doesn't explore.
- Each unit (slug) is edited in all three languages at once, so the versions stay consistent.
- The acceptance checks are deterministic and cheap. The parity check only reports what changed relative to the baseline, and the word diff shows only changed lines.
- The lead then reviews `git diff -U1` of the changed hunks (a few thousand tokens), not 36 whole files.
