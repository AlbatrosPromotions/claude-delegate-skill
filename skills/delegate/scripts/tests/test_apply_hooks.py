import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLY = os.path.join(SCRIPTS, "apply_hooks.py")
SHIPPED = os.path.join(os.path.dirname(SCRIPTS), "references", "settings-hooks.json")
RULES = {"hooks": {
    "SessionEnd": [{"hooks": [{"type": "command", "command": "hook-a", "timeout": 20}]}],
    "PostToolUse": [{"matcher": "^Agent$", "hooks": [{"type": "command", "command": "hook-a", "timeout": 20}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "hook-b"}]}],
}}
LINTER = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}


class ApplyHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.rules = os.path.join(self.tmp.name, "rules.json")
        self.settings = os.path.join(self.tmp.name, "settings.json")
        self.write(self.rules, RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def read(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def raw(self):
        with open(self.settings, "rb") as f:
            return f.read()

    def run_apply(self, *args):
        # CLAUDE_CONFIG_DIR points into the temp dir, so a run that forgets --settings still cannot touch the real settings.json
        return subprocess.run([sys.executable, APPLY] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, CLAUDE_CONFIG_DIR=self.tmp.name, PYTHONDONTWRITEBYTECODE="1"))

    def apply(self, *extra):
        return self.run_apply("--settings", self.settings, "--rules", self.rules, *extra)

    def test_adds_all_three_events_to_an_empty_settings_file(self):
        self.write(self.settings, {})
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 3 hook group(s), 0 already present", r.stdout)
        for line in ("+ SessionEnd (no matcher): hook-a", "+ PostToolUse (matcher ^Agent$): hook-a", "+ UserPromptSubmit (no matcher): hook-b"):
            self.assertIn(line, r.stdout)
        self.assertEqual(self.read(), RULES)

    def test_a_missing_settings_file_is_created(self):
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read(), RULES)

    def test_rerun_adds_nothing_and_leaves_the_file_byte_identical(self):
        self.write(self.settings, {"theme": "dark"})
        self.apply()
        before = self.raw()
        r = self.apply()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 0 hook group(s), 3 already present", r.stdout)
        self.assertIn("= PostToolUse (matcher ^Agent$): hook-a  (already present)", r.stdout)
        self.assertEqual(self.raw(), before)

    def test_nothing_to_add_means_the_file_is_not_even_reformatted(self):
        self.write(self.settings, RULES)  # one line, not the indent=2 layout the script writes
        before = self.raw()
        r = self.apply()
        self.assertIn("added 0 hook group(s), 3 already present", r.stdout)
        self.assertEqual(self.raw(), before)

    def test_a_group_with_other_commands_is_not_the_same_group(self):
        self.write(self.settings, {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "somebody-elses-hook"}]}]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s), 0 already present", r.stdout)
        self.assertEqual(len(self.read()["hooks"]["SessionEnd"]), 2)

    def test_keeps_an_unrelated_hook_group_and_every_other_key_in_order(self):
        self.write(self.settings, {"theme": "dark", "hooks": {"PostToolUse": [LINTER], "Stop": [{"hooks": [{"type": "command", "command": "bye"}]}]},
                                   "permissions": {"deny": ["Bash(rm -rf /)"]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s)", r.stdout)
        s = self.read()
        self.assertEqual(s["hooks"]["PostToolUse"], [LINTER, RULES["hooks"]["PostToolUse"][0]])  # appended after the one that was there
        self.assertEqual(s["hooks"]["Stop"], [{"hooks": [{"type": "command", "command": "bye"}]}])
        self.assertEqual(s["permissions"], {"deny": ["Bash(rm -rf /)"]})
        self.assertEqual(s["theme"], "dark")
        self.assertEqual(list(s), ["theme", "hooks", "permissions"])
        self.assertEqual(list(s["hooks"]), ["PostToolUse", "Stop", "SessionEnd", "UserPromptSubmit"])

    def test_the_same_command_under_another_matcher_is_another_group(self):
        self.write(self.settings, {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "hook-a"}]}]}})
        r = self.apply()
        self.assertIn("added 3 hook group(s)", r.stdout)
        self.assertEqual(len(self.read()["hooks"]["PostToolUse"]), 2)

    def test_an_existing_group_counts_when_matcher_and_commands_match_whatever_else_differs(self):
        self.write(self.settings, {"hooks": {
            "PostToolUse": [{"hooks": [{"command": "hook-a", "type": "command", "timeout": 5}], "matcher": "^Agent$"}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "hook-a"}]}, {"hooks": [{"type": "command", "command": "hook-b"}]}]}})
        r = self.apply()
        self.assertIn("added 1 hook group(s), 2 already present", r.stdout)
        s = self.read()
        self.assertEqual(len(s["hooks"]["PostToolUse"]), 1)
        self.assertEqual(len(s["hooks"]["SessionEnd"]), 2)
        self.assertEqual(s["hooks"]["UserPromptSubmit"], RULES["hooks"]["UserPromptSubmit"])

    def test_an_empty_or_star_matcher_is_the_same_as_none(self):
        self.write(self.settings, {"hooks": {"SessionEnd": [{"matcher": "", "hooks": [{"type": "command", "command": "hook-a"}]}],
                                             "UserPromptSubmit": [{"matcher": "*", "hooks": [{"type": "command", "command": "hook-b"}]}]}})
        r = self.apply()
        self.assertIn("added 1 hook group(s), 2 already present", r.stdout)
        s = self.read()
        self.assertEqual((len(s["hooks"]["SessionEnd"]), len(s["hooks"]["UserPromptSubmit"]), len(s["hooks"]["PostToolUse"])), (1, 1, 1))

    def test_dry_run_writes_nothing(self):
        r = self.apply("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would add 3 hook group(s)", r.stdout)
        self.assertIn("(dry run, nothing written)", r.stdout)
        self.assertFalse(os.path.exists(self.settings))
        self.write(self.settings, {"theme": "dark"})
        before = self.raw()
        self.apply("--dry-run")
        self.assertEqual(self.raw(), before)

    def test_broken_settings_file_is_refused(self):
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write("{not json")
        r = self.apply()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.raw(), b"{not json")

    def test_settings_of_the_wrong_shape_are_refused_untouched(self):
        for bad in ({"hooks": []}, {"hooks": {"PostToolUse": {}}}, {"hooks": None}, ["not", "an", "object"]):
            self.write(self.settings, bad)
            before = self.raw()
            r = self.apply()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("nothing was changed", r.stderr)
            self.assertEqual(self.raw(), before)

    def test_rules_of_the_wrong_shape_are_refused(self):
        for bad in ({"permissions": {}}, {"hooks": []}, {"hooks": {"Stop": [{"matcher": "x"}]}}, {"hooks": {"Stop": [{"hooks": ["cmd"]}]}}, []):
            self.write(self.rules, bad)
            r = self.apply()
            self.assertEqual(r.returncode, 1, bad)
            self.assertIn("expected", r.stderr)
            self.assertFalse(os.path.exists(self.settings))

    def test_the_shipped_rules_file_merges_into_the_default_settings_file(self):
        r = self.run_apply()  # no --settings: $CLAUDE_CONFIG_DIR/settings.json; no --rules: references/settings-hooks.json
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("added 4 hook group(s), 0 already present", r.stdout)
        with open(SHIPPED, encoding="utf-8") as f:
            shipped = json.load(f)
        self.assertEqual(self.read(), shipped)
        self.assertEqual(sorted(shipped["hooks"]), ["PostToolUse", "SessionEnd", "SessionStart", "UserPromptSubmit"])
        for groups in shipped["hooks"].values():
            self.assertIn("skills/delegate/scripts/hooks.py", groups[0]["hooks"][0]["command"])
        self.assertEqual(shipped["hooks"]["PostToolUse"][0]["matcher"], "^Agent$")


if __name__ == "__main__":
    unittest.main()
