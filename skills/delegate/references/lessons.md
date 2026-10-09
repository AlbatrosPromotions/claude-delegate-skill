# Delegation lessons

One entry per reviewed stage, newest last. Written by `/delegate-retro`. When a lesson recurs, it is distilled into `SKILL.md` or the agent prompts. This file is not loaded automatically.

## 2026-10-07 EDMS 06d883d0 "Build the document page (design stage 4)": baseline before the delegate skill
- The lead (Opus) did the implementation itself and delegated only the docs and KB edits. It made 350 calls, its context grew from 82k to 782k, and it used 164.6M cache-read tokens. Over its first 280 calls, 83% of its shell output was reading file slices and searching.
- Two Sonnet `general-purpose` subagents (project docs; KB articles in uz/ru/en) used 2.84M cache-read tokens together. Of each one's context, 64k was startup overhead (every tool schema plus CLAUDE.md), yet they used only Bash.
- Quality: the KB agent reported "I did not read the files back for typos". The lead reviewed later: `git diff -U0` of the uz articles about 1.5 minutes after the hand-back, plus one ru wording fix. So the review existed, but it was ad hoc and covered uz only.
- Changes made:
  - lean agents with tool allowlists (`sonnet-scout`, `sonnet-editor`; allowlists on `sonnet-coder` and `haiku-tester`);
  - this skill;
  - `i18n_parity.py`;
  - mandatory read-back in the editor, and a diff review of all languages in the quality gate.
  - Measured startup in the desktop app, without project CLAUDE.md: `general-purpose` 53k → `sonnet-editor` 6.7k, `sonnet-scout` 4.4k.
- Costliest lead outputs, ranked by size × later calls (`agent_cost.py`): all 7 top ones were read in calls 3–29 and cost ~16.7M re-read tokens (~10% of the session):
  - PLAN.md, read whole in two parts (~60k chars, ~6.4M);
  - a front component (~47k chars, ~4.7M);
  - design references `screens.js` and `lib.js` (~38k chars, ~4M);
  - a `frontend.md` slice (~1.7M).

  Large reads late in the session (98k chars of a saved artifact, around call 300+) were cheap in total.
- Candidates for the next retro (they need evidence that quality holds):
  - read only the current stage's section of PLAN.md, if that section is self-contained;
  - hand design references and big component files to `sonnet-coder` as paths in its brief.
- Watch at the next stage:
  - the lead's final context and cache-read per stage;
  - whether delegated work needed rework;
  - whether the lead reviews all three languages.

## 2026-10-07 EDMS 653ac61c "Document design stage 5: journals and forms": first stage with the delegate skill
- Lead (Opus): 245 calls, final context 564k (stage 4: 782k), cache-read 78.6M (stage 4: 164.6M, −52%), output 163k (stage 4: 269k).
- Routing was right: 5 subagents, all lean (3 `sonnet-coder`, 2 `sonnet-editor`), none `general-purpose`. Average startup 32k (stage 4: 65k).
- Quality held. The lead reviewed every hand-back by diff, fixed leftovers itself, and sent the regression coder back once. The regression script found a real, pre-existing validator bug (on-behalf statement attachments), which was then fixed. Editor reports list their checks; the docs editor flagged 5 possibly stale lines, and the lead checked them.
- New problem: subagent volume. Together they used 169.3M cache-read (Sonnet), more than the lead. The two big coders (257 and 166 calls, contexts 534k and 411k) spent most of their calls on step-by-step browser checks: 281 and 105 browser tool uses, mostly `mcp__Claude_Browser__computer`. Each step re-read a 300–500k context. Their early whole-file reads (~30k chars at calls 1–7) were also costly.
- Approved by the user and applied (2026-10-07). Estimated on the stage-5 transcripts: 171M → ~100M (−42%) for script checks + reading rules, ~82M (−52%) with ≤100-call units. Check at stage 6:
  - `sonnet-coder`: verify UI with the project's scripted checks (one Bash run of the puppeteer screenshot/regression script), and use the interactive browser only for a specific issue a script can't show, keeping it short;
  - add the editor's reading rules (grep, then ranges; no re-reads);
  - lead: one coder per unit of work, split before a unit grows past ~100 calls.

