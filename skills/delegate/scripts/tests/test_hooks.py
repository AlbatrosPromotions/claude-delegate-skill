"""Tests for hooks.py, run the way Claude Code runs it: a subprocess with the event as JSON on stdin and the answer as JSON on stdout."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
import agent_cost  # noqa: E402  (its COLUMNS and PRICES)

HOOKS = os.path.join(SCRIPTS, "hooks.py")
MODEL = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5-20251001"
SMALL = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000, "output_tokens": 10}
U1 = {"input_tokens": 1000, "cache_creation_input_tokens": 400000, "cache_read_input_tokens": 0, "output_tokens": 20000}
U2 = {"input_tokens": 200, "cache_creation_input_tokens": 50000, "cache_read_input_tokens": 400000, "output_tokens": 10000}


def assistant(mid, u, model=MODEL):
    return {"type": "assistant", "message": {"id": mid, "model": model, "role": "assistant", "content": [], "usage": u}}


def usd(price, *usages):
    return sum(u["input_tokens"] * price[0] + u["cache_creation_input_tokens"] * price[1] + u["cache_read_input_tokens"] * price[2]
               + u["output_tokens"] * price[3] for u in usages) / 1e6


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


class HookTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.config = os.path.join(self.dir, "config")  # CLAUDE_CONFIG_DIR: the metrics file lands here, never in the real ~/.claude
        os.makedirs(self.config)
        self.metrics = os.path.join(self.config, "delegate-metrics.tsv")
        self.session = self.make_session(3)

    def make_session(self, calls, name="abcdef12-0000"):
        return write(os.path.join(self.dir, "Projects-Demo", name + ".jsonl"), [assistant("m%d" % i, SMALL) for i in range(calls)])

    def add_agent(self, agent="x", calls=2, meta=None, model=HAIKU, session=None):
        """A subagent transcript with 2 calls (U1, U2) by default; meta=None writes no meta.json."""
        base = os.path.join(os.path.splitext(session or self.session)[0], "subagents", "agent-" + agent)
        write(base + ".jsonl", [assistant("s%d" % i, (U1, U2)[i % 2], model) for i in range(calls)])
        if meta is not None:
            write(base + ".meta.json", [meta])

    def run_hook(self, payload):
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run([sys.executable, HOOKS], input=stdin, capture_output=True, encoding="utf-8",
                              env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))

    def event(self, name, **fields):
        return dict({"session_id": "abcdef12-0000", "transcript_path": self.session, "cwd": self.dir, "hook_event_name": name}, **fields)

    def assertSilent(self, p):
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def context(self, p, event, quiet=True):
        """The additionalContext of a run that answered; checks the contract: exit 0, exactly one JSON object on stdout, no noise."""
        self.assertEqual(p.returncode, 0, p.stderr)
        if quiet:
            self.assertEqual(p.stderr, "")
        self.assertEqual(len(p.stdout.splitlines()), 1, p.stdout)
        answer = json.loads(p.stdout)
        self.assertEqual(list(answer), ["hookSpecificOutput"])
        self.assertEqual(answer["hookSpecificOutput"]["hookEventName"], event)
        return answer["hookSpecificOutput"]["additionalContext"]

    def table(self):
        with open(self.metrics, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return lines[0].split("\t"), [dict(zip(lines[0].split("\t"), line.split("\t"))) for line in lines[1:]]


class PostToolUseTest(HookTest):
    META = {"agentType": "sonnet-scout", "description": "scan the repo", "toolUseId": "toolu_X"}

    def post(self, **fields):
        return self.run_hook(self.event("PostToolUse", tool_name="Agent", tool_input={}, tool_response={}, **fields))

    def test_prints_what_the_subagent_cost(self):
        write(self.session, [assistant("m0", U1), assistant("m1", U2), assistant("m2", U2)])  # the lead: 3 calls, the last ends at 460k
        self.add_agent(meta=self.META)
        text = self.context(self.post(tool_use_id="toolu_X"), "PostToolUse")
        P = agent_cost.PRICES
        self.assertEqual(text, 'delegate-cost: sonnet-scout "scan the repo": ~$%.2f, 2 calls, final ctx 460k, cache-read 400k '
                               "(haiku-4-5-20251001). Lead so far: ~$%.2f, 3 calls, ctx 460k." % (
                                   usd(P["haiku"], U1, U2), usd(P["sonnet-5"], U1, U2, U2)))

    def test_an_unknown_tool_use_id_prints_nothing(self):
        self.add_agent(meta=self.META)
        self.assertSilent(self.post(tool_use_id="toolu_other"))

    def test_other_tools_and_missing_ids_print_nothing(self):
        self.add_agent(meta=self.META)
        self.assertSilent(self.run_hook(self.event("PostToolUse", tool_name="Bash", tool_use_id="toolu_X")))
        self.assertSilent(self.post())

    def test_a_background_launch_has_no_transcript_or_has_barely_started(self):
        self.assertSilent(self.post(tool_use_id="toolu_X"))  # nothing under subagents/ yet
        self.add_agent(calls=1, meta=self.META)
        self.assertSilent(self.post(tool_use_id="toolu_X"))  # one call is not a finished agent

    def test_stdout_stays_clean_when_agent_cost_prints_a_warning(self):
        self.add_agent(meta=self.META, model="claude-mystery-1")
        p = self.post(tool_use_id="toolu_X")
        self.assertIn("(mystery-1)", self.context(p, "PostToolUse", quiet=False))
        self.assertIn("warning: no price for claude-mystery-1", p.stderr)

    def test_text_that_is_not_ascii_survives(self):
        self.add_agent(meta=dict(self.META, description="Xodimlar holati — статус"))
        self.assertIn('"Xodimlar holati — статус"', self.context(self.post(tool_use_id="toolu_X"), "PostToolUse"))


class UserPromptSubmitTest(HookTest):
    NOTE = ("<task-notification>\n<task-id>x</task-id>\n<tool-use-id>toolu_X</tool-use-id>\n<output-file>/tmp/x.output</output-file>\n"
            "<status>completed</status>\n<summary>Agent \"scan the repo\" finished</summary>\n<result>done</result>\n</task-notification>")

    def prompt(self, text):
        return self.run_hook(self.event("UserPromptSubmit", prompt=text))

    def test_a_task_notification_prints_the_cost(self):
        self.add_agent(meta={"agentType": "sonnet-scout", "description": "scan the repo", "toolUseId": "toolu_X"})
        text = self.context(self.prompt(self.NOTE), "UserPromptSubmit")
        self.assertTrue(text.startswith('delegate-cost: sonnet-scout "scan the repo": ~$'), text)
        self.assertIn("2 calls", text)

    def test_the_agent_id_is_enough_when_the_meta_has_no_tool_use_id(self):
        self.add_agent(meta={"agentType": "sonnet-coder", "description": "build it"})
        note = "<task-notification>\n<task-id>x</task-id>\n<status>completed</status>\n</task-notification>"
        self.assertIn('delegate-cost: sonnet-coder "build it"', self.context(self.prompt(note), "UserPromptSubmit"))

    def test_a_notification_of_something_else_prints_nothing(self):
        self.add_agent(meta={"agentType": "sonnet-scout", "toolUseId": "toolu_X"})
        self.assertSilent(self.prompt("<task-notification>\n<task-id>bq1w3z</task-id>\n<status>completed</status>\n</task-notification>"))
        self.assertSilent(self.prompt("<task-notification>no ids in here</task-notification>"))

    def test_a_plain_prompt_prints_nothing(self):
        self.add_agent(meta={"agentType": "sonnet-scout", "toolUseId": "toolu_X"})
        self.assertSilent(self.prompt("please look at <task-id>x</task-id> and toolu_X"))  # ids without the notification wrapper
        self.assertSilent(self.prompt("refactor the parser"))
        self.assertSilent(self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "no transcript_path needed"}))
        self.assertSilent(self.run_hook({"hook_event_name": "UserPromptSubmit"}))

    def test_an_agent_id_cannot_leave_the_subagents_directory(self):
        write(os.path.join(os.path.splitext(self.session)[0], "subagents", "agent-q", "z.jsonl"), [assistant("m1", SMALL), assistant("m2", SMALL)])
        self.assertSilent(self.prompt("<task-notification>\n<task-id>q/z</task-id>\n</task-notification>"))


class SessionEndTest(HookTest):
    def end(self, **fields):
        return self.run_hook(self.event("SessionEnd", reason="other", **fields))

    def test_logs_a_session_that_has_a_subagent(self):
        self.add_agent(meta={"agentType": "sonnet-scout", "description": "scan"})
        self.assertSilent(self.end())
        header, rows = self.table()
        self.assertEqual(header, agent_cost.COLUMNS)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["session"], rows[0]["project"], rows[0]["lead_calls"], rows[0]["subagents"]), ("abcdef12", "Demo", "3", "1"))
        self.assertEqual(rows[0]["sub_usd"], "%.2f" % usd(agent_cost.PRICES["haiku"], U1, U2))

    def test_a_short_session_without_subagents_is_not_logged(self):
        self.assertSilent(self.end())
        self.assertFalse(os.path.exists(self.metrics))

    def test_the_threshold_is_30_calls(self):
        self.assertSilent(self.run_hook(self.event("SessionEnd", transcript_path=self.make_session(29, "s29-0000"))))
        self.assertFalse(os.path.exists(self.metrics))
        self.assertSilent(self.run_hook(self.event("SessionEnd", transcript_path=self.make_session(30, "s30-0000"))))
        self.assertEqual([r["session"] for r in self.table()[1]], ["s30-0000"])

    def test_the_usage_logged_earlier_survives_and_a_session_stays_one_row(self):
        row = ["2026-10-07", "Demo", "abcdef12", "old title", "opus-5-5", "1", "1", "0", "0", "0", "0", "0", "0.00", "0.00", "30", ""]
        with open(self.metrics, "w", encoding="utf-8") as f:
            f.write("\t".join(agent_cost.COLUMNS) + "\n" + "\t".join(row) + "\n")
        self.add_agent(meta={"agentType": "sonnet-scout"})
        self.assertSilent(self.end())
        _header, rows = self.table()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["usage_before"], rows[0]["usage_after"], rows[0]["lead_calls"]), ("30", "", "3"))

    def test_a_missing_transcript_is_one_line_on_stderr(self):
        for fields in ({"transcript_path": os.path.join(self.dir, "nope.jsonl")}, {"transcript_path": ""}):
            p = self.end(**fields)
            self.assertEqual((p.returncode, p.stdout), (0, ""))
            self.assertEqual(len(p.stderr.splitlines()), 1, p.stderr)
            self.assertTrue(p.stderr.startswith("delegate hooks:"), p.stderr)
        self.assertFalse(os.path.exists(self.metrics))


class ContractTest(HookTest):
    def test_garbage_on_stdin_exits_0_with_one_line_on_stderr(self):
        for garbage in ("this is not json", "", "{\"hook_event_name\": ", "[1, 2]", "null"):
            p = self.run_hook(garbage)
            self.assertEqual((p.returncode, p.stdout), (0, ""), garbage)
            self.assertEqual(len(p.stderr.splitlines()), 1, (garbage, p.stderr))
            self.assertTrue(p.stderr.startswith("delegate hooks:"), p.stderr)

    def test_bytes_that_are_not_utf8_do_not_matter(self):
        p = subprocess.run([sys.executable, HOOKS], input=b'{"hook_event_name": "Stop", "x": "\xff\xfe"}', capture_output=True,
                           env=dict(os.environ, CLAUDE_CONFIG_DIR=self.config, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"", b""))

    def test_other_events_print_nothing(self):
        for name in ("Stop", "SessionStart", "PreToolUse", "SubagentStop", None):
            self.assertSilent(self.run_hook(self.event(name)))
        self.assertSilent(self.run_hook({}))


if __name__ == "__main__":
    unittest.main()
