# Enforcement: rules the harness applies, not the prompt

A sentence in an agent prompt is a request; a worker can misread it or drop it under pressure. Put safety and scope into the harness wherever it can hold them.

## Deny list (data-destroying commands)

`settings-deny.json` next to this file holds `permissions.deny` rules for the user-level `~/.claude/settings.json`. Apply or re-apply them (idempotent, keeps every other setting):

    python3 ~/.claude/skills/delegate/scripts/apply_deny.py --dry-run   # show what would be added
    python3 ~/.claude/skills/delegate/scripts/apply_deny.py             # merge into ~/.claude/settings.json

How deny rules behave (Claude Code docs, 2026-10):
- Deny is evaluated before ask and allow, in every permission mode including auto, and for subagents: they run in the main conversation's mode, and a subagent's own `permissionMode` is ignored in auto mode, so deny rules are the only enforcement that reaches them.
- A rule matches each subcommand of a compound command (`a && b`, pipes, subshells, loops) and sees through wrappers such as `timeout`, `nice`, `xargs` and a leading `VAR=x`.
- `*` stands for any text. `Bash(git push --force*)` matches `--force` and `--force-with-lease`. `Bash(* migrate:fresh *)` matches `php artisan migrate:fresh --seed` and `sail artisan migrate:fresh --seed` but not the bare command, which needs the second rule `Bash(* migrate:fresh)`.
- It is a guardrail, not a security boundary: a different invocation form (a script that runs the command, `python -c`) is not matched. Keep the prompt rules too.

What the list covers: Laravel, Prisma, Rails and Django database resets, `dropdb`, docker volume removal, `git push --force`, `git reset --hard`, destructive `git clean`, and `rm -rf` of `/` or the home directory. Add project-specific rules to the project's `.claude/settings.json` under the same key; deny rules from every scope combine. To allow one of them again, remove the line from `~/.claude/settings.json` and run the command yourself in a terminal.

## Measurement hooks

`settings-hooks.json` next to this file installs four hooks that all run `scripts/hooks.py` (stdlib Python, no network, exit 0 always); apply with `python3 ~/.claude/skills/delegate/scripts/apply_hooks.py [--dry-run]`:
- `SessionStart` (startup only): one reminder line for the lead to read the plan limits at the start and before the final message, so no special prompt is needed. On a machine that installed the skill from the install repo (`install.sh` writes `~/.claude/delegate-install.json`: commit, remote, clone, flags), an update line comes first when the repo is ahead of that commit: `scripts/update_check.py --check` runs detached at most once a day (`git ls-remote` over the same deploy key, result in `~/.claude/delegate-update.json`, the hook itself never waits for the network), and the lead offers the user the update command with the recorded clone path and flags. `DELEGATE_UPDATE_CHECK=0` or deleting `delegate-install.json` turns it off; the source machine has no record, so no check.
- `SessionEnd`: logs the session to `~/.claude/delegate-metrics.tsv` when it had ≥ 30 calls or any subagent, and rewrites `~/.claude/delegate-report.txt`, the stage comparison table (same as `agent_cost.py --table [project]`), so the result of the measurement is readable at any time without a Claude session.
- `PostToolUse` on `Agent` and `UserPromptSubmit` (which carries a background subagent's hand-back): add one `delegate-cost:` line to the lead's context with that subagent's $, calls and final context, plus the lead's running total. The lead sees what each delegation cost while it can still size the next brief.
Hooks cannot read the plan limits; the lead reads them (desktop `get_usage` tool or `/usage`) and the retro logs them.

## Other harness-level rules

- Tool allowlists in `~/.claude/agents/*.md`: the scout has no shell and no write tool; no agent has the interactive browser (UI is verified by scripts).
- `maxTurns` per agent (scout 40, tester 60, editor 80, coder 120): a worker that hits its cap returns partial work, marked as such. That is a signal that the unit was too big, not a reason to resume it.
- `isolation: "worktree"` on the Agent call for parallel coders: each works in its own git worktree, and commands that reach into the main checkout are blocked.
- `~/.claude` is a git repo (remote AlbatrosPromotions/claude-config): every change to the skill, the agents or the deny list is a commit, so it can be reviewed and reverted.