## 2026-10-07 EDMS 923c2081 "Document design stage 6: reference books": second stage with the delegate skill
- Lead (Opus): 200 calls, final context 460k (stage 5: 564k, stage 4: 782k), cache-read 52.0M (stage 5: 78.6M, −34%). 8 subagents, all lean (3 scouts, 3 coders, 2 editors), 0 `general-purpose`, average startup 25k (stage 5: 32k). Subagent cache-read 116M (stage 5: 169M, −31%).
- The stage-5 rule worked: coders made 0 interactive browser calls (stage 5: 386) and verified with puppeteer scripts; a final runner did 209 PASS / 0 FAIL on a freshly restored DB. Still, coder A ran 169 calls (target ~100; 52.8M of the 116M) — the unit (two editors + unified list + route restyle + tour) was not split.
- Failure 1, lead's shared code: the lead wrote the shell `sections/section/save-bar.vue` (calls 57–60) and spawned both coders (74–75) without linting or compiling it. It failed to compile (`@include down()` without `@use` of the mixin, a wrong token `--color-text-primary`, stylelint/prettier errors). Coder B wrote three patches and tested on patched copies; coder A made `xt-*` copies; the lead fixed the shell at calls 98–100 and messaged coder A.
- Failure 2, a defect hidden as a script bug: the template-flow coder found that the first-open tour offer card covers the sticky «Saqlash» button and changed the scripts ("both script bugs… I made no product code changes"). The lead caught it as a real defect and fixed it. Stage 5's regression coder did report its real bug, so this is the first occurrence.
- Waste: the lead used `sleep 1` (call 21) and `sleep 240` (call 89) while waiting for subagents (also flagged in memory `edms-slowness`). `metrics.tsv` lost the project name because `agent_cost.py` was given a relative path.
- Review: every hand-back was reviewed (diffs, lint, tests, regression). The KB editor ran `i18n_parity.py` (0 errors), but the lead read only the uz hunk of one article (`git diff -U0 … uz/document-types.md | head -60`, call 93) — uz-only again, as in stage 4. Watch at stage 7.
- Proposed: lint/compile shared code before spawning; no sleeping while subagents run; coders report UI obstacles as possible defects; `agent_cost.py` resolves the path.

