#!/usr/bin/env python3
"""Where did a Claude Code session's tokens go? Reports the lead session and each of its subagents.

PATH may be a session .jsonl, a session directory, a subagent .jsonl, or a project directory under
~/.claude/projects (its newest session is used; with --previous, the one before it).

  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS
  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS/<session-id>.jsonl --top 8
  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS --previous --reports --log   # after a stage (used by /delegate-retro)

--previous takes the second-newest session of a project directory (the newest is the session that is running
the retro). --reports prints each subagent's brief and final report; --log adds the session to
~/.claude/delegate-metrics.tsv ($CLAUDE_CONFIG_DIR/delegate-metrics.tsv when that is set; re-logging the same
session replaces its row), so trends are visible over time. The SessionEnd hook (hooks.py) logs every substantial
session the same way.

--usage-before and --usage-after are the weekly all-models allowance used, in percent (/usage or the desktop usage
card), read at the start and at the end of a stage. With --log they go into the session's row (a value logged
earlier is kept unless you give a new one); once both are known, a "limit calibration" line says how much of the
allowance one API-equivalent dollar used: the real exchange rate between "$" below and the usage limit. When the lead
read the limits with the desktop get_usage tool, the first and last readings in the transcript fill the two cells by
themselves (a value you give wins).

--table first catches up: sessions of the last 14 days that the SessionEnd hook missed (left open, resumed and grown)
are logged or re-logged (delegate-catchup.json next to the metrics file remembers what was examined); the SessionStart
hook does the same for the project it starts in.

"startup" is the context of the first API call (system prompt + tool schemas + CLAUDE.md + prompt);
"final" is the context of the last call (roughly what the UI shows as an agent's tokens);
"avg ctx/call" is the mean context over all calls: what each call re-sends;
"cache-read" is the sum over all calls, which is what a long, large context really costs;
"re-read" estimates what one tool output cost: its size (in tokens) times the number of later calls;
"$" is API-equivalent dollars at list prices (PRICES below): a subscription's usage limit is consumed roughly
in proportion to it.
"""
import argparse
import calendar
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

CHARS_PER_TOKEN = 3.3  # rough average for code mixed with Uzbek/Russian text; used only for estimates
# A background subagent's transcript keeps only the streaming start of each call's usage (stop_reason null, output_tokens
# 1-30), so its output, mostly thinking that stays in the context, went uncounted (2026-10-20: 155 of 216 subagents since
# 10-14). That output reappears as the next call's context growth, beside the tool results and user text in between; at
# RESULT_CHARS_PER_TOKEN for those, the estimate matched the true output of 37 transcripts that keep the final usage.
RESULT_CHARS_PER_TOKEN = 2.1

# Anthropic API list prices, 2026-10-06; cache_write is the 5-minute write (1.25x input), a 1-hour write costs WRITE_1H x input
# $ per million tokens: (input, cache_write, cache_read, output). Key = substring of the model id; the longest matching key wins.
PRICES = {
    "fable": (10, 12.5, 0.25, 50),
    "opus-5-5": (4, 5, 0.20, 20),
    "opus": (5, 6.25, 0.50, 25),
    "sonnet-5": (2, 2.5, 0.20, 10),  # matches sonnet-5 and sonnet-5-5
    "sonnet": (3, 3.75, 0.30, 15),
    "haiku-5-5": (0.10, 0.125, 0.01, 0.50),
    "haiku": (1, 1.25, 0.10, 5),
}
# Claude Code writes the lead's cache with the 1-hour TTL and a subagent's with the 5-minute one (every transcript of 10-14);
# usage.cache_creation splits the two, and pricing both at 1.25x had hidden ~9% of a stage's $ (31% of a Fable lead's).
WRITE_1H = 2.0
UNPRICED = set()  # models already warned about
AGENT_TOOLS = ("Agent", "Task")  # tool_use names with which a lead spawns a subagent


