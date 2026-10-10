"""Tests for update_check.py, run the way install.sh and hooks.py run it: subprocesses with CLAUDE_CONFIG_DIR pointing at a temp dir,
and a local bare repository standing in for the install repo on GitHub (git ls-remote and fetch work the same against a path)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
import hooks  # noqa: E402  (REMINDER)

UPDATE_CHECK = os.path.join(SCRIPTS, "update_check.py")
HOOKS = os.path.join(SCRIPTS, "hooks.py")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def stamp(**ago):
    return (datetime.now(timezone.utc) - timedelta(**ago)).strftime(STAMP)


class UpdateCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.config = os.path.join(self.dir, "config")
        os.makedirs(self.config)
        self.install = os.path.join(self.config, "delegate-install.json")
        self.update = os.path.join(self.config, "delegate-update.json")
        self.env = dict(os.environ, CLAUDE_CONFIG_DIR=self.config, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
                        GIT_CONFIG_NOSYSTEM="1")
        self.env.pop("DELEGATE_UPDATE_CHECK", None)
        # the "GitHub" repo and the clone a server installed from
        self.remote = os.path.join(self.dir, "remote.git")
        self.clone = os.path.join(self.dir, "claude-delegate-skill")
        self.git("init", "--quiet", "--bare", self.remote)
        self.git("clone", "--quiet", self.remote, self.clone)
        self.first = self.commit("first version")
        self.git("-C", self.clone, "push", "--quiet", "-u", "origin", "HEAD")
        branch = self.git("-C", self.clone, "rev-parse", "--abbrev-ref", "HEAD")
        self.git("-C", self.remote, "symbolic-ref", "HEAD", "refs/heads/" + branch)  # what GitHub advertises as HEAD

    def git(self, *args):
        return subprocess.run(["git"] + list(args), check=True, capture_output=True, encoding="utf-8", env=self.env).stdout.strip()

    def commit(self, message):
        with open(os.path.join(self.clone, "SKILL.md"), "a", encoding="utf-8") as f:
            f.write(message + "\n")
        self.git("-C", self.clone, "add", "-A")
        self.git("-C", self.clone, "commit", "--quiet", "-m", message)
        return self.git("-C", self.clone, "rev-parse", "HEAD")

    def publish(self, message):
        """A newer version on the remote while the clone (and the install made from it) stays at the previous commit."""
        sha = self.commit(message)
        self.git("-C", self.clone, "push", "--quiet", "origin", "HEAD")
        self.git("-C", self.clone, "reset", "--quiet", "--hard", "HEAD~1")
        return sha

    def run_script(self, *args):
        return subprocess.run([sys.executable, UPDATE_CHECK] + list(args), capture_output=True, encoding="utf-8", env=self.env)

    def record(self, flags="--deny --hooks"):
        p = self.run_script("--installed", self.clone, "--flags", flags)
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stderr)
        return p.stdout.strip()

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def hook(self, source="startup", **env):
        event = {"session_id": "abcdef12-0000", "transcript_path": os.path.join(self.dir, "x.jsonl"), "hook_event_name": "SessionStart",
                 "source": source}
        p = subprocess.run([sys.executable, HOOKS], input=json.dumps(event), capture_output=True, encoding="utf-8", env=dict(self.env, **env))
        self.assertEqual((p.returncode, p.stderr), (0, ""), p.stderr)
        if not p.stdout:
            return None
        return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]

    def wait_for_check(self, previous=None, seconds=10):
        """The result the detached worker writes; fails when none arrives (or the file is still the previous one)."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if os.path.exists(self.update):
                data = self.read(self.update)
                if data != previous:
                    return data
            time.sleep(0.05)
        self.fail("no new delegate-update.json within %d s" % seconds)

    def assertNoCheck(self, seconds=1.0):
        before = self.read(self.update) if os.path.exists(self.update) else None
        time.sleep(seconds)
        after = self.read(self.update) if os.path.exists(self.update) else None
        self.assertEqual(before, after, "a background check ran")

    # --installed

    def test_installed_records_the_checkout(self):
        out = self.record("--deny --hooks")
        self.assertIn("recorded %s" % self.first[:7], out)
        data = self.read(self.install)
        self.assertEqual((data["commit"], data["remote"], data["repo_dir"], data["flags"]), (self.first, self.remote, self.clone, "--deny --hooks"))
        self.assertRegex(data["commit_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(data["installed_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_installed_from_a_plain_directory_turns_checks_off(self):
        self.record()
        plain = os.path.join(self.dir, "unpacked")
        os.makedirs(plain)
        p = self.run_script("--installed", plain)
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertIn("update checks are off", p.stdout)
        self.assertFalse(os.path.exists(self.install))

    def test_a_directory_inside_a_checkout_is_not_the_checkout(self):
        sub = os.path.join(self.clone, "skills")
        os.makedirs(sub)
        p = self.run_script("--installed", sub)
        self.assertIn("update checks are off", p.stdout)
        self.assertFalse(os.path.exists(self.install))

    def test_usage_on_bad_arguments(self):
        for args in ([], ["--installed"], ["--installed", self.clone, "--other", "x"]):
            p = self.run_script(*args)
            self.assertNotEqual(p.returncode, 0, args)
            self.assertIn("--installed <clone dir>", p.stderr)

    # --check

    def test_check_sees_the_remote_ahead_and_lists_the_commits(self):
        self.record()
        second = self.publish("second version")
        p = self.run_script("--check")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        data = self.read(self.update)
        self.assertEqual((data["for"], data["latest"], data["error"]), (self.first, second, None))
        self.assertRegex(data["latest_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(len(data["log"]), 1)
        self.assertRegex(data["log"][0], r"^%s \d{4}-\d{2}-\d{2} second version$" % second[:7])

    def test_check_with_nothing_new(self):
        self.record()
        self.run_script("--check")
        data = self.read(self.update)
        self.assertEqual((data["for"], data["latest"], data["log"], data["error"]), (self.first, self.first, [], None))

    def test_check_without_the_clone_still_knows_the_commit(self):
        self.record()
        second = self.publish("second version")
        import shutil
        shutil.rmtree(self.clone)
        self.run_script("--check")
        data = self.read(self.update)
        self.assertEqual((data["latest"], data["log"], data["latest_date"], data["error"]), (second, [], None, None))

    def test_check_against_an_unreachable_remote_records_the_error(self):
        self.record()
        data = self.read(self.install)
        data["remote"] = os.path.join(self.dir, "gone.git")
        with open(self.install, "w", encoding="utf-8") as f:
            json.dump(data, f)
        p = self.run_script("--check")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        data = self.read(self.update)
        self.assertEqual((data["for"], data["latest"]), (self.first, None))
        self.assertTrue(data["error"].startswith("git ls-remote:"), data["error"])

    def test_check_without_a_record_does_nothing(self):
        p = self.run_script("--check")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertFalse(os.path.exists(self.update))

    # the SessionStart hook

    def test_hook_offers_the_update_when_the_last_check_found_one(self):
        self.record("--hooks")
        second = self.publish("second version")
        self.run_script("--check")
        text = self.hook()
        head, commits, reminder = text.split("\n")
        self.assertRegex(head, r"^delegate: a newer version of the delegate skill is published \(installed %s \d{4}-\d{2}-\d{2}, latest %s "
                               r"\d{4}-\d{2}-\d{2}\)\. Tell the user in one line" % (self.first[:7], second[:7]))
        self.assertIn("`git -C %s pull && bash %s/install.sh --hooks`; run it only when the user agrees." % (self.clone, self.clone), head)
        self.assertRegex(commits, r"^New commits: %s \d{4}-\d{2}-\d{2} second version$" % second[:7])
        self.assertEqual(reminder, hooks.REMINDER)
        self.assertNoCheck()  # the result is fresh: no new check

    def test_hook_without_a_record_only_reminds(self):
        self.assertEqual(self.hook(), hooks.REMINDER)
        self.assertNoCheck()
        self.assertFalse(os.path.exists(self.update))

    def test_hook_starts_a_check_and_offers_the_update_next_time(self):
        self.record()
        second = self.publish("second version")
        self.assertEqual(self.hook(), hooks.REMINDER)  # nothing known yet, no waiting for the network
        data = self.wait_for_check()
        self.assertEqual(data["latest"], second)
        self.assertIn("latest %s" % second[:7], self.hook())

    def test_hook_is_quiet_when_the_install_is_current(self):
        self.record()
        self.assertEqual(self.hook(), hooks.REMINDER)
        self.assertEqual(self.wait_for_check()["latest"], self.first)
        self.assertEqual(self.hook(), hooks.REMINDER)
        self.assertNoCheck()

    def test_hook_distrusts_a_result_made_for_another_install(self):
        self.record()
        stale = {"for": "0" * 40, "checked_at": stamp(), "latest": "f" * 40, "latest_date": None, "log": [], "error": None}
        with open(self.update, "w", encoding="utf-8") as f:
            json.dump(stale, f)
        self.assertEqual(self.hook(), hooks.REMINDER)  # not "latest fffffff"
        self.assertEqual(self.wait_for_check(previous=stale)["for"], self.first)

    def test_a_failed_check_is_retried_after_an_hour_and_a_good_one_after_a_day(self):
        self.record()
        failed = {"for": self.first, "checked_at": stamp(hours=2), "latest": None, "latest_date": None, "log": [], "error": "git ls-remote: x"}
        with open(self.update, "w", encoding="utf-8") as f:
            json.dump(failed, f)
        self.assertEqual(self.hook(), hooks.REMINDER)
        self.assertIsNone(self.wait_for_check(previous=failed)["error"])
        good = {"for": self.first, "checked_at": stamp(hours=2), "latest": self.first, "latest_date": None, "log": [], "error": None}
        with open(self.update, "w", encoding="utf-8") as f:
            json.dump(good, f)
        self.hook()
        self.assertNoCheck()
        good["checked_at"] = stamp(days=2)
        with open(self.update, "w", encoding="utf-8") as f:
            json.dump(good, f)
        self.hook()
        self.assertNotEqual(self.wait_for_check(previous=good)["checked_at"], good["checked_at"])

    def test_opt_out_and_other_sources_stay_silent(self):
        self.record()
        self.publish("second version")
        self.run_script("--check")
        self.assertEqual(self.hook(DELEGATE_UPDATE_CHECK="0"), hooks.REMINDER)
        self.assertIsNone(self.hook(source="resume"))
        self.assertIsNone(self.hook(source="compact"))

    def test_notice_on_the_command_line(self):
        self.record()
        self.publish("second version")
        self.run_script("--check")
        p = self.run_script("--notice")
        self.assertEqual((p.returncode, p.stderr), (0, ""))
        self.assertTrue(p.stdout.startswith("delegate: a newer version"), p.stdout)


if __name__ == "__main__":
    unittest.main()
