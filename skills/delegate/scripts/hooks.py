#!/usr/bin/env python3
"""Claude Code hook of the delegate skill: measures what delegation costs, without anyone having to remember to.

One script serves three events (references/settings-hooks.json has the config, apply_hooks.py installs it). The event is
one JSON object on stdin; the only thing ever written to stdout is one JSON object with "additionalContext":

  SessionStart      (matcher startup) one reminder line for the lead: read the usage limit now and before the final message;
                    on a machine that installed the skill from the install repo, an update line first when a newer version is
                    published (update_check.py: the check runs detached once a day, the hook reads its last result).
                    Before that, the sessions of this project that the SessionEnd hook missed (left open, resumed and grown)
                    are logged, within a few seconds (agent_cost.catch_up)
  SessionEnd        a session with >= 30 lead calls or any subagent is logged to delegate-metrics.tsv, as `agent_cost.py --log` does
                    (the weekly % the lead read with get_usage fills usage_before/usage_after), and delegate-report.txt (the
                    stage comparison table) is refreshed
  PostToolUse       (matcher ^Agent$) a foreground subagent handed back: one "delegate-cost:" line for the lead
  PostToolUse       (matcher *, argument "context") the lead's context passed 300k, or a further 100k: one line with its size and
                    what each further call re-reads, and the handoff rule; never inside a subagent
  UserPromptSubmit  a background subagent's <task-notification> arrived: the same line; any other prompt: nothing, at once

It always exits 0: a measurement hook must never block or break a session. A problem is one line on stderr.
"""
import contextlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # agent_cost.py lives next to this file

MIN_AGENT_CALLS = 2  # a subagent with fewer calls has only just been launched (a background launch): nothing to report yet


def session_of(event):
    path = os.path.expanduser(str(event.get("transcript_path") or ""))
    if not path.endswith(".jsonl"):
        raise ValueError("the %s event has no usable transcript_path" % event.get("hook_event_name"))
    return path


def agent_line(event, tool_use_id=None, agent_id=None):
    """The delegate-cost line for one subagent of the event's session; None while it has no transcript or has barely run."""
    import agent_cost  # imported late: a prompt that is not a hand-back should cost almost nothing
    session = session_of(event)
    meta, transcript = agent_cost.find_subagent(session, tool_use_id, agent_id)
    if not transcript:
        return None
    a = agent_cost.analyze(transcript)
    return agent_cost.summarize_agent(meta, transcript, session, a) if a["calls"] >= MIN_AGENT_CALLS else None


REMINDER = ("delegate: costs are logged automatically, and the weekly all-models % you read with get_usage goes into the metrics "
            "row by itself. If this session is a work stage (it will spawn subagents or run more than ~30 calls), read the plan "
            "limits now (desktop: the get_usage tool; terminal: /usage) and again right before your final message, and put both "
            "in the handoff note as usage before/after. Afterwards the user runs /delegate-retro in a new session.")
CATCH_UP_BUDGET = 5.0  # seconds: the SessionStart hook has a 10 s timeout and must never make a session wait for bookkeeping


NUDGE_FROM, NUDGE_STEP = 300000, 100000  # tokens. 2026-10-13: the ~200k handoff rule held in no stage since 10-07; 9e9e439d
# spent $20 of its $38 after 466k. Below ~300k a fresh session's restart (~65k startup plus re-reading) costs about what it saves.
TAIL = 512 * 1024  # bytes read from the end of the transcript to find the last call's usage
NUDGE = ("delegate: your context is now %dk tokens, and every further call re-reads all of it (~$%.2f a call). At the next "
         "natural boundary write the handoff note (done, next, decisions, open questions, usage before/after) and continue in a "
         "fresh session; until then hand bulk reading and writing to subagents.")


def last_context(path):
    """(context tokens, model) of the transcript's last API call, from its last TAIL bytes; (0, None) when none is there."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:  # the first line of the tail is usually cut
            continue
        m = row.get("message") if isinstance(row, dict) else None
        u = m.get("usage") if isinstance(m, dict) else None
        if row.get("type") == "assistant" and isinstance(u, dict):
            tokens = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            return tokens, m.get("model")
    return 0, None


def context_nudge(event):
    """The lead's context passed NUDGE_FROM, or a further NUDGE_STEP since the last line: the NUDGE line, once per step (after
    a compaction the context falls, so passing a step again is told again). A tool call inside a subagent (agent_id set) is
    skipped: the line is for the lead. The last step told is kept per transcript in delegate-nudge.json next to the metrics file."""
    if event.get("agent_id"):
        return None
    session = session_of(event)
    tokens, model = last_context(session)
    if tokens < NUDGE_FROM:
        return None
    step = NUDGE_FROM + (tokens - NUDGE_FROM) // NUDGE_STEP * NUDGE_STEP
    import agent_cost
    state_path = os.path.join(os.path.dirname(agent_cost.METRICS), "delegate-nudge.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    if state.get(session) == step:
        return None
    state.pop(session, None)
    state[session] = step
    state = dict(list(state.items())[-200:])  # the newest 200 transcripts are enough
    tmp = "%s.%d.tmp" % (state_path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)
    return NUDGE % (tokens // 1000, tokens * agent_cost.price_for(model or "")[2] / 1e6)


def session_start(event):
    try:  # sessions of this project that the SessionEnd hook missed are logged now; the one starting is skipped
        path = os.path.expanduser(str(event.get("transcript_path") or ""))
        if path.endswith(".jsonl") and os.path.isdir(os.path.dirname(path)):
            import agent_cost
            if agent_cost.catch_up(os.path.dirname(path), exclude=path, budget=CATCH_UP_BUDGET):
                agent_cost.write_report(agent_cost.project_name(path))
    except Exception as e:  # bookkeeping; the reminder must come through anyway
        print("delegate hooks: catch-up: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    if event.get("source") not in ("startup", "clear"):  # not on resume or compact (the lead already has the note), nor without a source
        return None
    try:
        import update_check
        update = update_check.notice()
    except Exception as e:  # the update line is a courtesy; the reminder must come through anyway
        print("delegate hooks: update check: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        update = None
    return update + "\n" + REMINDER if update else REMINDER


def session_end(event):
    import agent_cost
    session = session_of(event)
    if agent_cost.log_if_substantial(session):
        agent_cost.write_report(agent_cost.project_name(session))


def post_tool_use(event):
    if event.get("tool_name") == "Agent" and event.get("tool_use_id"):
        return agent_line(event, tool_use_id=event["tool_use_id"])


def user_prompt_submit(event):
    prompt = str(event.get("prompt") or "")
    if "<task-notification>" not in prompt:
        return None
    tool_use_id, task_id = (re.search(r"<%s>\s*([^<\s]+)\s*</%s>" % (tag, tag), prompt) for tag in ("tool-use-id", "task-id"))
    if tool_use_id or task_id:
        return agent_line(event, tool_use_id and tool_use_id.group(1), task_id and task_id.group(1))


HANDLERS = {"SessionStart": session_start, "SessionEnd": session_end, "PostToolUse": post_tool_use, "UserPromptSubmit": user_prompt_submit}


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if not isinstance(event, dict):
            raise ValueError("expected a JSON object on stdin")
        name = event.get("hook_event_name")
        handler = context_nudge if name == "PostToolUse" and "context" in sys.argv[1:] else HANDLERS.get(name)
        with contextlib.redirect_stdout(sys.stderr):  # agent_cost prints warnings (an unpriced model); stdout is for the answer only
            text = handler(event) if handler else None
    except (Exception, SystemExit) as e:
        print("delegate hooks: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}))


if __name__ == "__main__":
    main()
