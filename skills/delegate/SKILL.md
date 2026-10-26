---
name: delegate
description: Split a task across subagents without losing quality — which agent and model gets which kind of work, how to write a self-contained brief, when to split and when not to, and how to verify what comes back. Load before spawning any subagent, when planning a multi-step task, or when the lead's own context is growing large.
---

# Delegate: route work to the right subagent

Quality first; then waste neither the user's time nor the usage limit, a Claude Max subscription consumed roughly in proportion to API prices (an Opus/Fable token costs several Sonnet tokens, a Haiku token almost nothing). Delegate only when the result will be at least as good as your own, and never accept it unverified.

## What consumes the limit

- Every call re-sends the whole context (a session ≈ context × calls; the lead's context is the dearest); output costs 5× input and thinking stays in the context. A lean subagent starts at ~4–12k tokens, `general-purpose`/`Explore`/`Plan` at ~50k+, a fork at the lead's whole conversation.
- 1% of the weekly allowance ≈ $28 of Opus/Sonnet work (2026-10-26; $27 on 10-20) and ≈ $9–15 of Fable work (10-13). A Fable lead plans and reviews; bulk reading and writing are not its job. Delegation pays only when workers stay short and the lead small: `references/costs.md`.

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
- Not below ~20–30k tokens of real work per worker (startup and your review dominate), not above ≈ 60 calls or a ~4k-character brief: a coder's context is ~200k at call 50 and ~340k at call 100, so calls 51–100 cost ~2.5× calls 1–50 (2026-10-20). A worker that reaches its turn cap stops and reports what it has: split the unit and respawn with a fresh brief. One item per spawn when each needs its own build-and-look cycle of minutes (a character, a garment, a screen): 3–7-item briefs hit the cap with items undone six times (10-25/26) and were respawned anyway.
- Parallel workers never touch the same files; two coders in one checkout also share build caches, dev servers and the git index, so spawn them with `isolation: "worktree"` (it branches from your HEAD: commit first) and merge each result yourself. Worktrees still share ports, Docker and CPU: give each worker its own ports, start only the services its tests need, run related tests while iterating and full suites once at the end. Dependent steps run in sequence, the earlier result in the next brief. Units that all edit one file (a generator, a service provider, the changelog) are not independent: split the file or run them in sequence (10-25/26: one generator in 15 parallel workers, three `--ff-only` merges failed). Give each worker its own scratchpad subdirectory (three overwrote one `build.sh`).
- Shared code that workers build on (a shell component, a composable) must lint, test and compile before you spawn them.
- Name shared conventions in every parallel brief: display formats (name order, dates, numbers), i18n keys, naming.

## Time (the user's time; never bought with a skipped check)

- Put a decision only the user can make (approval, open questions) to them as soon as it exists, not in the wrap-up: they answer while workers run (2026-10-25: asked 2 h late, answered in 64 s, cost an extra PR).
- Workers build and verify at the smallest scale that proves the change. Runs the lead repeats anyway (full-scale data, the CI gate) and anything over a few minutes are the lead's, with `run_in_background`, while it does the next independent step.
- Start the gate (CI, review) on the final tree and change nothing while it runs; run the seconds-long checks first. One fix commit per review round; a round with only minor findings goes to the next change.
- A hand-back saying "waiting on its own background work" means the worker left processes running: find them by its own paths (`pgrep -fl <its scratchpad or log>`), not a guessed pattern, and stop them before accepting.

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
- Diff the checks too: a threshold, expected value, skip or disabled assertion changed in a test or check is a finding unless the brief asked for it (three workers loosened a limit to pass, 10-25/26).
- "Not verified" or "didn't read back" is not done: send it back once with a precise list (SendMessage keeps the worker's context); if the second attempt fails too, fix it yourself or respawn with a corrected brief, never a third round.

## Keep your own context small

- Read the plan once; if it is long, only the current stage's section. grep first, then read the range. Never re-read an unchanged file.
- Fewer calls: batch independent shell commands into one Bash call, make independent tool calls in parallel, never spend a call just to look.
- Subagents notify you when they finish: no `sleep`, no polling. Filter long output (`| tail -n 40`, `| grep -E 'error|fail'`), but a pipe returns tail's exit code, so run a gate as `cmd > log 2>&1; echo exit=$?; tail -n 40 log`, never `gate | tail && merge` (10-26: a PR merged on a failed check).
- Push bulk reading (design references, long docs, many files) to a scout, or into the coder's brief as paths.
- One stage per session. At a natural boundary, or once your context is large (~200k+), write a handoff note (done, next, decisions, open questions, usage % before/after) and continue in a fresh session.

## Enforcement and measurement

- A prompt rule is a request, not a guardrail: `references/enforcement.md` has the deny list and the hooks; the agents have tool allowlists and turn caps. Hooks log every substantial session to `~/.claude/delegate-metrics.tsv` and print each subagent's cost after its hand-back.
- Read the plan limits at the start and the end of a stage (desktop: `get_usage`; terminal: `/usage`) and put the weekly all-models % in the handoff note; when the allowance is low, run Sonnet-only work and keep the lead for review. After a stage the user runs `/delegate-retro`.