def load(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:  # a transcript still being written may end in half a character
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def describe(name, inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "command", "pattern", "url", "query", "description", "prompt"):
        if k in inp:
            s = " ".join(str(inp[k]).split())
            if k == "command":
                s = re.sub(r"^cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*(&&|;)\s*", "", s)  # show the command, not the cd prefix
            s = s[:100]
            if name == "Read" and ("offset" in inp or "limit" in inp):
                s += " [%s+%s]" % (inp.get("offset", 1), inp.get("limit", "all"))
            return s
    return json.dumps(inp, ensure_ascii=False)[:100]


def price_for(model):
    for key in sorted(PRICES, key=len, reverse=True):
        if key in model:
            return PRICES[key]
    if model not in UNPRICED:
        UNPRICED.add(model)
        print("warning: no price for %s, using Sonnet 5.5 prices" % model)
    return PRICES["sonnet-5"]


def cost(u, model):
    """API-equivalent dollars of one call's usage (a call with no tokens, e.g. a <synthetic> row, costs nothing). The
    1-hour part of the cache write (usage.cache_creation.ephemeral_1h_input_tokens) costs WRITE_1H x input, the rest 1.25x."""
    tokens = (u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0),
              u.get("output_tokens", 0))
    if not any(tokens):
        return 0.0
    price = price_for(model)
    write_1h = min((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0, tokens[1])
    return (sum(t * p for t, p in zip(tokens, price)) + write_1h * (WRITE_1H * price[0] - price[1])) / 1e6


def analyze(path, skip=frozenset()):
    """The lead's (or a subagent's) numbers; calls whose message id is in `skip` (copied from an older transcript, see
    copied_ids) are left out of calls, context and $, and counted in copied/copied_usd instead. A call's usage is taken from
    its final row; a call that has only streamed rows gets its output estimated (see RESULT_CHARS_PER_TOKEN)."""
    rows = load(path)
    seen, calls, model = set(), [], None
    copied = []  # (usage, model) of the calls left out
    call_models = []  # message.model of each counted call, parallel to calls
    index = {}  # message id -> position in calls
    streamed, shown, between = [], [], []  # per call: no final row yet; its text and tool-input chars; user chars after it
    uses, sizes, order, issued = {}, {}, [], {}
    failed = set()  # tool_use ids whose result is an error; a failed Agent call (unknown agent type, denied) has no transcript
    readings, usage_ids = [], set()  # weekly all-models % from each get_usage tool result, in order
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "assistant":
            model = m.get("model") or model
            if m.get("id") in skip:  # a copied call: its tokens, tools and usage readings count in the older transcript
                if m.get("id") not in seen and m.get("usage"):
                    seen.add(m.get("id"))
                    copied.append((m["usage"], m.get("model") or model or "?"))
                continue
            if m.get("id") not in seen and m.get("usage"):
                seen.add(m.get("id"))
                index[m.get("id")] = len(calls)
                calls.append(m["usage"])
                call_models.append(m.get("model"))
                streamed.append(True)
                shown.append(0)
                between.append(0)
            i = index.get(m.get("id"))
            if i is not None and (m.get("stop_reason") or "stop_reason" not in m):  # a final row (rows without the key: trusted)
                calls[i] = m.get("usage") or calls[i]
                streamed[i] = False
            for c in m.get("content") or []:
                if i is not None and c.get("type") == "text":
                    shown[i] += len(c.get("text", ""))
                elif i is not None and c.get("type") == "tool_use":
                    shown[i] += len(json.dumps(c.get("input")))
                if c.get("type") == "tool_use":
                    uses[c["id"]] = (c["name"], describe(c["name"], c.get("input")), json.dumps(c.get("input"), sort_keys=True))
                    issued[c["id"]] = len(calls) - 1
                    order.append(c["id"])
                    if c["name"].endswith("get_usage"):
                        usage_ids.add(c["id"])
        elif r.get("type") == "user" and isinstance(m.get("content"), str) and calls:
            between[-1] += len(m["content"])
        elif r.get("type") == "user" and isinstance(m.get("content"), list):
            for c in m["content"]:
                if c.get("type") == "text" and calls:
                    between[-1] += len(c.get("text", ""))
                if c.get("type") == "tool_result":
                    if c.get("is_error"):
                        failed.add(c["tool_use_id"])
                    cc = c.get("content")
                    sizes[c["tool_use_id"]] = (sum(len(x.get("text", "")) for x in cc if isinstance(x, dict))
                                               if isinstance(cc, list) else len(str(cc or "")))
                    if calls:
                        between[-1] += sizes[c["tool_use_id"]]
                    if c["tool_use_id"] in usage_ids and weekly_pct(cc) is not None:
                        readings.append(weekly_pct(cc))

    def ctx(u):
        return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)

    for i, u in enumerate(calls):
        if streamed[i]:  # the next call's context holds this call's output and the user content in between
            out = max(u.get("output_tokens", 0), shown[i] / CHARS_PER_TOKEN)
            if i + 1 < len(calls):
                out = max(out, ctx(calls[i + 1]) - ctx(u) - between[i] / RESULT_CHARS_PER_TOKEN)
            calls[i] = dict(u, output_tokens=round(out))

    by_tool = defaultdict(lambda: [0, 0])
    repeats = defaultdict(list)
    for tid in order:
        name, desc, raw = uses[tid]
        by_tool[name][0] += 1
        by_tool[name][1] += sizes.get(tid, 0)
        if name in ("Read", "Bash"):
            repeats[(name, raw)].append(sizes.get(tid, 0))
    return {
        "model": model or "?",
        "calls": len(calls),
        "startup": ctx(calls[0]) if calls else 0,
        "final": (ctx(calls[-1]) + calls[-1].get("output_tokens", 0)) if calls else 0,
        "avg_ctx": sum(ctx(u) for u in calls) / len(calls) if calls else 0,
        "cache_read": sum(u.get("cache_read_input_tokens", 0) for u in calls),
        "cache_write": sum(u.get("cache_creation_input_tokens", 0) for u in calls),
        "output": sum(u.get("output_tokens", 0) for u in calls),
        "estimated": sum(streamed),  # calls whose output is estimated
        "usd": sum(cost(u, cm or model or "?") for u, cm in zip(calls, call_models)),
        "copied": len(copied),
        "copied_usd": sum(cost(u, cm) for u, cm in copied),
        "usage_readings": readings,
        "agent_calls": len({t for t in order if uses[t][0] in AGENT_TOOLS and t not in failed}),
        "by_tool": dict(by_tool),
        # A tool output is re-sent (as cache reads) on every later call, so its real cost is size x later calls.
        "costliest": sorted(((sizes.get(t, 0) / CHARS_PER_TOKEN * max(0, len(calls) - 1 - issued[t]),
                              sizes.get(t, 0), issued[t] + 1, uses[t][0], uses[t][1]) for t in order), reverse=True),
        "repeats": [(uses_key[0], len(v), sum(v), json.loads(uses_key[1])) for uses_key, v in repeats.items() if len(v) > 1],
    }


