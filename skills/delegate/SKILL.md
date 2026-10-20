---
name: delegate
description: Split a task across subagents without losing quality — which agent and model gets which kind of work, how to write a self-contained brief, when to split and when not to, and how to verify what comes back. Load before spawning any subagent, when planning a multi-step task, or when the lead's own context is growing large.
---

# Delegate: route work to the right subagent

Quality first, then the usage limit. The budget is a Claude Max subscription, not API dollars: the limit is consumed roughly in proportion to API prices, so a lead-model token (Opus/Fable) costs several Sonnet tokens and a Haiku token almost nothing. Delegate only when the result will be at least as good as doing it yourself, and never accept it unverified.

## What consumes the limit

- Every call re-sends the whole context: a session costs about context size × calls; the lead's context is the dearest.
- A fresh subagent's startup (system prompt, tools, CLAUDE.md, brief) is ~4–12k tokens for the lean agents below, ~50k+ for `general-purpose`, `Explore` or `Plan`. A fork copies the lead's whole conversation.
- Output (thinking included) costs 5× input per token, and thinking stays in the context: 56% of a Sonnet `max` coder's context growth (2026-10-20). 1% of the weekly allowance ≈ $27 of Opus/Sonnet work (2026-10-20) and ≈ $9–15 of Fable work (10-13). A Fable lead plans and reviews; bulk reading and bulk writing are not its job.
- Delegation pays only when workers stay short and the lead small (EDMS 5–8: the workers cost what the lead saved). Numbers: `references/costs.md`.

## Who does what

| Work | Goes to | Notes |
|---|---|---|
| Plan, design decisions, reviewing diffs, accepting results | lead (you) | judgment stays here |
| A small change already in your context (≤ ~3 files) | lead | a spawn costs more than the edit |
| Facts from code or docs: where X is defined or used, how a flow works, what a long doc says | `sonnet-scout` | Read/Grep/Glob only, `path:line` evidence; ≤ ~5 files per spawn (40 turns). Git history: run git yourself, put the output in the brief |
| Code changes from a concrete plan | `sonnet-coder` | the plan names files, behaviour, edge cases, checks; 120-turn cap |
| Docs, KB/help articles, UI strings, translations | `sonnet-editor` | one worker per unit, all languages of a unit together; 80-turn cap |
| A Penpot screen from a spec and a ready component library | `sonnet-penpot` | one screen per spawn; 60-turn cap |
| Acceptance checks, scripted UI checks | `haiku-tester` | checklist in, PASS/FAIL with evidence out; pilot it on one unit first |
| Anything a script can decide: parity, lint, types, tests, greps | a script | deterministic and free |
| `general-purpose`, `Explore`, `Plan`, fork | avoid | 50k+ startup for tools the work never touches; for a tool set no lean agent has, write a lean agent in `~/.claude/agents/`; fork only from a small context (<~50k) |

- Don't give Haiku prose in Uzbek or Russian, or judgment calls.
- Unsure a worker matches your quality? Pilot one unit, review it, improve the brief, then delegate the rest.

## Splitting

- Independent units, each with its own acceptance check (backend vs frontend, separate screens, groups of articles). Keep together what must stay consistent: all languages of a text, a component and its tests.
- Not below ~20–30k tokens of real work per worker (startup and your review dominate), not above ≈ 60 calls or a ~4k-character brief: a coder's context is ~200k at call 50 and ~340k at call 100, so calls 51–100 cost ~2.5× calls 1–50 (2026-10-20). A worker that reaches its turn cap stops and reports what it has: split the unit and respawn with a fresh brief.
- Parallel workers never touch the same files; two coders in one checkout also share build caches, dev servers and the git index, so spawn them with `isolation: "worktree"` (it branches from your HEAD: commit first) and merge each result yourself. Worktrees still share ports, Docker and CPU: give each worker its own ports, start only the services its tests need, run related tests while iterating and full suites once at the end. Dependent steps run in sequence, the earlier result in the next brief.
- Shared code that workers build on (a shell component, a composable) must lint, test and compile before you spawn them.
- Name shared conventions in every parallel brief: display formats (name order, dates, numbers), i18n keys, naming.

## The brief (the worker knows only what you write down)

1. **Goal**: one sentence stating the outcome.
2. **Where**: the exact path or worktree, known places as `path:line`, and what NOT to touch.
3. **Context**: only what the worker needs (the decision made, the new behaviour). No history.
4. **Rules**: conventions, glossary, shared formats, safety (no git state changes, no data-destroying commands); project rules live in the project's CLAUDE.md, which every worker reads.
5. **Acceptance**: commands to run, greps that must return nothing, the parity check clean.
6. **Output**: report in English, ≤ ~30 lines, "Open questions" for anything ambiguous instead of guessing.

Hand over the facts you have (line numbers, names, glossary); give paths and ranges, not pasted file contents. 1–3k tokens is the sweet spot. Examples: `references/brief-example.md`.

## Verify before you accept (the quality gate)

- Run the deterministic checks, or require them in the brief: tests, lint, `scripts/i18n_parity.py`.
- Review changed lines, not whole files: `git diff --stat`, then `git diff -U1 -- <paths>`; after a tester, `git status` must show no product file changed. For translations run `scripts/i18n_diff.py <root>` and read every language; `(no change)` in one language is a red flag.
- When a decision depends on a scout's claim, open one or two of the cited lines.
- "Not verified" or "didn't read back" is not done: send it back once with a precise list (SendMessage keeps the worker's context); if the second attempt fails too, fix it yourself or respawn with a corrected brief, never a third round.

## Keep your own context small

- Read the plan once; if it is long, only the current stage's section. grep first, then read the range. Never re-read an unchanged file.
- Fewer calls: batch independent shell commands into one Bash call, make independent tool calls in parallel, never spend a call just to look.
- Subagents notify you when they finish: no `sleep`, no polling. Filter long output (`| tail -n 40`, `| grep -E 'error|fail'`).
- Push bulk reading (design references, long docs, many files) to a scout, or into the coder's brief as paths.
- One stage per session. At a natural boundary, or once your context is large (~200k+), write a handoff note (done, next, decisions, open questions, usage % before/after) and continue in a fresh session.

## Enforcement and measurement

- A prompt rule is a request, not a guardrail: `references/enforcement.md` has the deny list and the hooks; the agents have tool allowlists and turn caps.
- Hooks log every substantial session to `~/.claude/delegate-metrics.tsv` and show each subagent's cost after its hand-back; `scripts/agent_cost.py --table [project]` compares stages, `agent_cost.py <session.jsonl> [--reports]` details one.
- Read the plan limits at the start and the end of a stage (desktop: `get_usage` tool; terminal: `/usage`); put the weekly all-models % in the handoff note. When the allowance is low, run Sonnet-only work and keep the lead for review.
- After a stage the user runs `/delegate-retro`: measurement, quality review, a lesson in `references/lessons.md`, proposed edits.
