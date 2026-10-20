# What things cost: prices and measurements

Not loaded automatically. `SKILL.md` keeps the rules; this file keeps the numbers behind them.

## The budget is a subscription limit

The user works on a Claude Max subscription, not on API billing. The limit (a 5-hour window and a weekly allowance, shown by `/usage`) is consumed roughly in proportion to API list prices, so the API-equivalent $ printed by `scripts/agent_cost.py` is the best available proxy. Anthropic does not publish the exact formula, so the metrics file keeps the weekly all-models % before and after each stage (`usage_before`, `usage_after`) and `agent_cost.py` prints the resulting `limit calibration` (%/$). Several stages of that line are the real exchange rate; until then the $ figures are relative, not absolute. The desktop app exposes the limits to the lead as the `get_usage` tool (5-hour window, weekly all models, weekly per model); the terminal shows them with `/usage`.

## Limit calibration (measured 2026-10-12, recomputed 2026-10-13 without copied calls; Max plan, desktop app; superseded for Opus/Sonnet by the 2026-10-20 measurement under the price table, which counts subagent output)

Every `get_usage` reading in the transcripts was lined up against the cumulative API-equivalent $ of every session and subagent up to that moment (`agent_cost` prices), each API call counted once by its message id. The first version (10-12) counted the calls that a continued or forked conversation copies into its new transcript twice; that inflated the Fable intervals most ($116 → $71 in one). From 10-08 22:00 to the reset on 10-12 15:00Z the weekly all-models meter went 48% → 90% over $566: about $13.5 per 1%, so 100% of the week ≈ $1,350 at that mix (≈ $1,650 at the Opus-heavy mix after the reset). The rate depends on the model mix:

| Interval (UTC) | Meter | Transcript $ in the interval (by model) | $ per 1% |
|---|---|---|---|
| 10-08 22:00 → 23:33 | +2% | $50 (Sonnet 30, Opus 20) | 25 |
| 10-08 23:33 → 10-09 17:23 | +11% | $257 (Sonnet 175, Opus 59, Fable 22) | 23.4 |
| 10-10 12:38 → 10-11 01:23 | +10% | $71 (Fable 65, Opus 6) | 7.1 |
| 10-11 01:23 → 11:10 (one Fable session) | +6% | $73 (Fable 61, Opus 7, Sonnet 5) | 12.2 |
| 10-11 12:33 → 10-12 10:34 | +2% | $37 (Fable 20, Opus 15, Sonnet 2) | 18 |
| 10-12 15:07 → 10-13 11:08 (after the weekly reset) | +8% | $133 (Opus 90, Sonnet 25, Fable 18) | 16.6 |

Reading: Opus/Sonnet work costs the limit about $19–25 per 1% (the low end after the reset, taking Fable at $9), Fable work about $7–12 per 1% (two Fable-only intervals: 16% for $144, ≈ $9), so per API-equivalent dollar Fable consumes the weekly allowance ~2.4× faster than Opus 5.5; per token, with the 2.5× price on top, a Fable token costs the limit about 6× an Opus token. The Fable meter moved 22% → 34% for $59 of Fable work on 10-11 and 0% → 4% for $17.6 after the reset: that per-model allowance is worth ≈ $450–500. The meter shows whole percents, so a single interval of +1–2% carries ±50% error; read the long intervals. Usage outside Claude Code (claude.ai chat) in the same window also lands in the meter.

Hidden thinking: a lead's output tokens are 55–79% invisible thinking (output minus the visible text, thinking and tool-input characters at 4 chars/token): Fable 63–73% (53f065d2: 1.09M output tokens, ~695k hidden ≈ $35), Opus at `max` 56–79%. On Opus output is a small share of a session (01a6213c: $1.9 of $8.3); on Fable it is about half (53f065d2: $54 of $107; 52322d0f: $36 of $68), so effort matters for a Fable lead far more than for Opus.

## API list prices used by agent_cost.py ($ per million tokens, 2026-10-06)

| Model | input | cache write, 5 min (1.25×) | cache read | output |
|---|---|---|---|---|
| Fable 5.1 | 10 | 12.5 | 0.25 | 50 |
| Opus 5.5 | 4 | 5 | 0.20 | 20 |
| Opus 5 / 4.x | 5 | 6.25 | 0.50 | 25 |
| Sonnet 5.5 / 5 | 2 | 2.5 | 0.20 | 10 |
| Sonnet 4.6 | 3 | 3.75 | 0.30 | 15 |
| Haiku 5.5 | 0.10 | 0.125 | 0.01 | 0.50 |

