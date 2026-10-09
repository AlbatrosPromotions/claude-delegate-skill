import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
import agent_cost  # noqa: E402

ROWS = [
    {"date": "2026-10-07", "session": "aaaa1111", "project": "EDMS", "title": "stage 4", "lead_calls": "350", "lead_final_k": "782",
     "lead_usd": "41.96", "sub_usd": "1.24", "usage_before": "", "usage_after": ""},
    {"date": "2026-10-08", "session": "bbbb2222", "project": "EDMS", "title": "stage 5", "lead_calls": "245", "lead_final_k": "564",
     "lead_usd": "21.57", "sub_usd": "39.53", "usage_before": "40", "usage_after": "52"},
    {"date": "2026-10-08", "session": "cccc3333", "project": "other", "title": "x", "lead_calls": "10", "lead_final_k": "50",
     "lead_usd": "1.00", "sub_usd": "0", "usage_before": "", "usage_after": ""},
]


class StageTableTest(unittest.TestCase):
    def test_change_against_previous_row_and_calibration(self):
        lines = agent_cost.stage_table(ROWS, "EDMS").splitlines()
        self.assertEqual(len(lines), 3)  # header + 2 EDMS rows, the other project filtered out
        self.assertIn("43.20", lines[1]); self.assertNotIn("%", lines[1].split("43.20")[1].split("  ")[0])  # first row: no change
        self.assertIn("61.10", lines[2]); self.assertIn("+41%", lines[2])  # 61.10 vs 43.20
        self.assertIn("0.196", lines[2]); self.assertIn("40->52", lines[2])  # (52-40)/61.10 %/$

    def test_without_project_every_row_is_shown(self):
        self.assertEqual(len(agent_cost.stage_table(ROWS).splitlines()), 4)
        self.assertIn("no logged sessions for project nope", agent_cost.stage_table(ROWS, "nope"))

    def test_report_file_and_table_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            metrics = os.path.join(tmp, "delegate-metrics.tsv")
            with open(metrics, "w", encoding="utf-8") as f:
                f.write("\t".join(agent_cost.COLUMNS) + "\n")
                for r in ROWS:
                    f.write("\t".join(r.get(c, "") for c in agent_cost.COLUMNS) + "\n")
            old = agent_cost.METRICS
            agent_cost.METRICS = metrics
            try:
                path = agent_cost.write_report("EDMS")
            finally:
                agent_cost.METRICS = old
            text = open(path, encoding="utf-8").read()
            self.assertTrue(path.endswith("delegate-report.txt"))
            self.assertIn("(project EDMS)", text); self.assertIn("stage 5", text); self.assertNotIn("other", text)
            env = dict(os.environ, CLAUDE_CONFIG_DIR=tmp)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "agent_cost.py"), "--table", "EDMS"], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("+41%", r.stdout)
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "agent_cost.py")], capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0); self.assertIn("path is required", r.stderr)


class SessionStartHookTest(unittest.TestCase):
    def run_hook(self, event):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "hooks.py")], input=json.dumps(event), capture_output=True, text=True)

    def test_startup_gets_the_reminder_resume_does_not(self):
        r = self.run_hook({"hook_event_name": "SessionStart", "source": "startup", "transcript_path": "/nonexistent.jsonl"})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SessionStart"); self.assertIn("get_usage", out["additionalContext"])
        r = self.run_hook({"hook_event_name": "SessionStart", "source": "resume", "transcript_path": "/nonexistent.jsonl"})
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
