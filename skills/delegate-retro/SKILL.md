---
name: delegate-retro
description: Retrospective for the delegate skill after a stage — measure the session's API-equivalent cost, review what the subagents delivered, log a lesson, and propose evidence-based edits to the delegate skill and the agents. The user runs it with /delegate-retro [session id, title or project dir].
disable-model-invocation: true
---

# Delegate retrospective

Goal: make `delegate` and the agents cheaper over time without ever making the results worse. The budget is a subscription usage limit, consumed roughly in proportion to API prices, so compare stages by the $ figures, not by raw token counts. Quality evidence outranks savings: reject any change that saves tokens but weakens a check.

## 1. Measure

- Pick the session the user names (an id prefix, or a title to match against `<session>/custom-title.json`). Otherwise use `--previous`: the newest session of the current project is this retro itself, so the stage is the one before it. Project dir: `~/.claude/projects/<cwd with every / replaced by ->`.
- Run `python3 ~/.claude/skills/delegate/scripts/agent_cost.py <session.jsonl or project dir> [--previous] --reports --log`. Heed its warnings (unknown model price, transcript layout drift).
- Compare against earlier rows of `~/.claude/delegate-metrics.tsv` (same project first): lead $ and subagent $ per stage, the lead's final context, the number of `general-purpose`/`Explore` spawns, average subagent startup. Note the stage's scope so a bigger stage isn't read as a regression.
- If the user shares `/usage` percentages from before and after the stage, log them in the lesson: they are the ground truth for what the limit really charged.

## 2. Review quality (this matters most)

For each subagent, use its brief, its report and the lead's next actions (all printed by `--reports`):
- Did it do what the brief asked? Look for "not verified", "did not", "unsure", partial output (turn cap hit), deviations and open questions.
- Did the lead review the result (a diff of the changed paths, the checks) or move on? Did it send the agent back, or redo the work itself?
- Ask the user whether anything from this stage turned out wrong later: bugs, wrong translations, rework.
- Was the routing right (agent, model, effort)? Was the brief self-contained, or did the worker have to explore? Did parallel workers collide (same files, same checkout) or diverge on shared formats?

Name each problem with evidence (session, agent, what happened). Don't report a problem you can't show.

## 3. Log the lesson

Append to `~/.claude/skills/delegate/references/lessons.md`: `## <date> <project> <session> "<title>"`, then 2–6 bullets covering the numbers that moved ($ first), what worked, what failed (with evidence) and the change you propose.

## 4. Propose changes; the user approves

Turn recurring findings into the smallest durable change:
- **A defect that recurs** (seen twice or more): add a deterministic check (extend `scripts/i18n_parity.py` or add a script, with a test under `scripts/tests/`) or a verification step to the agent prompt.
- **Waste that recurs** (re-reading, exploring outside the scope, `general-purpose` spawns, long workers): add a rule to `SKILL.md` or to the agent prompt.
- **A worker that underdelivers** on a kind of task: change the routing or raise its effort.
- **Safety or scope**: prefer harness-level enforcement (a deny rule in `references/settings-deny.json`, a tool allowlist, `maxTurns`) over a sentence in a prompt; see `references/enforcement.md`.

Show each change as a diff together with its evidence, and apply only what the user approves. Run the script tests when a script changed: `python3 -m unittest discover -s ~/.claude/skills/delegate/scripts/tests`. Then commit and publish:
`git -C ~/.claude add -A && git -C ~/.claude commit -m "delegate: <what changed and why>" && git -C ~/.claude push && bash ~/.claude/skills/delegate/scripts/publish.sh "delegate: <same message>"`
(`~/.claude` is the source of truth, remote AlbatrosPromotions/claude-config; `publish.sh` mirrors the skill and agents to AlbatrosPromotions/claude-delegate-skill, which servers install from.)
If a change made results worse, revert it (`git -C ~/.claude revert <commit>`) and publish again.

## Guardrails

- Never remove or weaken verification steps, parity checks or review gates to save tokens.
- Keep `delegate/SKILL.md` under ~2k tokens (about 7k characters of English), because it is loaded into the lead's context. Move detail to `references/`.
- Change numbers in `SKILL.md` and `references/costs.md` (thresholds, costs, prices) only from measurements, and date them.
- Lower an effort or pick a cheaper model only with at least two stages of evidence that quality held. Effort mostly changes output tokens, a small share of a long worker's cost; shorter runs and smaller contexts save more.