def first_call(path):
    """(timestamp, message id) of the transcript's first API call, else None."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"usage"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            m = r.get("message") if isinstance(r, dict) else None
            if r.get("type") == "assistant" and isinstance(m, dict) and m.get("id") and m.get("usage"):
                return str(r.get("timestamp") or ""), str(m["id"])
    return None


def call_ids(path):
    return {r["message"]["id"] for r in load(path) if isinstance(r, dict) and r.get("type") == "assistant"
            and isinstance(r.get("message"), dict) and r["message"].get("id") and r["message"].get("usage")}


def copied_ids(session):
    """Message ids of the calls this transcript shares with an older transcript. Continuing or forking a conversation writes
    a new transcript (sometimes under another project) that starts with the parent's rows, message ids included, so both
    rows counted the same API calls (2026-10-13: 10 transcripts held $218 of copies). A call counts in the oldest transcript
    holding it: the one whose first call is earlier (for an exact copy, the smaller path). Only transcripts that began
    earlier and were still written after this one began are read, and only one whose text holds this transcript's first
    call id as a JSON value is parsed (a quoted id inside a tool output is escaped and does not match)."""
    session = os.path.abspath(session)
    mine = first_call(session)
    if not mine:
        return set()
    try:
        began = calendar.timegm(time.strptime(mine[0][:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        began = 0
    needle = json.dumps(mine[1]).encode()
    ids = set()
    root = os.path.dirname(os.path.dirname(session))  # the projects dir: every project's transcripts
    for other in glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl")):
        other = os.path.abspath(other)
        try:
            if other == session or os.path.getmtime(other) < began - 60:
                continue
            theirs = first_call(other)
            if not theirs or (theirs[0], other) >= (mine[0], session):
                continue
            with open(other, "rb") as f:
                if needle not in f.read():
                    continue
            ids |= call_ids(other)
        except OSError:
            continue
    return ids


def weekly_pct(content):
    """The weekly all-models allowance used (%) in a get_usage tool result (JSON, possibly inside text blocks), else None."""
    text = " ".join(x.get("text", "") for x in content if isinstance(x, dict)) if isinstance(content, list) else str(content or "")
    try:
        windows = json.loads(text).get("plan", {}).get("windows", [])
    except (ValueError, AttributeError):
        return None
    for w in windows if isinstance(windows, list) else []:
        if isinstance(w, dict) and "all models" in str(w.get("label", "")).lower() and isinstance(w.get("percentUsed"), (int, float)):
            return float(w["percentUsed"])
    return None


def k(n):
    if n >= 1_000_000:
        return "%.2fM" % (n / 1e6)
    return "%dk" % round(n / 1000) if n >= 1000 else str(n)


def resolve(path, previous=False):
    """The session .jsonl to analyze; for a project directory the newest by mtime, or the second-newest with previous."""
    path = os.path.expanduser(path.rstrip("/"))
    if path.endswith(".jsonl") or (os.path.isdir(path) and os.path.exists(path + ".jsonl")):
        if previous:
            print("note: --previous only applies to a project directory; using the session you named", file=sys.stderr)
        return path if path.endswith(".jsonl") else path + ".jsonl"
    sessions = sorted(glob.glob(os.path.join(path, "*.jsonl")), key=lambda p: (os.path.getmtime(p), p), reverse=True)
    if not sessions:
        sys.exit("no session .jsonl found at %s" % path)
    if previous:
        if len(sessions) < 2:
            sys.exit("--previous needs at least two sessions in %s, found only %s" % (path, os.path.basename(sessions[0])))
        return sessions[1]
    return sessions[0]


def session_title(session):
    """The session's custom title (<session-dir>/custom-title.json, key customTitle), or ''."""
    try:
        with open(os.path.join(os.path.abspath(session)[:-len(".jsonl")], "custom-title.json"), encoding="utf-8") as f:
            return " ".join(str(json.load(f).get("customTitle") or "").split())
    except (OSError, ValueError, AttributeError):
        return ""


