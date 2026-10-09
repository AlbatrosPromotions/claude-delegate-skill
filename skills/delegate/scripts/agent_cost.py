#!/usr/bin/env python3
"""Where did a Claude Code session's tokens go? Reports the lead session and each of its subagents.

PATH may be a session .jsonl, a session directory, a subagent .jsonl, or a project directory under
~/.claude/projects (its newest session is used).

  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS
  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS/<session-id>.jsonl --top 8
  agent_cost.py ~/.claude/projects/-Users-me-Projects-EDMS --reports --log   # after a stage (used by /delegate-retro)

--reports prints each subagent's brief and final report; --log adds the session to metrics.tsv
next to the skill (re-logging the same session replaces its row), so trends are visible over time.

"startup" is the context of the first API call (system prompt + tool schemas + CLAUDE.md + prompt);
"final" is the context of the last call (roughly what the UI shows as an agent's tokens);
"cache-read" is the sum over all calls, which is what a long, large context really costs;
"re-read" estimates what one tool output cost: its size (in tokens) times the number of later calls.
"""
import argparse
import glob
import json
import os
import re
import sys
import time
from collections import defaultdict

CHARS_PER_TOKEN = 3.3  # rough average for code mixed with Uzbek/Russian text; used only for estimates


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
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


def analyze(path):
    rows = load(path)
    seen, calls, model = set(), [], None
    uses, sizes, order, issued = {}, {}, [], {}
    for r in rows:
        m = r.get("message") or {}
        if r.get("type") == "assistant":
            model = m.get("model") or model
            if m.get("id") not in seen and m.get("usage"):
                seen.add(m.get("id"))
                calls.append(m["usage"])
            for c in m.get("content") or []:
                if c.get("type") == "tool_use":
                    uses[c["id"]] = (c["name"], describe(c["name"], c.get("input")), json.dumps(c.get("input"), sort_keys=True))
                    issued[c["id"]] = len(calls) - 1
                    order.append(c["id"])
        elif r.get("type") == "user" and isinstance(m.get("content"), list):
            for c in m["content"]:
                if c.get("type") == "tool_result":
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
        "cache_read": sum(u.get("cache_read_input_tokens", 0) for u in calls),
        "cache_write": sum(u.get("cache_creation_input_tokens", 0) for u in calls),
        "output": sum(u.get("output_tokens", 0) for u in calls),
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


def resolve(path):
    path = os.path.expanduser(path.rstrip("/"))
    if path.endswith(".jsonl"):
        return path
    if os.path.isdir(path) and os.path.exists(path + ".jsonl"):
        return path + ".jsonl"
    sessions = glob.glob(os.path.join(path, "*.jsonl"))
    if not sessions:
        sys.exit("no session .jsonl found at %s" % path)
    return max(sessions, key=os.path.getmtime)


METRICS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metrics.tsv")
COLUMNS = ["date", "project", "session", "title", "lead_model", "lead_calls", "lead_final_k", "lead_cache_read_M",
           "subagents", "general_purpose", "sub_startup_avg_k", "sub_cache_read_M"]


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


def log_metrics(session, lead, subs_info):
    session = os.path.abspath(session)
    title_file = os.path.join(session[:-len(".jsonl")], "custom-title.json")
    title = ""
    if os.path.exists(title_file):
        with open(title_file, encoding="utf-8") as f:
            title = json.load(f).get("customTitle", "")
    startups = [a["startup"] for _, a in subs_info]
    row = {
        "date": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(session))),
        "project": project_name(session),
        "session": os.path.basename(session)[:8],
        "title": " ".join(title.split())[:60],
        "lead_model": lead["model"].replace("claude-", ""),
        "lead_calls": lead["calls"],
        "lead_final_k": round(lead["final"] / 1000),
        "lead_cache_read_M": round(lead["cache_read"] / 1e6, 2),
        "subagents": len(subs_info),
        "general_purpose": sum(1 for meta, _ in subs_info if meta.get("agentType") == "general-purpose"),
        "sub_startup_avg_k": round(sum(startups) / len(startups) / 1000) if startups else 0,
        "sub_cache_read_M": round(sum(a["cache_read"] for _, a in subs_info) / 1e6, 2),
    }
    rows = []
    if os.path.exists(METRICS):
        with open(METRICS, encoding="utf-8") as f:
            rows = [dict(zip(COLUMNS, line.split("\t"))) for line in f.read().splitlines()[1:] if line.strip()]
    rows = [r for r in rows if r.get("session") != row["session"]] + [row]  # re-logging a session replaces its row
    with open(METRICS, "w", encoding="utf-8") as f:
        f.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in COLUMNS) + "\n")
    print("\nlogged to %s (%d sessions)" % (METRICS, len(rows)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--top", type=int, default=5, help="costliest tool outputs to list per session (default 5)")
    ap.add_argument("--reports", action="store_true", help="also print each subagent's brief (start) and final report")
    ap.add_argument("--log", action="store_true", help="add or update this session's row in metrics.tsv next to the skill")
    args = ap.parse_args()

    session = resolve(args.path)
    lead = analyze(session)
    print("Session %s  (%s)" % (os.path.basename(session), lead["model"]))
    print("  calls %d | startup %s | final context %s | cache-read %s | cache-write %s | output %s" % (
        lead["calls"], k(lead["startup"]), k(lead["final"]), k(lead["cache_read"]), k(lead["cache_write"]), k(lead["output"])))
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
    subs = sorted(glob.glob(os.path.join(sub_dir, "agent-*.jsonl")), key=os.path.getmtime)
    subs_info, hints = [], []
    if subs:
        print("\nSubagents (%d):" % len(subs))
        print("  %-17s %-11s %5s %8s %7s %10s %7s  %s" % ("type", "model", "calls", "startup", "final", "cache-read", "output", "description"))
    else:
        print("\nNo subagents.")
    for p in subs:
        meta = {}
        if os.path.exists(p[:-len(".jsonl")] + ".meta.json"):
            with open(p[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as f:
                meta = json.load(f)
        a = analyze(p)
        subs_info.append((meta, a))
        atype = meta.get("agentType", "?")
        print("  %-17s %-11s %5d %8s %7s %10s %7s  %s" % (
            atype[:17], a["model"].replace("claude-", "")[:11], a["calls"], k(a["startup"]), k(a["final"]),
            k(a["cache_read"]), k(a["output"]), meta.get("description", "")[:50]))
        if atype == "general-purpose" and a["startup"] > 40000:
            hints.append("'%s' ran as general-purpose (startup %s): a lean agent with a tools allowlist starts at ~5k + CLAUDE.md."
                         % (meta.get("description", "?"), k(a["startup"])))
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
        print("\nCache-read: lead %s vs all subagents %s." % (k(lead["cache_read"]), k(sum(a["cache_read"] for _, a in subs_info))))
    if lead["final"] > 300000:
        hints.append("The lead's context reached %s; everything it read early was re-read on each later call. "
                     "Push bulk reading to scouts or briefs, or split the work at a boundary." % k(lead["final"]))
    for h in hints:
        print("hint: " + h)
    if args.log:
        log_metrics(session, lead, subs_info)


if __name__ == "__main__":
    main()
