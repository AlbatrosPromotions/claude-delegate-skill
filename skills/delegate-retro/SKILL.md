---
name: delegate-retro
description: Retrospective for the delegate skill after a stage — measure the session, review what the subagents delivered, log a lesson, and propose evidence-based edits to the delegate skill and the agents. The user runs it with /delegate-retro [session id, title or project dir].
disable-model-invocation: true
---

# Delegate retrospective

Goal: make `delegate` and the agents cheaper over time without ever making the results worse. Quality evidence outranks token savings. Reject any change that saves tokens but weakens a check.

## 1. Measure

- Pick the session the user names. Otherwise use the newest session of the current project: `~/.claude/projects/<cwd with every / replaced by ->`.
- Run `python3 ~/.claude/skills/delegate/scripts/agent_cost.py <session.jsonl or project dir> --reports --log`.
- Compare against earlier rows of `~/.claude/skills/delegate/metrics.tsv` (same project first): the lead's final context and cache-read per stage, the number of `general-purpose` spawns, and average subagent startup.

## 2. Review quality (this matters most)

For each subagent, use its brief, its report and the lead's next actions (all printed by `--reports`):
- Did it do what the brief asked? Look for "not verified", "did not", "unsure", deviations and open questions.
- Did the lead review the result (a diff of the changed paths, the checks) or move on? Did it send the agent back, or redo the work itself?
- Ask the user whether anything from this stage turned out wrong later: bugs, wrong translations, rework.
- Was the routing right (agent, model, effort)? Was the brief self-contained, or did the worker have to explore?

Name each problem with evidence (session, agent, what happened). Don't report a problem you can't show.

## 3. Log the lesson

Append to `~/.claude/skills/delegate/references/lessons.md`: `## <date> <project> <session> "<title>"`, then 2–6 bullets covering the numbers that moved, what worked, what failed (with evidence) and the change you propose.

## 4. Propose changes; the user approves

Turn recurring findings into the smallest durable change:
- **A defect that recurs** (seen twice or more): add a deterministic check (extend `scripts/i18n_parity.py` or add a script) or a verification step to the agent prompt.
- **Waste that recurs** (re-reading, exploring outside the scope, `general-purpose` spawns): add a rule to `SKILL.md` or to the agent prompt.
- **A worker that underdelivers** on a kind of task: change the routing or raise its effort. Lower an effort or pick a cheaper model only with at least two stages of evidence that quality held.

Show each change as a diff together with its evidence, and apply only what the user approves. Then commit:
`git -C ~/.claude add -A && git -C ~/.claude commit -m "delegate: <what changed and why>" && git -C ~/.claude push`
(the remote is the private repo AlbatrosPromotions/claude-config)
If a change made results worse, revert it: `git -C ~/.claude revert <commit>`.

## Guardrails

- Never remove or weaken verification steps, parity checks or review gates to save tokens.
- Keep `delegate/SKILL.md` under ~2k tokens, because it is loaded into the lead's context. Move detail to `references/`.
- Change numbers in `SKILL.md` (thresholds, costs) only from measurements, and date them.