A 1-hour cache write costs 2× input (Opus 5.5 $8, Sonnet 5.5 $4, Fable 5.1 $20). Claude Code writes the lead's cache with the 1-hour TTL and a subagent's with the 5-minute one; since 2026-10-14 `agent_cost.py` prices each part from `usage.cache_creation` and the metrics rows are re-logged at these prices. Before that every write counted at 1.25×, which hid ~9% of a stage's $ (31% of a Fable lead's): the calibration above was computed at the old prices, so its Fable rates are ~25–30% low. Measured at the new prices, 10-13 19:08Z → 10-14 17:11Z: 14% → 24% for $186 ($31 of it Fable): with Fable at ~2.7%, Opus/Sonnet ≈ $21 per 1%.

Subagent output (measured 2026-10-20). A background subagent's transcript keeps only the streaming start of each call's usage (`stop_reason` null, `output_tokens` 1–30), and `agent_cost.py` read each call's first row, so subagent output (mostly thinking, which also stays in the context) went nearly uncounted: in the 61 transcripts of 10-14 → 10-20 that do have final rows, the first rows held 0.74M of 9.67M output tokens. Since 10-20 a call takes its final row, and a call with only streamed rows gets as output the next call's context growth minus the user content in between at 2.1 chars/token (checked on 37 transcripts with final rows, stripped: median 1.01, p10–p90 0.95–1.13, $ within 1%). Re-priced, the 93 sessions of 10-14 → 10-20: subagents $974 → $1,362 (+40%), total $1,972 → $2,370 (+20%); a VR-Sud Sonnet `max` coder wrote ~188k output tokens (145 coders, $6.93 each), and its own output was 56% of its context growth. The weekly meter at these prices, every call counted once by message id: 10-14 12:13Z → 10-19 13:39Z 19% → 93% for $2,011 (Sonnet 1,278, Opus 726, Fable 7) = $27.2 per 1%; after the weekly reset, 10-19 15:10Z → 10-20 13:18Z 0% → 17% for $461 (Opus 292, Sonnet 170) = $27.1 per 1%. So 100% of the week ≈ $2,700 of Opus/Sonnet work. The calibrations above counted subagents at their first row (streamed rows appear from 2.1.293, 10-08), so their $ per 1% are low, the Sonnet-heavy 10-08/09 intervals most.

What follows from the table:
- Output costs 5× input on every model. Thinking is output, and in a long run output is a small share of the total (EDMS stage 8 lead: 129k output ≈ $2.6 against 49.5M cache reads ≈ $9.9). Effort is not only thinking, though: lower effort also means fewer, more consolidated tool calls, higher effort more exploration and verification calls, and calls × context is the biggest line. So an effort change must be judged by total $ and defects per stage, never by output tokens alone.
- Effort policy (decided 2026-10-09): coder and editor `max` (they write the deliverable, rework is dear), scout `high`, tester default. Lead `max` for the next stage as the quality baseline (`modelSettings.claude-opus-5-5.effortLevel` in `~/.claude/settings.json`), then one comparable stage at `xhigh`; compare total $, lead calls and defects, and keep `xhigh` only if defects are equal. Anthropic's guidance: `xhigh` is the sweet spot for coding and agentic work; `max` earns its cost only where measurement shows headroom.
- Coder pilot (decided by the user 2026-10-20): `sonnet-coder` runs at `xhigh` instead of `max` for the next two delegate stages, in every project, because thinking was 56% of a `max` coder's context growth (188k output tokens and $6.93 a coder in VR-Sud, 10-14 → 10-20). The retro compares those stages with the `max` baseline of 10-14 → 10-20: per coder $, calls and output; lead send-backs for a defect (follow-ups that only answer the worker's open questions or correct the brief do not count); defects found in review or later. Back to `max` (revert the agent file) if defects rise; `sonnet-editor` stays `max`.
- A cache read costs 2.5–10% of a fresh input token, so re-sending context is cheaper than it looks; at 400k context × 200 calls it is still the biggest line.
- Sonnet 5.5 and Opus 5.5 have the same cache-read price. Moving long-context work to Sonnet halves fresh input and output, not re-reads. The real savings come from smaller contexts and shorter runs on any model, and from Haiku for mechanical checks.
- A lead on Fable pays 2.5× Opus 5.5 for every token: use it for the plan and the review, not for bulk reading.

