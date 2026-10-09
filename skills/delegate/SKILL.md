---
name: delegate
description: Split a task across subagents without losing quality — which agent and model gets which kind of work, how to write a self-contained brief, when to split and when not to, and how to verify what comes back. Load before spawning any subagent, when planning a multi-step task, or when the lead's own context is growing large.
---

# Delegate: route work to the right subagent

Quality is the first constraint; tokens are the second. Delegate only when the result will be at least as good as doing it yourself, and never accept it unverified.

## What things cost (measured on this machine, 2026-10-07)

- Every API call re-sends the whole context (as cache reads). A session's real cost is roughly the sum of its context size over all its calls, so whatever is read early is paid for again on every later call.
- The lead (Opus) is the most expensive context in the system. In EDMS document-design stage 4 the lead made 280 calls and grew from 82k to 665k tokens of context: 114.7M cache-read tokens in total. Its two Sonnet subagents used 2.84M together. 83% of the lead's shell output was reading file slices and searching.
- Each fresh subagent pays a fixed startup cost before it reads anything: system prompt, tool schemas, CLAUDE.md and the brief. `general-purpose` carries every tool: ~53k tokens in the desktop app before CLAUDE.md (64k in EDMS). The lean agents below start at ~4–6k plus CLAUDE.md.
- A fork copies the lead's whole conversation. Don't fork from a large context.

## Who does what

| Work | Goes to | Notes |
|---|---|---|
| Reading the plan, design decisions, trade-offs, reviewing diffs, accepting the result | lead (you) | judgment stays here |
| A small change already in your context (about 3 files or fewer) | lead | a spawn costs more than the edit |
| Facts from code or docs: where X is defined or used, how a flow works, what a long doc says about Y | `sonnet-scout` | read-only, returns `path:line` evidence |
| Code changes from a concrete plan | `sonnet-coder` | the plan names files, behaviour, edge cases and checks |
| Docs, KB/help articles, UI strings, translations (uz/ru/en) | `sonnet-editor` | one worker per unit, all languages together |
| Acceptance checks, browser checks | `haiku-tester` | checklist in, PASS/FAIL with evidence out |
| Anything a script can decide: parity, lint, types, tests, greps for leftover wording | a script | deterministic and free: run it, don't eyeball it |

- Don't use `general-purpose` for routine work. It costs ~47k more per spawn for tools the work never touches. Use it only when no lean agent fits.
- Don't give Haiku Uzbek prose or judgment calls.
- If you're unsure a worker can match your quality on a kind of task, pilot it: delegate one unit, review it closely, add what you learned to the brief, then delegate the rest.

## Splitting

- Split into independent units that each have their own acceptance check (backend vs frontend, separate screens, groups of articles).
- Keep together what must stay consistent: all language versions of the same text (never split by language), a component and its tests, a migration and its model.
- Don't split below ~20–30k tokens of real work per worker. Below that, the startup cost and your review dominate.
- Don't let one worker grow too long either: give each coder one unit of work, sized to finish in roughly 100 calls. A worker's every call re-reads its whole context, so a 250-call run costs several times more than three 80-call runs (EDMS stage 5: two coders at 257 and 166 calls used 126M of the subagents' 169M cache-read). For a follow-up unit, spawn a fresh worker with a brief instead of sending more work to a long-running one.
- Ask coders to verify UI with the project's scripted checks (screenshot / regression scripts), not step-by-step browser clicking.
- Parallel workers must never touch the same files. Run dependent steps one after another and put the earlier result into the next brief.
- Shared code that workers will build on (a shell component, a composable) must pass lint, tests and one compile/render before you spawn them (EDMS stage 6: an uncompiled shell cost both coders workarounds).

## The brief (the worker knows only what you write down)

1. **Goal**: one sentence stating the outcome.
2. **Where**: the exact path or worktree, known places as `path:line`, and what NOT to touch.
3. **Context**: only what the worker needs, such as the decision already made and the new behaviour. Leave out the history.
4. **Rules**: conventions, glossary, style, and safety (no git state changes, no destructive DB commands).
5. **Acceptance**: commands to run, greps that must return nothing, and the parity check clean.
6. **Output**: format and length, plus "Open questions" for anything ambiguous instead of guessing.

Hand over the facts you already have (line numbers, names, glossary) so the worker doesn't rediscover them. Give paths and line ranges, not pasted file contents. 1–3k tokens is the usual sweet spot. For a worked example, see `references/brief-example.md`.

## Verify before you accept (the quality gate)

- Run the deterministic checks, or require them in the brief: tests, lint, `scripts/i18n_parity.py`.
- Review the changed lines, not whole files: `git diff --stat`, then `git diff -U1 -- <paths>`. For translations, run `scripts/i18n_diff.py <root>` (`--base main` after commits) and read every language's lines, not uz only; `(no change)` in one language is a red flag.
- When a decision depends on a scout's claim, open one or two of the cited lines.
- A report that says "not verified" or "didn't read back" is not done. Send it back with SendMessage (the worker keeps its context) or verify it yourself.
- Fix small issues yourself. Send larger ones back as a precise list.

## Keep your own context small

- Read the plan once. If the plan is long, read only the current stage's section.
- grep first, then read the range. Never re-read a file that hasn't changed.
- Subagents notify you when they finish. Don't `sleep` or poll; do your next step or end the turn.
- Filter long command output, e.g. `| tail -n 40` or `| grep -E 'error|fail'`.
- Push bulk reading (design references, long docs, many files) to a scout, or into the coder's brief as paths.
- If your context is large (~300k+) and the remaining work separates cleanly, stop at a natural boundary. Write a handoff note (done, next, decisions, open questions) and continue in a fresh session.

## Measure and improve

- `python3 ~/.claude/skills/delegate/scripts/agent_cost.py <session.jsonl | session dir | project dir>` reports, for the session and each of its subagents, the startup cost, final context, number of calls and largest outputs. `--reports` adds each brief, report, and what the lead did after the hand-back. `--log` records the session in `metrics.tsv`.
- After a stage, the user runs `/delegate-retro`. It measures the session, reviews quality, logs a lesson in `references/lessons.md`, and proposes evidence-based edits to this skill and the agents.
