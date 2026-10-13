"""Tests for the two things the 2026-10-12 retro added: usage_before/usage_after filled from the lead's get_usage readings,
and catch_up(): sessions the SessionEnd hook missed are logged at --table and at SessionStart."""
import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
import agent_cost  # noqa: E402

MODEL = "claude-opus-5-5"
U = {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 100000, "output_tokens": 500}


def write(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows))
    return path


def usage_result(pct, tid):
    plan = {"plan": {"windows": [{"label": "5-hour limit", "percentUsed": 3}, {"label": "Weekly · all models", "percentUsed": pct},
                                 {"label": "Weekly · Fable", "percentUsed": 7}]}}
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": json.dumps(plan)}]}]}}


def call(mid, content=()):
    return {"type": "assistant", "timestamp": "2026-10-12T10:00:00.000Z",
            "message": {"id": mid, "model": MODEL, "role": "assistant", "content": list(content), "usage": U}}


def get_usage(tid):
    return {"type": "tool_use", "id": tid, "name": "mcp__ccd_session_mgmt__get_usage", "input": {}}


IDS = itertools.count()  # message ids are unique across transcripts, as the API makes them (copies are recognised by id)


def session_rows(calls, readings=()):
    """`calls` lead calls; the first and the last carry a get_usage call with the given readings (one reading: the first only)."""
    rows = [{"type": "user", "message": {"role": "user", "content": "go"}}]
    for i in range(calls):
        rows.append(call("m%d" % next(IDS)))
    for n, pct in enumerate(readings):
        tid = "toolu_usage%d" % n
        rows.insert(1 + n * (calls - 1) if len(readings) > 1 else 1, call("u%d" % next(IDS), [get_usage(tid)]))
        rows.insert(2 + n * (calls - 1) if len(readings) > 1 else 2, usage_result(pct, tid))
    return rows


class UsageReadingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.metrics = os.path.join(self.dir, "delegate-metrics.tsv")

    def session(self, readings, name="abcdef12-0000"):
        return write(os.path.join(self.dir, "projects", "Projects-Demo", name + ".jsonl"), session_rows(5, readings))

    def log(self, session, **usage):
        with mock.patch.object(agent_cost, "METRICS", self.metrics), contextlib.redirect_stdout(io.StringIO()):
            agent_cost.log_metrics(session, agent_cost.analyze(session), [], **usage)
        with open(self.metrics, encoding="utf-8") as f:
            lines = f.read().splitlines()
        row = dict(zip(lines[0].split("\t"), lines[-1].split("\t")))
        return row["usage_before"], row["usage_after"]

    def test_analyze_collects_the_weekly_all_models_readings_in_order(self):
        self.assertEqual(agent_cost.analyze(self.session([61, 66]))["usage_readings"], [61.0, 66.0])
        self.assertEqual(agent_cost.analyze(self.session([]))["usage_readings"], [])

    def test_weekly_pct_reads_text_blocks_and_strings_and_ignores_other_results(self):
        plan = json.dumps({"plan": {"windows": [{"label": "Weekly · all models", "percentUsed": 84.5}]}})
        self.assertEqual(agent_cost.weekly_pct([{"type": "text", "text": plan}]), 84.5)
        self.assertEqual(agent_cost.weekly_pct(plan), 84.5)
        self.assertIsNone(agent_cost.weekly_pct("not json"))
        self.assertIsNone(agent_cost.weekly_pct(json.dumps({"plan": {"status": "unavailable"}})))
        self.assertIsNone(agent_cost.weekly_pct(json.dumps({"plan": {"windows": [{"label": "Weekly · Fable", "percentUsed": 7}]}})))

    def test_first_and_last_reading_fill_the_usage_cells(self):
        self.assertEqual(self.log(self.session([61, 63, 66])), ("61", "66"))

    def test_one_reading_fills_before_only(self):
        self.assertEqual(self.log(self.session([61])), ("61", ""))

    def test_a_given_value_wins_over_the_readings_which_win_over_the_old_row(self):
        s = self.session([61, 66])
        self.assertEqual(self.log(s, usage_after=70), ("61", "70"))
        self.assertEqual(self.log(s), ("61", "66"))  # a plain re-log: the readings replace the old cells
        t = self.session([], "bbbbbbbb-0000")
        self.assertEqual(self.log(t, usage_before=40), ("40", ""))
        self.assertEqual(self.log(t), ("40", ""))  # no readings: the old row's cells survive

    def test_the_cli_prints_the_readings_and_the_calibration(self):
        s = self.session([61, 66])
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "agent_cost.py"), s, "--log"], capture_output=True, text=True,
                           env=dict(os.environ, CLAUDE_CONFIG_DIR=self.dir))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("plan usage read in the session (weekly, all models): 61% -> 66%", p.stdout)
        self.assertIn("limit calibration: +5% of the weekly allowance", p.stdout)


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.metrics = os.path.join(self.dir, "delegate-metrics.tsv")
        self.project = os.path.join(self.dir, "projects", "Projects-Demo")
        self.big = write(os.path.join(self.project, "aaaaaaaa-0000.jsonl"), session_rows(35, [61, 66]))
        self.small = write(os.path.join(self.project, "bbbbbbbb-0000.jsonl"), session_rows(3))
        self.other = write(os.path.join(self.dir, "projects", "Projects-Other", "cccccccc-0000.jsonl"), session_rows(31))

    def catch_up(self, *args, **kw):
        with mock.patch.object(agent_cost, "METRICS", self.metrics), contextlib.redirect_stdout(io.StringIO()):
            return agent_cost.catch_up(*args, **kw)

    def rows(self):
        if not os.path.exists(self.metrics):
            return {}
        with open(self.metrics, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return {r["session"]: r for r in (dict(zip(lines[0].split("\t"), line.split("\t"))) for line in lines[1:])}

    def seen(self):
        with open(os.path.join(self.dir, "delegate-catchup.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_substantial_sessions_of_every_project_are_logged_once_and_a_grown_one_again(self):
        self.assertEqual(sorted(self.catch_up()), ["aaaaaaaa", "cccccccc"])
        rows = self.rows()
        self.assertEqual(set(rows), {"aaaaaaaa", "cccccccc"})
        self.assertEqual((rows["aaaaaaaa"]["lead_calls"], rows["aaaaaaaa"]["usage_before"], rows["aaaaaaaa"]["usage_after"]), ("37", "61", "66"))
        self.assertEqual(set(self.seen()), {self.big, self.small, self.other})  # the small one was examined too, not logged
        self.assertEqual(self.catch_up(), [])  # nothing changed: nothing is parsed again
        with open(self.big, "a", encoding="utf-8") as f:  # the session was resumed and grew
            f.write("".join(json.dumps(call("x%d" % i)) + "\n" for i in range(5)))
        os.utime(self.big, (time.time() + 1, time.time() + 1))
        self.assertEqual(self.catch_up(), ["aaaaaaaa"])
        self.assertEqual(self.rows()["aaaaaaaa"]["lead_calls"], "42")

    def test_one_project_the_running_session_excluded_and_old_files_skipped(self):
        self.assertEqual(self.catch_up(self.project, exclude=self.big), [])
        os.utime(self.other, (time.time() - 20 * 86400,) * 2)
        self.assertEqual(self.catch_up(), ["aaaaaaaa"])  # the other project's session is older than 14 days
        self.assertNotIn(self.other, self.seen())

    def test_a_session_logged_by_the_hook_is_not_parsed_again(self):
        with mock.patch.object(agent_cost, "METRICS", self.metrics):
            agent_cost.log_if_substantial(self.big)
        self.assertEqual(self.seen(), {self.big: os.path.getmtime(self.big)})
        self.assertEqual(self.catch_up(self.project), [])

    def test_the_budget_stops_the_scan_and_the_rest_waits_for_next_time(self):
        self.assertEqual(self.catch_up(budget=-1), [])  # no time at all: nothing is examined, nothing remembered
        self.assertFalse(os.path.exists(os.path.join(self.dir, "delegate-catchup.json")))
        self.assertEqual(sorted(self.catch_up(budget=60)), ["aaaaaaaa", "cccccccc"])  # next time, with time, the rest is done

    def test_a_relogged_row_keeps_its_place_and_the_table_is_in_date_order(self):
        self.catch_up()
        rows = self.rows()
        with open(self.big, "a", encoding="utf-8") as f:
            f.write(json.dumps(call("x")) + "\n")
        os.utime(self.big, (time.time() + 1, time.time() + 1))
        self.catch_up()
        self.assertEqual(list(self.rows()), list(rows))  # same order after the re-log
        table = agent_cost.stage_table([{"date": "2026-10-12", "session": "b", "title": "", "lead_usd": "1", "sub_usd": "0"},
                                        {"date": "2026-10-11", "session": "a", "title": "", "lead_usd": "1", "sub_usd": "0"}])
        self.assertLess(table.index(" a "), table.index(" b "))

    def test_table_cli_catches_up_first(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=self.dir)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "agent_cost.py"), "--table"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("caught up 2 session(s) the SessionEnd hook had missed:", p.stdout)
        self.assertIn("aaaaaaaa", p.stdout); self.assertIn("61->66", p.stdout)
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "agent_cost.py"), "--table"], capture_output=True, text=True, env=env)
        self.assertNotIn("caught up", p.stdout)

    def test_session_start_hook_catches_up_its_project(self):
        new = os.path.join(self.project, "dddddddd-0000.jsonl")  # the session that is starting; its transcript may not exist yet
        event = {"hook_event_name": "SessionStart", "source": "startup", "transcript_path": new, "cwd": self.dir}
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "hooks.py")], input=json.dumps(event), capture_output=True, text=True,
                           env=dict(os.environ, CLAUDE_CONFIG_DIR=self.dir, DELEGATE_UPDATE_CHECK="0"))
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertIn("get_usage", json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(set(self.rows()), {"aaaaaaaa"})  # this project only
        self.assertTrue(os.path.exists(os.path.join(self.dir, "delegate-report.txt")))


if __name__ == "__main__":
    unittest.main()
