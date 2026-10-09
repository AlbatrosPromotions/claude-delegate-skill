"""Tests for agent_cost.py: row dedup, API-equivalent $, session selection, drift warning, metrics file."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
import agent_cost  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "agent_cost.py")
MODEL = "claude-sonnet-5-5"
HAIKU = "claude-haiku-4-5-20251001"
OLD_HEADER = ("date\tproject\tsession\ttitle\tlead_model\tlead_calls\tlead_final_k\tlead_cache_read_M\tsubagents\t"
              "general_purpose\tsub_startup_avg_k\tsub_cache_read_M")  # COLUMNS before lead_usd / sub_usd
OLD_ROW = "2026-10-07\tEDMS\t653ac61c\tDocument design stage 5\topus-5-5\t245\t564\t78.64\t5\t0\t32\t169.27"


def usage(inp, cache_write, cache_read, out):
    return {"input_tokens": inp, "cache_creation_input_tokens": cache_write, "cache_read_input_tokens": cache_read,
            "output_tokens": out}


U1, U2, U3 = usage(1000, 400000, 0, 20000), usage(200, 50000, 400000, 10000), usage(100, 0, 450000, 30000)


def usd(price, *usages):
    """Expected dollars: price = (input, cache_write, cache_read, output) per million tokens."""
    return sum(u["input_tokens"] * price[0] + u["cache_creation_input_tokens"] * price[1]
               + u["cache_read_input_tokens"] * price[2] + u["output_tokens"] * price[3] for u in usages) / 1e6


def assistant(mid, content, u, model=MODEL):
    return {"type": "assistant", "message": {"id": mid, "model": model, "role": "assistant", "content": content, "usage": u}}


def tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(tid, content, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def lead_rows(model=MODEL):
    """Three API calls (msg_A, msg_B, msg_C); msg_A is written twice, as Claude Code does for a message with two blocks."""
    return [
        {"type": "user", "message": {"role": "user", "content": "start"}},
        assistant("msg_A", [{"type": "text", "text": "spawning a scout"}], U1, model),
        assistant("msg_A", [tool_use("toolu_1", "Agent", {"description": "scan", "prompt": "scan the repo"})], U1, model),
        tool_result("toolu_1", "scout is done"),
        assistant("msg_B", [tool_use("toolu_2", "Bash", {"command": "ls"})], U2, model),
        tool_result("toolu_2", [{"type": "text", "text": "a.txt\nb.txt"}]),
        assistant("msg_C", [{"type": "text", "text": "done"}], U3, model),
    ]


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


def touch(path, mtime):
    write(path, [])
    os.utime(path, (mtime, mtime))
    return path


def run_cli(*args):
    return subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))


class AnalyzeTest(unittest.TestCase):
    def analyze(self, rows):
        with tempfile.TemporaryDirectory() as d:
            return agent_cost.analyze(write(os.path.join(d, "lead.jsonl"), rows))

    def test_counts_dedupe_and_cost(self):
        a = self.analyze(lead_rows())
        self.assertEqual(a["calls"], 3)  # msg_A is two rows with one usage: counted once
        self.assertEqual(a["startup"], 401000)  # first call: 1000 + 400000 + 0
        self.assertEqual(a["final"], 480100)  # last call: 100 + 0 + 450000 + 30000 output
        self.assertEqual(a["cache_read"], 850000)
        self.assertEqual(a["cache_write"], 450000)
        self.assertEqual(a["output"], 60000)
        self.assertEqual(a["agent_calls"], 1)
        self.assertAlmostEqual(a["usd"], usd(agent_cost.PRICES["sonnet-5"], U1, U2, U3), places=9)

    def test_agent_calls_counts_spawns_that_started(self):
        rows = lead_rows() + [  # the Agent call in lead_rows counts; so does a Task call; a failed spawn and TaskCreate do not
            assistant("msg_D", [tool_use("toolu_3", "Task", {"description": "old name"}), tool_use("toolu_4", "Agent", {"description": "typo"}),
                                tool_use("toolu_5", "TaskCreate", {"subject": "todo"})], U3),
            tool_result("toolu_3", "report"), tool_result("toolu_4", "Agent type 'sonnet-scuot' not found", is_error=True),
            tool_result("toolu_5", "created")]
        self.assertEqual(self.analyze(rows)["agent_calls"], 2)

    def test_each_call_is_priced_with_its_own_model(self):
        # m3 has no model of its own: it takes the session model, the last one seen (Haiku)
        a = self.analyze([assistant("m1", [], U1, "claude-opus-5-5"), assistant("m2", [], U2, HAIKU), assistant("m3", [], U3, None)])
        P = agent_cost.PRICES
        self.assertAlmostEqual(a["usd"], usd(P["opus-5-5"], U1) + usd(P["haiku"], U2, U3), places=9)

    def test_longest_matching_key_wins(self):
        P = agent_cost.PRICES
        for model, key in (("claude-opus-5-5", "opus-5-5"), ("claude-opus-5-5-20261001", "opus-5-5"), ("claude-opus-4-1", "opus"),
                           ("claude-sonnet-5-5", "sonnet-5"), ("claude-sonnet-5", "sonnet-5"), ("claude-sonnet-4-6", "sonnet"),
                           ("claude-fable-5-1", "fable"), ("claude-haiku-5-5", "haiku-5-5"), (HAIKU, "haiku")):
            self.assertEqual(agent_cost.price_for(model), P[key], model)

    def test_unknown_model_uses_sonnet_5_prices_and_warns_once(self):
        rows = lead_rows("claude-mystery-1") + [  # a <synthetic> row has no tokens: nothing to price, no warning
            assistant("msg_S", [{"type": "text", "text": "Prompt is too long"}], usage(0, 0, 0, 0), "<synthetic>")]
        out = io.StringIO()
        with mock.patch.object(agent_cost, "UNPRICED", set()), contextlib.redirect_stdout(out):
            a = self.analyze(rows)
            self.analyze(rows)
        self.assertEqual(out.getvalue().count("warning: no price for claude-mystery-1, using Sonnet 5.5 prices"), 1)
        self.assertNotIn("<synthetic>", out.getvalue())
        self.assertAlmostEqual(a["usd"], usd(agent_cost.PRICES["sonnet-5"], U1, U2, U3), places=9)


class ResolveTest(unittest.TestCase):
    def test_newest_and_previous(self):
        with tempfile.TemporaryDirectory() as d:
            old, current = touch(os.path.join(d, "old.jsonl"), 1000000), touch(os.path.join(d, "current.jsonl"), 2000000)
            self.assertEqual(agent_cost.resolve(d), current)
            self.assertEqual(agent_cost.resolve(d, previous=True), old)

    def test_previous_is_the_second_newest_not_the_oldest(self):
        with tempfile.TemporaryDirectory() as d:
            touch(os.path.join(d, "a.jsonl"), 1000000)
            second = touch(os.path.join(d, "b.jsonl"), 2000000)
            touch(os.path.join(d, "c.jsonl"), 3000000)
            self.assertEqual(agent_cost.resolve(d, previous=True), second)

    def test_previous_needs_two_sessions(self):
        with tempfile.TemporaryDirectory() as d:
            touch(os.path.join(d, "only.jsonl"), 1000000)
            with self.assertRaises(SystemExit) as cm:
                agent_cost.resolve(d, previous=True)
            self.assertIn("--previous", str(cm.exception))

    def test_previous_is_ignored_for_a_named_session(self):
        with tempfile.TemporaryDirectory() as d:
            named = touch(os.path.join(d, "named.jsonl"), 1000000)
            touch(os.path.join(d, "newer.jsonl"), 2000000)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(agent_cost.resolve(named, previous=True), named)


class CliTest(unittest.TestCase):
    def test_warns_when_the_lead_spawned_agents_but_no_transcripts_exist(self):
        with tempfile.TemporaryDirectory() as d:
            p = run_cli(write(os.path.join(d, "s1.jsonl"), lead_rows()))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("warning: the lead spawned 1 agents but 0 subagent transcripts were found under", p.stdout)
        self.assertIn("script written for Claude Code 2.1.x", p.stdout)

    def test_costs_title_and_no_warning_when_every_agent_has_a_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            session = write(os.path.join(d, "s1.jsonl"), lead_rows())
            write(os.path.join(d, "s1", "custom-title.json"), [{"customTitle": "Stage 9: tests"}])
            write(os.path.join(d, "s1", "subagents", "agent-a1.jsonl"), [assistant("m1", [], U1, HAIKU)])
            write(os.path.join(d, "s1", "subagents", "agent-a1.meta.json"), [{"agentType": "sonnet-scout", "description": "scan"}])
            p = run_cli(session)
        P = agent_cost.PRICES
        lead_usd, sub_usd = usd(P["sonnet-5"], U1, U2, U3), usd(P["haiku"], U1)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("warning:", p.stdout)
        self.assertIn('(claude-sonnet-5-5)  "Stage 9: tests"', p.stdout.splitlines()[0])
        self.assertIn("| output 60k | ~$%.2f" % lead_usd, p.stdout)
        self.assertIn("Cost (API-equivalent): lead $%.2f | subagents $%.2f | total $%.2f   (cache-read: lead 850k vs subagents 0)" % (
            lead_usd, sub_usd, lead_usd + sub_usd), p.stdout)

    def test_metrics_file_lives_in_the_config_dir(self):
        def metrics_path(**extra):
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
            code = "import sys; sys.path.insert(0, %r); import agent_cost; print(agent_cost.METRICS)" % SCRIPTS
            return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                  env=dict(env, PYTHONDONTWRITEBYTECODE="1", **extra)).stdout.strip()

        self.assertEqual(metrics_path(CLAUDE_CONFIG_DIR="/tmp/cfg"), "/tmp/cfg/delegate-metrics.tsv")
        self.assertEqual(metrics_path(), os.path.expanduser("~/.claude/delegate-metrics.tsv"))


class LogMetricsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = os.path.join(tmp.name, "Projects-Demo")
        self.session = write(os.path.join(project, "abcdef12-0000.jsonl"), lead_rows())
        write(os.path.join(project, "abcdef12-0000", "custom-title.json"), [{"customTitle": "Stage 9\ttests"}])
        sub = write(os.path.join(project, "abcdef12-0000", "subagents", "agent-a1.jsonl"), [assistant("m1", [], U1, HAIKU)])
        self.lead = agent_cost.analyze(self.session)
        self.subs_info = [({"agentType": "sonnet-scout"}, agent_cost.analyze(sub))]
        self.metrics = os.path.join(tmp.name, "delegate-metrics.tsv")  # never the real file

    def log(self, lead=None):
        with mock.patch.object(agent_cost, "METRICS", self.metrics), contextlib.redirect_stdout(io.StringIO()):
            agent_cost.log_metrics(self.session, lead or self.lead, self.subs_info)

    def table(self):
        with open(self.metrics, encoding="utf-8") as f:
            lines = f.read().splitlines()
        header = lines[0].split("\t")
        return header, [dict(zip(header, line.split("\t"))) for line in lines[1:]]

    def test_writes_header_and_row(self):
        self.log()
        header, rows = self.table()
        P = agent_cost.PRICES
        self.assertEqual(header, agent_cost.COLUMNS)
        self.assertEqual(header[-2:], ["lead_usd", "sub_usd"])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["session"], rows[0]["title"], rows[0]["subagents"]), ("abcdef12", "Stage 9 tests", "1"))
        self.assertEqual(rows[0]["lead_usd"], "%.2f" % usd(P["sonnet-5"], U1, U2, U3))
        self.assertEqual(rows[0]["sub_usd"], "%.2f" % usd(P["haiku"], U1))

    def test_a_file_with_the_old_header_is_rewritten_with_empty_usd_cells(self):
        with open(self.metrics, "w", encoding="utf-8") as f:
            f.write(OLD_HEADER + "\n" + OLD_ROW + "\n")
        self.log()
        header, rows = self.table()
        self.assertEqual(header, agent_cost.COLUMNS)
        self.assertEqual([r["session"] for r in rows], ["653ac61c", "abcdef12"])
        old = rows[0]
        self.assertEqual("\t".join(old[c] for c in header[:len(OLD_HEADER.split("\t"))]), OLD_ROW)
        self.assertEqual((old["lead_usd"], old["sub_usd"]), ("", ""))
        self.assertNotEqual(rows[1]["lead_usd"], "")

    def test_logging_the_same_session_again_replaces_its_row(self):
        self.log()
        self.log(dict(self.lead, usd=99.0))
        _header, rows = self.table()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lead_usd"], "99.00")


if __name__ == "__main__":
    unittest.main()
