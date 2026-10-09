# What things cost: prices and measurements

Not loaded automatically. `SKILL.md` keeps the rules; this file keeps the numbers behind them.

## The budget is a subscription limit

The user works on a Claude Max subscription, not on API billing. The limit (a 5-hour window and a weekly allowance, shown by `/usage`) is consumed roughly in proportion to API list prices, so the API-equivalent $ printed by `scripts/agent_cost.py` is the best available proxy. Anthropic does not publish the exact formula, so the metrics file keeps the weekly all-models % before and after each stage (`usage_before`, `usage_after`) and `agent_cost.py` prints the resulting `limit calibration` (%/$). Several stages of that line are the real exchange rate; until then the $ figures are relative, not absolute. The desktop app exposes the limits to the lead as the `get_usage` tool (5-hour window, weekly all models, weekly per model); the terminal shows them with `/usage`.

## API list prices used by agent_cost.py ($ per million tokens, 2026-10-06)

| Model | input | cache write (≈1.25×) | cache read | output |
|---|---|---|---|---|
| Fable 5.1 | 10 | 12.5 | 0.25 | 50 |
| Opus 5.5 | 4 | 5 | 0.20 | 20 |
| Opus 5 / 4.x | 5 | 6.25 | 0.50 | 25 |
| Sonnet 5.5 / 5 | 2 | 2.5 | 0.20 | 10 |
| Sonnet 4.6 | 3 | 3.75 | 0.30 | 15 |
| Haiku 5.5 | 0.10 | 0.125 | 0.01 | 0.50 |

What follows from the table:
- Output costs 5× input on every model. Thinking is output, and in a long run output is a small share of the total (EDMS stage 8 lead: 129k output ≈ $2.6 against 49.5M cache reads ≈ $9.9). Effort is not only thinking, though: lower effort also means fewer, more consolidated tool calls, higher effort more exploration and verification calls, and calls × context is the biggest line. So an effort change must be judged by total $ and defects per stage, never by output tokens alone.
- Effort policy (decided 2026-10-09): coder and editor `max` (they write the deliverable, rework is dear), scout `high`, tester default. Lead `max` for the next stage as the quality baseline (`modelSettings.claude-opus-5-5.effortLevel` in `~/.claude/settings.json`), then one comparable stage at `xhigh`; compare total $, lead calls and defects, and keep `xhigh` only if defects are equal. Anthropic's guidance: `xhigh` is the sweet spot for coding and agentic work; `max` earns its cost only where measurement shows headroom.
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