## Measured on this machine (desktop app, 2026-10-07/08)

- Startup of a fresh subagent (first call's context, before the project CLAUDE.md): `general-purpose` ~53k (64k inside EDMS); `sonnet-editor` 6.7k; `sonnet-scout` 4.4k. `Explore` and `Plan` carry nearly every tool, like `general-purpose`.
- EDMS stage 4, before the skill: the lead made 350 calls, grew from 82k to 782k tokens of context, 164.6M cache-read; its two `general-purpose` subagents used 2.84M. Stage 7, third stage with the skill: 185 calls, 418k, 44.3M (−73%), 7 lean subagents at 78.1M.
- Subagent volume: in stage 5 two coders at 257 and 166 calls used 126M of the subagents' 169M cache-read, mostly on step-by-step browser checks; after the scripted-check rule and the ~100-call unit, coders ran 81–107 calls and made 0–1 interactive browser calls.
- 83% of the stage-4 lead's shell output was reading file slices and searching; the seven costliest reads (PLAN.md whole, a 47k-char component, design references) were made in calls 3–29 and cost ~16.7M re-read tokens (~10% of the session).
- Stage-by-stage rows with $ columns: `~/.claude/delegate-metrics.tsv` (logged by `agent_cost.py --log`).

## EDMS stages priced (agent_cost.py, Opus 5.5 lead, Sonnet 5.5 workers, 2026-10-09)

| Stage | Lead $ | Subagents $ | Total $ | Lead cache-read | Sub cache-read |
|---|---|---|---|---|---|
| 4 (lead did the work itself) | 41.96 | 1.24 | 43.20 | 164.6M | 2.8M |
| 5 (first with the skill) | 21.57 | 39.53 | 61.09 | 78.6M | 169.3M |
| 6 | 15.69 | 30.91 | 46.60 | 54.7M | 116.0M |
| 7 | 12.94 | 20.23 | 33.17 | 44.3M | 78.1M |
| 8 | 14.50 | 26.16 | 40.66 | 49.5M | 96.3M |

Reading: the lead got 3× cheaper, but the workers added as much as the lead saved, so the total only fell once the workers were kept short (stage 7). The earlier retros compared cache-read tokens, which hid this because a Sonnet cache read costs the same as an Opus 5.5 one. Delegation buys quality, review and a small lead context; it saves the limit only when every worker's context × calls is also kept small.

## Sessions since the rework (2026-10-10 → 10-12, every project)

| Session | Lead | Lead $ | Workers | Workers $ | Total | Note |
|---|---|---|---|---|---|---|
| 52322d0f TZ analysis | Fable, 122 calls, avg ctx 380k | 67.80 | none | 0 | 67.80 | 723k output tokens; read 100k chars of the TZ in calls 2–5 |
| 53f065d2 TZ → architecture package | Fable, 251 calls, avg ctx 357k, 5 compactions | 107.12 | 6 scouts | 4.87 | 111.99 | 1.09M output tokens; 1.2M chars of documents written through heredocs; two scouts hit the 40-turn cap |
| 01a6213c Delice admin cleanup | Opus, 136 calls | 8.27 | 2 coders (120, 113 calls) | 9.76 | 18.03 | coder A hit the 120-turn cap on a 9.1k-char brief |
| b7a78328 Penpot pilot screen | Opus, 53 calls | 4.05 | 1 `general-purpose` (50k startup) | 0.54 | 4.59 | no lean agent had the Penpot tools; `sonnet-penpot` written afterwards |
| 64c5b771 five Penpot screens | Opus, 82 calls | 6.50 | 5 `sonnet-penpot` (12k startup, 13–29 calls) | 1.74 | 8.24 | every screen self-checked and re-exported by the lead |
| ce076143 SMM brief package | Opus, 76 calls | 7.00 | none | 0 | 7.00 | usage 87% → 88% |
| 11 smaller Opus sessions (Herd, sos-scrum, scratch) | 43–97 calls | 1.7–6.1 each | none | 0 | ≈ 33 | |

Totals: $289 in three days, Fable $208. The three Fable sessions of the TZ thread ($41 + $68 + $107 = $216) are the whole story of the week's consumption; delegation, where it was used, cost $17 for 14 workers.
