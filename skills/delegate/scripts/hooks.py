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
                    what each further call re-reads, and the handoff rule. Inside a subagent instead: the turn warning, once when
                    about a sixth of its maxTurns is left and once more 3 turns before the cap: finish and report
  UserPromptSubmit  a background subagent's <task-notification> arrived: the same line; a user's request in a chat past 300k
                    (once per 100k): the lead tells the user the chat's size and offers a new chat; any other prompt: nothing

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
         "natural boundary (a unit done, a PR opened) write the handoff note (done, next, decisions, open questions, usage "
         "before/after) and tell the user in one line to continue in a new chat from it; only the user can open one. Until then "
         "hand bulk reading and writing to subagents.")
# 2026-10-13 (e80047a3, 6fb889bb): NUDGE fired at 300k and 404k, yet the leads spent 53% and 52% of their $ after it, on new
# requests the user sent into the same chat, and named a new chat only when asked "what next?". A new request is the boundary.
PROMPT_NUDGE = ("delegate: the user is starting a new request in a chat whose context is %dk tokens; each call re-reads all of "
                "it (~$%.2f a call). Before you start, tell the user in one line that this chat is at %dk and that a new chat "
                "from a short handoff note would make every call several times cheaper, and offer to write the note; then do what "
                "they choose. If the request is a quick follow-up on work in your context (a fix they just saw, a merge), do it "
                "and put that line at the end of your answer instead.")
MACHINE_PROMPTS = ("<task-notification>", "<agent-message", "<ci-monitor-event")  # arrive as prompts, but no user wrote them

# 2026-10-13: the "report 10-20 calls before the cap" prompt rule did not hold: a sonnet-coder (e80047a3, overlay family) ran
# into maxTurns 120 without a report, as 01a6213c's coder A had on 10-12; a model does not count its own calls.
TURN_WARN = ("delegate: you have used %d of your %d turns; at %d the run ends and nothing you have not reported reaches the "
             "lead. Start nothing new: bring the work to a clean state, run the checks you can, and deliver your final report "
             "(done, left undone, checks run, open questions) within the next ~%d calls.")
TURN_LAST = ("delegate: %d of your %d turns used, the run ends in %d. Deliver your final report now: done, left undone, checks "
             "run, open questions.")
TURN_LAST_LEFT = 3


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


def told(key, step):
    """True the first time this step is told for key (a transcript, or a transcript plus a kind of line), False after that.
    The last step told per key is kept in delegate-nudge.json next to the metrics file; a new step replaces it."""
    import agent_cost
    state_path = os.path.join(os.path.dirname(agent_cost.METRICS), "delegate-nudge.json")
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    if state.get(key) == step:
        return False
    state.pop(key, None)
    state[key] = step
    state = dict(list(state.items())[-200:])  # the newest 200 keys are enough
    tmp = "%s.%d.tmp" % (state_path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, state_path)
    return True


def context_step(path):
    """(step, tokens, $ a call) once the transcript's last call passed NUDGE_FROM, else None; the step is NUDGE_FROM plus whole
    NUDGE_STEPs (after a compaction the context falls, so passing a step again is told again)."""
    tokens, model = last_context(path)
    if tokens < NUDGE_FROM:
        return None
    import agent_cost
    return NUDGE_FROM + (tokens - NUDGE_FROM) // NUDGE_STEP * NUDGE_STEP, tokens, tokens * agent_cost.price_for(model or "")[2] / 1e6


def context_nudge(event):
    """The lead's context passed NUDGE_FROM, or a further NUDGE_STEP since the last line: the NUDGE line, once per step. A tool
    call inside a subagent (agent_id set) gets the turn warning instead: the NUDGE line is for the lead."""
    if event.get("agent_id"):
        return turn_warning(event)
    session = session_of(event)
    at = context_step(session)
    return NUDGE % (at[1] // 1000, at[2]) if at and told(session, at[0]) else None


def prompt_nudge(event, prompt):
    """A request the user typed into a chat past NUDGE_FROM: PROMPT_NUDGE, once per step, apart from the tool-call NUDGE (which
    the lead has usually seen by then, without telling the user). Prompts no user wrote, and events without a transcript,
    get nothing: a prompt must never wait on, or fail for, bookkeeping."""
    if any(tag in prompt for tag in MACHINE_PROMPTS):
        return None
    path = os.path.expanduser(str(event.get("transcript_path") or ""))
    if not path.endswith(".jsonl") or not os.path.isfile(path):
        return None
    at = context_step(path)
    return PROMPT_NUDGE % (at[1] // 1000, at[2], at[1] // 1000) if at and told(path + "#prompt", at[0]) else None


def max_turns(agent_type, cwd):
    """maxTurns from the agent's definition, <cwd>/.claude/agents/<type>.md before the user's agents directory (a project agent
    wins, as in Claude Code); None for a built-in or plugin agent, or a definition without maxTurns."""
    if not re.fullmatch(r"[\w-]+", str(agent_type or "")):  # no file for built-in or plugin agents; nothing may leave the dir
        return None
    import agent_cost
    dirs = ([os.path.join(str(cwd), ".claude", "agents")] if cwd else []) + [os.path.join(os.path.dirname(agent_cost.METRICS), "agents")]
    for d in dirs:
        try:
            with open(os.path.join(d, agent_type + ".md"), encoding="utf-8") as f:
                head = f.read(8192)
        except OSError:
            continue
        front = re.match(r"---[ \t]*\n(.*?)\n---", head, re.S)
        n = front and re.search(r"^maxTurns:\s*(\d+)\s*$", front.group(1), re.M)
        return int(n.group(1)) if n else None
    return None


def api_calls(path):
    """The API calls in a transcript so far: its assistant rows, one per message id (a reply split over rows shares one)."""
    ids = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if '"assistant"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type") == "assistant":
                m = row.get("message")
                ids.add((m.get("id") if isinstance(m, dict) else None) or i)
    return len(ids)


def turn_warning(event):
    """Inside a subagent: TURN_WARN once when about a sixth of its maxTurns is left (20 of a coder's 120), TURN_LAST once
    TURN_LAST_LEFT turns before the cap. The subagent's transcript is the event's own or agent-<agent_id>.jsonl of the session."""
    cap = max_turns(event.get("agent_type"), event.get("cwd"))
    if not cap:
        return None
    path = os.path.expanduser(str(event.get("transcript_path") or ""))
    if not os.path.basename(path).startswith("agent-"):
        import agent_cost
        _meta, path = agent_cost.find_subagent(session_of(event), agent_id=event.get("agent_id"))
    if not path or not os.path.isfile(path):
        return None
    calls = api_calls(path)
    left = cap - calls
    if left <= TURN_LAST_LEFT:
        step, text = "last", TURN_LAST % (calls, cap, max(left, 0))
    elif left <= cap // 6:
        step, text = "warn", TURN_WARN % (calls, cap, cap, left - TURN_LAST_LEFT)
    else:
        return None
    return text if told(path, step) else None


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
        return prompt_nudge(event, prompt)
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
