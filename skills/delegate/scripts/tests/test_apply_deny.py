import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = os.path.join(SCRIPTS, "apply_deny.py")
RULES = {"permissions": {"deny": ["Bash(git push --force*)", "Bash(* migrate:fresh)"]}}


def run(*args):
    return subprocess.run([sys.executable, APPLY] + list(args), capture_output=True, text=True)


class ApplyDenyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rules = os.path.join(self.tmp.name, "rules.json")
        self.settings = os.path.join(self.tmp.name, "settings.json")
        with open(self.rules, "w", encoding="utf-8") as f:
            json.dump(RULES, f)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def test_adds_rules_and_keeps_other_settings(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"theme": "dark", "permissions": {"defaultMode": "auto", "deny": ["Bash(* migrate:fresh)"]}}, f)
        r = run("--settings", self.settings, "--rules", self.rules)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 1 rule(s), 1 already present", r.stdout)
        s = self.read()
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(s["permissions"]["defaultMode"], "auto")
        self.assertEqual(s["permissions"]["deny"], ["Bash(* migrate:fresh)", "Bash(git push --force*)"])
        self.assertEqual(list(s), ["theme", "permissions"])  # key order kept

    def test_rerun_changes_nothing(self):
        run("--settings", self.settings, "--rules", self.rules)
        before = open(self.settings, encoding="utf-8").read()
        r = run("--settings", self.settings, "--rules", self.rules)
        self.assertIn("added 0 rule(s), 2 already present", r.stdout)
        self.assertEqual(open(self.settings, encoding="utf-8").read(), before)

    def test_missing_settings_file_is_created(self):
        r = run("--settings", self.settings, "--rules", self.rules)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read()["permissions"]["deny"], RULES["permissions"]["deny"])

    def test_dry_run_writes_nothing(self):
        r = run("--settings", self.settings, "--rules", self.rules, "--dry-run")
        self.assertIn("would add 2 rule(s)", r.stdout)
        self.assertFalse(os.path.exists(self.settings))

    def test_broken_settings_file_is_refused(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("{not json")
        r = run("--settings", self.settings, "--rules", self.rules)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(open(self.settings, encoding="utf-8").read(), "{not json")


if __name__ == "__main__":
    unittest.main()
