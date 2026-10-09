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
allowance one API-equivalent dollar used: the real exchange rate between "$" below and the usage limit.

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

# Anthropic API list prices, 2026-10-06; cache_write assumed 1.25x input
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
    """API-equivalent dollars of one call's usage (a call with no tokens, e.g. a <synthetic> row, costs nothing)."""
    tokens = (u.get("input_tokens", 0), u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0),
              u.get("output_tokens", 0))
    return sum(t * p for t, p in zip(tokens, price_for(model))) / 1e6 if any(tokens) else 0.0


def analyze(path):
    rows = load(path)
    seen, calls, model = set(), [], None
    call_models = []  # message.model of each counted call, parallel to calls
    uses, sizes, order, issued = {}, {}, [], {}
    failed = set()  # tool_use ids whose result is an error; a failed Agent call (unknown agent type, denied) has no transcript
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "assistant":
            model = m.get("model") or model
            if m.get("id") not in seen and m.get("usage"):
                seen.add(m.get("id"))
                calls.append(m["usage"])
                call_models.append(m.get("model"))
            for c in m.get("content") or []:
                if c.get("type") == "tool_use":
                    uses[c["id"]] = (c["name"], describe(c["name"], c.get("input")), json.dumps(c.get("input"), sort_keys=True))
                    issued[c["id"]] = len(calls) - 1
                    order.append(c["id"])
        elif r.get("type") == "user" and isinstance(m.get("content"), list):
            for c in m["content"]:
                if c.get("type") == "tool_result":
                    if c.get("is_error"):
                        failed.add(c["tool_use_id"])
                    cc = c.get("content")
                    sizes[c["tool_use_id"]] = (sum(len(x.get("text", "")) for x in cc if isinstance(x, dict))
                                               if isinstance(cc, list) else len(str(cc or "")))

    def ctx(u):
        return u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)

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
        "usd": sum(cost(u, cm or model or "?") for u, cm in zip(calls, call_models)),
        "agent_calls": len({t for t in order if uses[t][0] in AGENT_TOOLS and t not in failed}),
        "by_tool": dict(by_tool),
        # A tool output is re-sent (as cache reads) on every later call, so its real cost is size x later calls.
        "costliest": sorted(((sizes.get(t, 0) / CHARS_PER_TOKEN * max(0, len(calls) - 1 - issued[t]),
                              sizes.get(t, 0), issued[t] + 1, uses[t][0], uses[t][1]) for t in order), reverse=True),
        "repeats": [(uses_key[0], len(v), sum(v), json.loads(uses_key[1])) for uses_key, v in repeats.items() if len(v) > 1],
    }


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


def merge_usage(old, usage_before, usage_after):
    """The (usage_before, usage_after) cells of a session's row: a value that is given wins, else the one already logged in old."""
    return tuple("%g" % v if v is not None else old.get(c, "") for c, v in (("usage_before", usage_before), ("usage_after", usage_after)))


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
    row["usage_before"], row["usage_after"] = merge_usage(old, usage_before, usage_after)
    rows = [r for r in rows if r.get("session") != row["session"]] + [row]  # re-logging a session replaces its row
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
    if not quiet:
        print("\nlogged to %s (%d sessions)" % (METRICS, len(rows)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
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
    if (args.usage_before is not None or args.usage_after is not None) and not args.log:
        print("note: --usage-before/--usage-after are stored only with --log", file=sys.stderr)

    session = resolve(args.path, args.previous)
    lead = analyze(session)
    title = session_title(session)
    print("Session %s  (%s)%s" % (os.path.basename(session), lead["model"], '  "%s"' % title if title else ""))
    print("  calls %d | startup %s | final context %s | cache-read %s | cache-write %s | output %s | ~$%.2f | avg ctx/call %s" % (
        lead["calls"], k(lead["startup"]), k(lead["final"]), k(lead["cache_read"]), k(lead["cache_write"]), k(lead["output"]),
        lead["usd"], k(lead["avg_ctx"])))
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
    usage = merge_usage(logged_row(session) if args.log else {}, args.usage_before, args.usage_after)
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