METRICS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "delegate-metrics.tsv")
COLUMNS = ["date", "project", "session", "title", "lead_model", "lead_calls", "lead_final_k", "lead_cache_read_M",
           "subagents", "general_purpose", "sub_startup_avg_k", "sub_cache_read_M", "lead_usd", "sub_usd",
           "usage_before", "usage_after"]  # the last two: weekly all-models allowance used (%) before and after the stage


def brief_and_report(path):
    """The subagent's brief (first user message) and its final words (text or SubagentHandback)."""
    brief, report = "", ""
    for r in load(path):
        m = r.get("message") or {}
        content = m.get("content")
        if r.get("type") == "user" and not brief:
            brief = content if isinstance(content, str) else "\n".join(
                x.get("text", "") for x in content or [] if isinstance(x, dict) and x.get("type") == "text")
        elif r.get("type") == "assistant":
            parts = [x.get("text", "") for x in content or [] if x.get("type") == "text"]
            parts += [str((x.get("input") or {}).get("message", "")) for x in content or []
                      if x.get("type") == "tool_use" and x.get("name") == "SubagentHandback"]
            if any(p.strip() for p in parts):
                report = "\n".join(p for p in parts if p.strip())
    return brief, report


def lead_followup(lead_rows, agent_id):
    """What the lead did right after the agent's hand-back (the last non-assistant row mentioning the agent,
    e.g. the task notification), and how often it sent the agent more work. Evidence for the quality review."""
    last, sent_back = None, 0
    for i, r in enumerate(lead_rows):
        if r.get("type") == "assistant":
            sent_back += sum(1 for c in (r.get("message") or {}).get("content") or [] if c.get("type") == "tool_use"
                             and c.get("name") == "SendMessage" and agent_id in json.dumps(c.get("input")))
        elif agent_id in json.dumps(r, ensure_ascii=False):
            last = i
    words, actions = "", []
    for r in lead_rows[last + 1:] if last is not None else []:
        if r.get("type") != "assistant":
            continue
        for c in (r.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and c.get("text", "").strip() and not words:
                words = " ".join(c["text"].split())[:400]
            elif c.get("type") == "tool_use" and len(actions) < 4:
                actions.append("%s: %s" % (c["name"], describe(c["name"], c.get("input"))))
        if words and len(actions) >= 4:
            break
    return words, actions, sent_back


def project_name(session):
    name = os.path.basename(os.path.dirname(os.path.abspath(session)))
    home = os.path.expanduser("~").replace("/", "-")
    if name.startswith(home + "-"):
        name = name[len(home) + 1:]
    return name[len("Projects-"):] if name.startswith("Projects-") else name


def read_meta(transcript):
    """A subagent's meta.json (next to its transcript) as a dict; {} when it is missing or unreadable."""
    try:
        with open(transcript[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def subagent_files(session):
    """The subagent transcripts of a session .jsonl (<session>/subagents/agent-*.jsonl), oldest first."""
    sub_dir = os.path.join(session[:-len(".jsonl")], "subagents")
    return sorted(glob.glob(os.path.join(glob.escape(sub_dir), "agent-*.jsonl")), key=os.path.getmtime)


def find_subagent(session_jsonl, tool_use_id=None, agent_id=None):
    """(meta, transcript path) of a subagent of the session: the one whose meta.json has this toolUseId, else the one with
    this agent id (agent-<id>.jsonl; its meta may be missing, then {}). (None, None) when there is no such transcript."""
    if tool_use_id:
        for p in subagent_files(session_jsonl):
            meta = read_meta(p)
            if meta.get("toolUseId") == tool_use_id:
                return meta, p
    if agent_id and re.fullmatch(r"[\w-]+", str(agent_id)):  # the id may come from a prompt: it must not leave the directory
        p = os.path.join(session_jsonl[:-len(".jsonl")], "subagents", "agent-%s.jsonl" % agent_id)
        if os.path.isfile(p):
            return read_meta(p), p
    return None, None


def summarize_agent(meta, transcript, lead_session, a=None):
    """One line for the lead's context: what a subagent cost, and the lead's running total (a = analyze(transcript) if known)."""
    a = a or analyze(transcript)
    line = 'delegate-cost: %s "%s": ~$%.2f, %d calls, final ctx %s, cache-read %s (%s).' % (
        meta.get("agentType") or "?", " ".join(str(meta.get("description") or "").split())[:40], a["usd"], a["calls"],
        k(a["final"]), k(a["cache_read"]), a["model"].replace("claude-", ""))
    try:
        lead = analyze(lead_session)
    except Exception:  # an advisory line: an unreadable lead transcript only drops the running total
        return line
    return line + " Lead so far: ~$%.2f, %d calls, ctx %s." % (lead["usd"], lead["calls"], k(lead["final"]))


def start_date(session):
    """Local YYYY-MM-DD of the first row with a "timestamp" (when the session began), else of the file's mtime."""
    with open(session, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ts = json.loads(line).get("timestamp")
            except (ValueError, AttributeError):  # a blank or broken line, or JSON that is not an object
                continue
            if isinstance(ts, str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts):
                try:  # transcripts stamp UTC ("...Z"); the row should carry the local date of the session's start
                    return time.strftime("%Y-%m-%d", time.localtime(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))))
                except ValueError:
                    return ts[:10]
            if isinstance(ts, str) and re.match(r"\d{4}-\d{2}-\d{2}", ts):
                return ts[:10]
    return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(session)))