## 2026-10-08 EDMS 4a12cfb8 "Document design stage 7: files": third stage with the delegate skill
- Lead (Opus): 185 calls, final context 418k (stage 6: 460k, stage 4: 782k), cache-read 44.3M (stage 6: 52.0M, −15%; stage 4: 164.6M, −73%). 7 subagents, all lean (2 scouts, 3 coders, 2 editors), 0 `general-purpose`, startup avg 26k. Subagent cache-read 78.1M (stage 6: 116M, −33%; stage 5: 169M). Lead + subagents: 122M (stage 6: 168M, stage 5: 248M).
- Stage-6 rules held: coders ran 81 / 107 / 103 calls (stage 6: 169) with 0 interactive browser calls; the lead edited and linted the shared `file-viewer.vue` (calls 71–74) before spawning the two dependent coders (76–77), and no coder had to patch it; no sleep-waits for subagents (the two `sleep`s were in-script/server warm-up); coder C reported a product defect as an open question (video play badge never shows) instead of hiding it.
- Review: every hand-back reviewed by diff, lint/test and screenshots; 0 sent back. The lead found a KB gap (task attachments missing from the «Send to chat» exception) and fixed it in uz/ru/en.
- Recurring gap (3rd time: stages 4, 6, 7): the lead read only the uz KB diff (call 84: `git diff -U0 resources/knowledge/uz/`) and checked ru/en with one targeted grep, although SKILL.md says to read uz/ru/en hunks side by side. No ru/en defect has been found so far (PRs front #139 / api #151 still open), but the gate is not followed because reading three full diffs is expensive.
- Proposed: a `scripts/i18n_diff.py` that prints each changed unit's uz/ru/en changed lines grouped together (one cheap command), and the quality-gate line points to it.
- Applied (user approved 2026-10-08): `scripts/i18n_diff.py` (per changed unit: every language's changed md lines, or each changed key with its uz/ru/en values; `(no change)` flags a language left behind), and the quality gate in SKILL.md now points to it.
- User feedback (2026-10-08): the only defects found so far are in the orders list row — «Muddati oʻtgan» shown twice (status badge + holder tag), the icon/text block and the `⋯` button look off. All come from `components/custom/document-row.vue` (commit f2bacf1, stage 3), which the lead built alone without subagents. No defect traced to delegated work in stages 5–7.

## 2026-10-08 EDMS aef3f855 "Start document design stage 8 (statistics + user page)": fourth stage with the delegate skill
- Lead (Opus): 203 calls, final context 447k (stage 7: 418k), cache-read 49.5M (stage 7: 44.3M, +12%). 7 subagents (2 scouts, 4 coders, 1 editor), 0 `general-purpose`, startup avg 28k. Subagent cache-read 96.3M (stage 7: 78.1M, +23%); lead + subagents 146M (stage 7: 122M, stage 6: 168M, stage 5: 248M). Scope was larger than stage 7 (backend + three screens + KB), so the rise is proportional, not a regression.
- Earlier rules held: the lead built the shared `utils/staffStatus.ts` and common keys before spawning the two parallel front coders; `scripts/i18n_diff.py` was used for the KB and locale review (the stage-7 gap is closed); coders made 1 interactive browser call in total; full regression of stages 4–7 ran in the background while the lead wrote docs.
- Review: every hand-back reviewed (diff, sqlite check of the staffing numbers, `tsc` against a `git stash` baseline, screenshots); 0 sent back. The lead fixed three things itself: the editor's two Knowledge-test lines (reported, not hidden), the «Statistika» → «Xodimlar holati» rename in the KB (brief gap: it did not say the label changes), and a name-order mismatch between the users list and the user page (two parallel coders each formatted the person name their own way).
- New, seen once (watch, no rule yet): the «Xodimlar holati» coder wrote its report in Russian although the brief had no Cyrillic (it had been editing ru.json); the largest coder (user page) ran 137 calls / 35.6M with 7 `sleep` polls of its own background screenshot run.
- Recurring cost, no cheap fix: re-reading the published visual page (`report.html`) costs 130–190k chars in every stage (stages 6–8); the Artifact tool requires it, and it happens in the last ~20 calls, so it is ~2% of the lead.
- No change proposed this stage. If the report-language drift or the cross-coder display mismatch recurs, add "report in English" to the agent prompts and "name shared display formats (person name order, dates) in each parallel brief" to SKILL.md.

## 2026-10-09 review "Rework for a subscription budget": skill audit, no stage
- Context: the user runs on a Claude Max subscription (no API budget), so the limit, not dollars, is the constraint. Audit of the skill, the agents, the retro and the scripts (session 57862dd7, Fable 5.1 lead).
- Findings that changed the design: (1) safety was prompt-only (scout had Bash, tester had Edit/Write, DB resets forbidden only in text); (2) the agents carried EDMS/Laravel/desktop specifics (artisan commands, puppeteer, `mcp__Claude_Browser`); (3) the retro's "newest session" was the retro itself; (4) cost was measured in raw cache-read tokens, unweighted by price: Sonnet 5.5 and Opus 5.5 have the same cache-read price ($0.20/M), so moving long-context work to Sonnet saves on fresh input and output only; (5) `metrics.tsv` lived inside the skill (code and data mixed; servers and the Mac would diverge); (6) SKILL.md was over its own ~2k-token guardrail.
- Changes: tool allowlists tightened (scout Read/Grep/Glob; no browser anywhere), `maxTurns` per agent, generic safety rules with project specifics delegated to the project's CLAUDE.md, English reports; `references/enforcement.md` + `settings-deny.json` + `scripts/apply_deny.py`; `agent_cost.py` prints API-equivalent $ (price table dated 2026-10-06), `--previous`, a transcript-layout drift warning, metrics at `~/.claude/delegate-metrics.tsv`; `scripts/tests/`; `scripts/publish.sh` mirrors to AlbatrosPromotions/claude-delegate-skill; SKILL.md adds Explore/Plan avoidance, worktree isolation for parallel coders, a failure path (one send-back, then take over), one stage per session, `/usage` checks; costs moved to `references/costs.md`.
- Priced the five EDMS stages with the new script: totals $43.20 (stage 4, no delegation), $61.09, $46.60, $33.17, $40.66. The lead fell from $42 to $13–22, but the workers cost $20–40 per stage, so delegation has not yet reduced the total; the cache-read comparisons in the earlier entries overstated the savings. Table in `references/costs.md`.
- Watch at the next stage: the total $ per stage against this baseline (the target is below stage 4's $43 at equal scope), whether `maxTurns` caps are hit (then units are too big), whether the $ figures track `/usage` deltas, whether the scout misses git-history questions without a shell.
- Same day, later: `agent_cost.py --table [project]` and `~/.claude/delegate-report.txt` (rewritten by the SessionEnd hook) show the stage comparison without a Claude session; a SessionStart hook reminds the lead to read the plan limits at the start and before the final message, so no special prompt is needed. The SessionEnd hook logged four admin-hardening sessions on its own as they were closed: the pipeline works live.
- Follow-up the same day: hooks for automatic measurement (`scripts/hooks.py`: SessionEnd logs the session, PostToolUse(Agent)/UserPromptSubmit put a `delegate-cost:` line into the lead's context after each hand-back), `--usage-before/--usage-after` columns with a `limit calibration` line (%/$), avg context per call and a >150-calls hint. The desktop app exposes the plan limits to the lead as the `get_usage` tool; at the time of writing the weekly all-models meter stood at 61% with the Fable meter at 5%, so "all models" is the binding limit, not the lead model's own meter.
- Decision (user, 2026-10-09): the lead runs the next EDMS stage at effort `max` (settings.json, opus-5-5) as the quality baseline; the stage after it, at equal scope, at `xhigh`. Compare total $, lead calls, avg context per call and defects; keep the cheaper level only if defects are equal. Workers stay at `max` (coder, editor) and `high` (scout).
