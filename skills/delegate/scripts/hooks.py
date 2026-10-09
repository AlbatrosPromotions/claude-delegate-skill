#!/usr/bin/env python3
"""Claude Code hook of the delegate skill: measures what delegation costs, without anyone having to remember to.

One script serves three events (references/settings-hooks.json has the config, apply_hooks.py installs it). The event is
one JSON object on stdin; the only thing ever written to stdout is one JSON object with "additionalContext":

  SessionEnd        a session with >= 30 lead calls or any subagent is logged to delegate-metrics.tsv, as `agent_cost.py --log` does
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

MIN_LEAD_CALLS = 30  # SessionEnd: a shorter session without subagents is not worth a row
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


def session_end(event):
    import agent_cost
    session = session_of(event)
    lead = agent_cost.analyze(session)
    subs = agent_cost.subagent_files(session)
    if lead["calls"] >= MIN_LEAD_CALLS or subs:
        agent_cost.log_metrics(session, lead, [(agent_cost.read_meta(p), agent_cost.analyze(p)) for p in subs], quiet=True)


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


HANDLERS = {"SessionEnd": session_end, "PostToolUse": post_tool_use, "UserPromptSubmit": user_prompt_submit}


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