def session_id(session):
    return os.path.basename(session)[:8]


def read_metrics():
    """The rows of the metrics file as dicts ([] if there is none); a file with fewer columns still loads, the new cells stay empty."""
    if not os.path.exists(METRICS):
        return []
    with open(METRICS, encoding="utf-8") as f:
        lines = f.read().splitlines()
    header = lines[0].split("\t") if lines else []
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line.strip()]


def logged_row(session):
    """The session's row in the metrics file, or {}."""
    return next((r for r in read_metrics() if r.get("session") == session_id(os.path.abspath(session))), {})


def merge_usage(old, usage_before, usage_after, readings=()):
    """The (usage_before, usage_after) cells of a session's row: a value that is given wins; then the first and the last
    plan-usage reading the lead made in the session (get_usage tool results; one reading fills 'before' only); then what
    the old row already has."""
    readings = list(readings or [])
    auto = {"usage_before": readings[0] if readings else None, "usage_after": readings[-1] if len(readings) > 1 else None}
    return tuple("%g" % v if v is not None else ("%g" % auto[c] if auto[c] is not None else old.get(c, ""))
                 for c, v in (("usage_before", usage_before), ("usage_after", usage_after)))


def calibration(usage_before, usage_after, total_usd):
    """The 'limit calibration' line (how much of the weekly allowance one API-equivalent dollar used), or None if it can't be told."""
    try:
        used = float(usage_after) - float(usage_before)
    except (TypeError, ValueError):  # one of the two is not known
        return None
    if used <= 0 or total_usd <= 0:
        return None
    return "limit calibration: +%g%% of the weekly allowance for $%.2f total -> %.3g %%/$ (about $%.2f per 1%%)" % (
        used, total_usd, used / total_usd, total_usd / used)


def stage_table(rows, project=None):
    """The logged sessions as a comparison table: $ per stage, the change against the previous priced row, and the
    limit calibration (%/$) where usage before and after were logged. project filters the rows (None: every project)."""
    rows = sorted((r for r in rows if not project or r.get("project") == project), key=lambda r: r.get("date", ""))  # stable: catch-up rows land in date order
    if not rows:
        return "no logged sessions" + (" for project %s" % project if project else "")
    head = "%-10s %-8s %-9s %-40s %5s %6s %7s %7s %7s %5s %6s  %s" % (
        "date", "session", "project", "title", "calls", "ctx k", "lead $", "subs $", "total $", "chg", "%/$", "usage")
    lines, prev = [head], None
    for r in rows:
        try:
            lead_usd, sub_usd = float(r.get("lead_usd") or 0), float(r.get("sub_usd") or 0)
        except ValueError:
            lead_usd = sub_usd = 0.0
        total = lead_usd + sub_usd
        change = "%+.0f%%" % ((total - prev) / prev * 100) if prev and total else ""
        before, after = r.get("usage_before", ""), r.get("usage_after", "")
        try:
            rate = "%.3g" % ((float(after) - float(before)) / total) if before and after and total else ""
        except (ValueError, ZeroDivisionError):
            rate = ""
        usage = "%s->%s" % (before, after) if before or after else ""
        lines.append("%-10s %-8s %-9s %-40s %5s %6s %7.2f %7.2f %7.2f %5s %6s  %s" % (
            r.get("date", ""), r.get("session", ""), r.get("project", "")[:9], r.get("title", "")[:40], r.get("lead_calls", ""),
            r.get("lead_final_k", ""), lead_usd, sub_usd, total, change, rate, usage))
        if total:
            prev = total
    return "\n".join(lines)


