"""Tests for i18n_parity.py and i18n_diff.py. Both scripts run unchanged, as subprocesses, on small fixtures."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)
PARITY = os.path.join(SCRIPTS, "i18n_parity.py")
DIFF = os.path.join(SCRIPTS, "i18n_diff.py")

# Same structure in every language: one heading, one link, two list items; no apostrophes, so no style findings.
FAQ = {
    "uz": "# Savollar\n\nBatafsil [yordam sahifasida](https://example.com/help).\n\n- birinchi\n- ikkinchi\n",
    "ru": "# Вопросы\n\nПодробнее на [странице помощи](https://example.com/help).\n\n- первый\n- второй\n",
    "en": "# Questions\n\nSee the [help page](https://example.com/help).\n\n- first\n- second\n",
}


def put(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def run(script, *args):
    return subprocess.run([sys.executable, script] + list(args), capture_output=True, encoding="utf-8",
                          env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"))


class ParityMarkdownTest(unittest.TestCase):
    def check(self, files):
        """files: {relative path: text} under a fresh root; returns the CompletedProcess of i18n_parity.py."""
        with tempfile.TemporaryDirectory() as root:
            for rel, text in files.items():
                put(root, rel, text)
            return run(PARITY, root)

    def test_clean_root_exits_zero(self):
        p = self.check({"%s/faq.md" % lang: text for lang, text in FAQ.items()})
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("0 error(s), 0 warning(s)", p.stdout)

    def test_different_link_target_is_reported(self):
        files = {"%s/faq.md" % lang: text for lang, text in FAQ.items()}
        files["ru/faq.md"] = FAQ["ru"].replace("https://example.com/help", "https://example.com/other")
        p = self.check(files)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[links]", p.stdout)

    def test_unit_missing_in_one_language_is_reported(self):
        files = {"%s/faq.md" % lang: text for lang, text in FAQ.items()}
        files["uz/extra.md"] = files["ru/extra.md"] = "# Qoshimcha\n"  # no en/extra.md
        p = self.check(files)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[missing-file]", p.stdout)
        self.assertIn("missing in: en", p.stdout)

    def test_word_mixing_cyrillic_and_latin_letters_is_reported(self):
        files = {"%s/faq.md" % lang: text for lang, text in FAQ.items()}
        files["uz/faq.md"] = FAQ["uz"].replace("Savollar", "Сavollar")  # Cyrillic Es followed by Latin letters
        p = self.check(files)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[mixed-script]", p.stdout)


class ParityLocaleFilesTest(unittest.TestCase):
    def check(self, uz, ru, en):
        with tempfile.TemporaryDirectory() as root:
            for lang, data in (("uz", uz), ("ru", ru), ("en", en)):
                put(root, lang + ".json", json.dumps(data, ensure_ascii=False))
            return run(PARITY, root)

    def test_key_missing_in_one_language_is_reported(self):
        p = self.check({"hello": "Salom", "bye": "Xayr"}, {"hello": "Привет"}, {"hello": "Hello", "bye": "Bye"})
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[missing-keys]", p.stdout)
        self.assertIn("ru: 1 keys missing: bye", p.stdout)

    def test_placeholder_mismatch_is_reported(self):
        p = self.check({"greeting": "Salom, {name}!"}, {"greeting": "Привет, {nam}!"}, {"greeting": "Hello, {name}!"})
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("[placeholders]", p.stdout)

    def test_clean_locale_files_exit_zero(self):
        p = self.check({"greeting": "Salom, {name}!"}, {"greeting": "Привет, {name}!"}, {"greeting": "Hello, {name}!"})
        self.assertEqual(p.returncode, 0, p.stdout)


class DiffTest(unittest.TestCase):
    def test_shows_every_language_and_marks_the_unchanged_ones(self):
        article = {
            "uz": "# Savollar\n\nBirinchi qator.\nIkkinchi qator.\n",
            "ru": "# Вопросы\n\nПервая строка.\nВторая строка.\n",
            "en": "# Questions\n\nFirst line.\nSecond line.\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)  # git compares real paths (on macOS /var is a link to /private/var)

            def git(*args):
                subprocess.run(["git", "-C", root, "-c", "commit.gpgsign=false"] + list(args), check=True, capture_output=True)

            git("init", "-q")
            git("config", "user.email", "test@example.com")
            git("config", "user.name", "Test")
            for lang, text in article.items():
                put(root, "%s/a.md" % lang, text)
            git("add", "-A")
            git("commit", "-q", "-m", "init")
            put(root, "uz/a.md", article["uz"].replace("Ikkinchi qator.", "Yangi ikkinchi qator."))  # only uz changes
            p = run(DIFF, root)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("=== a.md", p.stdout)
        self.assertIn("--- ru\n(no change)", p.stdout)
        self.assertIn("--- en\n(no change)", p.stdout)
        uz_lines = p.stdout.split("--- uz\n", 1)[1].splitlines()
        self.assertIn("+Yangi ikkinchi qator.", uz_lines)
        self.assertIn("-Ikkinchi qator.", uz_lines)


if __name__ == "__main__":
    unittest.main()
