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
        with contextlib.redirect_stdout(sys.stderr):  # agent_cost prints warnings (an unpriced model); stdout is for the answer only
            text = HANDLERS[name](event) if name in HANDLERS else None
    except (Exception, SystemExit) as e:
        print("delegate hooks: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}))


if __name__ == "__main__":
    main()