def write_report(project=None):
    """Keep delegate-report.txt (next to the metrics file) as the current stage table; returns its path."""
    path = os.path.join(os.path.dirname(METRICS), "delegate-report.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("delegate report, updated %s%s\n\n%s\n" % (
            time.strftime("%Y-%m-%d %H:%M"), " (project %s)" % project if project else "", stage_table(read_metrics(), project)))
    return path


def pct(s):
    """argparse type: a percentage such as 45, 45.5 or 45%."""
    try:
        v = float(s.rstrip("%"))
    except ValueError:
        v = -1
    if not 0 <= v <= 100:  # also rejects nan
        raise argparse.ArgumentTypeError("%r is not a percentage between 0 and 100" % s)
    return v


def log_metrics(session, lead, subs_info, usage_before=None, usage_after=None, quiet=False):
    """Add or replace the session's row. A usage value that is not given keeps the one already logged for the session
    (so 'before' can be logged at the start of a stage and 'after' at its end, and the SessionEnd hook never wipes them).
    quiet=True skips the 'logged to' line (a hook must not talk)."""
    session = os.path.abspath(session)
    startups = [a["startup"] for _, a in subs_info]
    row = {
        "date": start_date(session),
        "project": project_name(session),
        "session": session_id(session),
        "title": session_title(session)[:60],
        "lead_model": lead["model"].replace("claude-", ""),
        "lead_calls": lead["calls"],
        "lead_final_k": round(lead["final"] / 1000),
        "lead_cache_read_M": round(lead["cache_read"] / 1e6, 2),
        "subagents": len(subs_info),
        "general_purpose": sum(1 for meta, _ in subs_info if meta.get("agentType") == "general-purpose"),
        "sub_startup_avg_k": round(sum(startups) / len(startups) / 1000) if startups else 0,
        "sub_cache_read_M": round(sum(a["cache_read"] for _, a in subs_info) / 1e6, 2),
        "lead_usd": "%.2f" % lead["usd"],
        "sub_usd": "%.2f" % sum(a["usd"] for _, a in subs_info),
    }
    rows = read_metrics()
    old = next((r for r in rows if r.get("session") == row["session"]), {})
    row["usage_before"], row["usage_after"] = merge_usage(old, usage_before, usage_after, lead.get("usage_readings"))
    if old:  # re-logging a session replaces its row in place, so the table keeps its order
        rows = [row if r.get("session") == row["session"] else r for r in rows]
    else:
        rows.append(row)
    target = os.path.realpath(METRICS)  # a symlinked file stays a symlink
    tmp = "%s.%d.tmp" % (target, os.getpid())  # write aside, then replace: hooks of sessions ending together never see a half-written file
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\t".join(COLUMNS) + "\n")
            for r in rows:
                f.write("\t".join(str(r.get(c, "")) for c in COLUMNS) + "\n")
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    mark_seen({session: os.path.getmtime(session)})
    if not quiet:
        print("\nlogged to %s (%d sessions)" % (METRICS, len(rows)))


MIN_LEAD_CALLS = 30  # a shorter session without subagents is not worth a row (the SessionEnd hook and catch_up use it)


def log_if_substantial(session, quiet=True):
    """Log the session the way the SessionEnd hook does, when it had >= MIN_LEAD_CALLS lead calls or any subagent, or already
    has a row (a fork whose copied calls no longer count); True if logged."""
    lead = analyze(session, copied_ids(session))
    subs = subagent_files(session)
    if lead["calls"] < MIN_LEAD_CALLS and not subs and not logged_row(session):
        return False
    log_metrics(session, lead, [(read_meta(p), analyze(p)) for p in subs], quiet=quiet)
    return True


def seen_path():
    return os.path.join(os.path.dirname(METRICS), "delegate-catchup.json")  # transcript path -> mtime when it was last examined


def read_seen():
    try:
        with open(seen_path(), encoding="utf-8") as f:
            seen = json.load(f)
        return seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_seen(update):
    seen = read_seen()
    seen.update((os.path.abspath(p), m) for p, m in update.items())
    tmp = "%s.%d.tmp" % (seen_path(), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.replace(tmp, seen_path())


def catch_up(project_dir=None, exclude=None, budget=None, days=14):
    """Log the sessions the SessionEnd hook missed (left open, closed without the hook, resumed and grown): every transcript
    under project_dir (default: every project under the config dir) changed in the last `days` days and newer than when it
    was last examined is logged when it is substantial, as the hook does; `exclude` (the running session) is skipped; the
    scan stops after `budget` seconds and continues next time. Returns the ids it logged."""
    start = time.time()
    dirs = [project_dir] if project_dir else glob.glob(os.path.join(glob.escape(os.path.join(os.path.dirname(METRICS), "projects")), "*"))
    seen, examined, logged = read_seen(), {}, []
    exclude = os.path.abspath(exclude) if exclude else None
    cutoff = time.time() - days * 86400
    for d in dirs:
        for path in sorted(glob.glob(os.path.join(glob.escape(d), "*.jsonl")), key=os.path.getmtime, reverse=True):
            path = os.path.abspath(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if path == exclude or mtime < cutoff or seen.get(path, -1) >= mtime:
                continue
            if budget is not None and time.time() - start > budget:
                break
            try:
                if log_if_substantial(path):
                    logged.append(session_id(path))
            except Exception as e:  # one broken transcript must not stop the others
                print("warning: could not log %s: %s" % (path, e), file=sys.stderr)
            examined[path] = mtime
    if examined:
        mark_seen(examined)
    return logged


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="session .jsonl, session dir or project dir (not needed with --table)")
    ap.add_argument("--table", nargs="?", const="", metavar="PROJECT",
                    help="print the comparison table of logged stages (optionally one project) and exit; the SessionEnd hook keeps the same table in delegate-report.txt")
    ap.add_argument("--previous", action="store_true",
                    help="for a project directory: the second-newest session (the newest is the one running the retro)")
    ap.add_argument("--top", type=int, default=5, help="costliest tool outputs to list per session (default 5)")
    ap.add_argument("--reports", action="store_true", help="also print each subagent's brief (start) and final report")
    ap.add_argument("--log", action="store_true",
                    help="add or update this session's row in delegate-metrics.tsv in the Claude config dir (~/.claude)")
    ap.add_argument("--usage-before", type=pct, metavar="PCT",
                    help="weekly all-models allowance used (%%) before the stage (/usage); stored by --log")
    ap.add_argument("--usage-after", type=pct, metavar="PCT",
                    help="the same after the stage; with both known, a limit calibration line (%%/$) is printed")
    args = ap.parse_args()
    if args.table is not None:
        logged = catch_up()
        if logged:
            print("caught up %d session(s) the SessionEnd hook had missed: %s\n" % (len(logged), " ".join(logged)))
        print(stage_table(read_metrics(), args.table or None))
        return
    if not args.path:
        ap.error("path is required (or use --table)")
    if (args.usage_before is not None or args.usage_after is not None) and not args.log:
        print("note: --usage-before/--usage-after are stored only with --log", file=sys.stderr)

    session = resolve(args.path, args.previous)
    lead = analyze(session, copied_ids(session))
    title = session_title(session)
    print("Session %s  (%s)%s" % (os.path.basename(session), lead["model"], '  "%s"' % title if title else ""))
    print("  calls %d | startup %s | final context %s | cache-read %s | cache-write %s | output %s | ~$%.2f | avg ctx/call %s" % (
        lead["calls"], k(lead["startup"]), k(lead["final"]), k(lead["cache_read"]), k(lead["cache_write"]), k(lead["output"]),
        lead["usd"], k(lead["avg_ctx"])))
    if lead["copied"]:
        print("  not counted here: %d calls ($%.2f) copied from an older transcript (a continued or forked conversation)"
              % (lead["copied"], lead["copied_usd"]))
    if lead["usage_readings"]:
        print("  plan usage read in the session (weekly, all models): %s" % " -> ".join("%g%%" % r for r in lead["usage_readings"]))
    total_out = sum(v[1] for v in lead["by_tool"].values()) or 1
    tools = sorted(lead["by_tool"].items(), key=lambda kv: -kv[1][1])[:6]
    print("  tool output by tool: " + ", ".join("%s x%d %s chars (%d%%)" % (n, c, k(ch), 100 * ch // total_out) for n, (c, ch) in tools))
    print("  costliest tool outputs (size x later calls that re-read it):")
    for cost, size, call_no, name, desc in lead["costliest"][:args.top]:
        print("    ~%-7s re-read  %8s chars @ call %d/%d  %-5s %s" % (
            k(cost), "{:,}".format(size), call_no, lead["calls"], name, desc))
    for name, times, chars, inp in sorted(lead["repeats"], key=lambda x: -x[2])[:5]:
        print("  identical %s repeated x%d (%s chars): %s" % (name, times, "{:,}".format(chars), describe(name, inp)))

    sub_dir = os.path.join(session[:-len(".jsonl")], "subagents")
    subs = subagent_files(session)
    subs_info, hints = [], []
    if subs:
        print("\nSubagents (%d):" % len(subs))
        print("  %-17s %-11s %5s %8s %7s %10s %7s %7s  %s" % (
            "type", "model", "calls", "startup", "final", "cache-read", "output", "$", "description"))
    else:
        print("\nNo subagents.")
    for p in subs:
        meta = read_meta(p)
        a = analyze(p)
        subs_info.append((meta, a))
        atype = meta.get("agentType", "?")
        print("  %-17s %-11s %5d %8s %7s %10s %7s %7.2f  %s" % (
            atype[:17], a["model"].replace("claude-", "")[:11], a["calls"], k(a["startup"]), k(a["final"]),
            k(a["cache_read"]), k(a["output"]), a["usd"], meta.get("description", "")[:50]))
        if atype == "general-purpose" and a["startup"] > 40000:
            hints.append("'%s' ran as general-purpose (startup %s): a lean agent with a tools allowlist starts at ~5k + CLAUDE.md."
                         % (meta.get("description", "?"), k(a["startup"])))
    estimated = lead["estimated"] + sum(a["estimated"] for _, a in subs_info)
    if estimated:
        print("note: the output of %d calls is estimated from the next call's context growth (a background subagent's "
              "transcript keeps only the streaming usage; within ~10%% on transcripts that keep both)" % estimated)
    if lead["agent_calls"] > len(subs):
        print("warning: the lead spawned %d agents but %d subagent transcripts were found under %s; "
              "either the session was resumed under a new id (earlier agents sit under the original id) or the transcript layout "
              "changed (script written for Claude Code 2.1.x)" % (lead["agent_calls"], len(subs), sub_dir))
    if args.reports and subs:
        lead_rows = load(session)
        for p, (meta, _a) in zip(subs, subs_info):
            brief, report = brief_and_report(p)
            words, actions, sent_back = lead_followup(lead_rows, os.path.basename(p)[len("agent-"):-len(".jsonl")])
            print("\n--- %s (%s)" % (meta.get("description", os.path.basename(p)), meta.get("agentType", "?")))
            print("brief: %d chars | %s …" % (len(brief), " ".join(brief.split())[:300]))
            print("report:\n  " + (report.strip()[:2000] or "(none)").replace("\n", "\n  "))
            print("lead after the hand-back (sent back to the agent %d time(s)):" % sent_back)
            for a in actions:
                print("  next action: " + a)
            print("  next words: " + (words or "(none found)"))
    if subs:
        sub_usd = sum(a["usd"] for _, a in subs_info)
        print("\nCost (API-equivalent): lead $%.2f | subagents $%.2f | total $%.2f   (cache-read: lead %s vs subagents %s)" % (
            lead["usd"], sub_usd, lead["usd"] + sub_usd, k(lead["cache_read"]), k(sum(a["cache_read"] for _, a in subs_info))))
    # with --log, a value logged earlier completes the pair (e.g. 'before' logged at the start of the stage)
    usage = merge_usage(logged_row(session) if args.log else {}, args.usage_before, args.usage_after, lead["usage_readings"])
    line = calibration(usage[0], usage[1], lead["usd"] + sum(a["usd"] for _, a in subs_info))
    if line:
        print(line)
    if lead["final"] > 300000:
        hints.append("The lead's context reached %s; everything it read early was re-read on each later call. "
                     "Push bulk reading to scouts or briefs, or split the work at a boundary." % k(lead["final"]))
    if lead["calls"] > 150:
        hints.append("%d lead calls at avg ctx %s: batch independent commands into one Bash call and make independent tool calls "
                     "in parallel; every call re-sends the whole context." % (lead["calls"], k(lead["avg_ctx"])))
    for h in hints:
        print("hint: " + h)
    if args.log:
        log_metrics(session, lead, subs_info, args.usage_before, args.usage_after)


if __name__ == "__main__":
    main()
