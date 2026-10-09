#!/usr/bin/env python3
"""Show every language's changes to multilingual texts together, so a reviewer reads uz / ru / en, not uz only.

Uses the same layouts as i18n_parity.py (ROOT/<lang>/**/*.md, ROOT/<lang>/**/*.json, ROOT/<lang>.json,
ROOT/<lang>/**/*.php with --php). For each changed unit it prints:
  - articles (.md): each language's changed lines (-U0), one language after another;
  - locale files (.json / .php): each changed key once, with its value in every language (old -> new).
Compared with --base (default HEAD, i.e. uncommitted changes); untracked files count as fully added.

Examples:
  i18n_diff.py api/resources/knowledge
  i18n_diff.py front/i18n/locales --base main
  i18n_diff.py api/lang --php --only messages.php
"""
import argparse
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from i18n_parity import changed_units, discover, load_kv  # noqa: E402


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=check).stdout


def tracked(root, path):
    return subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", "--", path],
                          capture_output=True).returncode == 0


def md_lines(root, path, base):
    if not tracked(root, path):
        with open(path, encoding="utf-8") as f:
            return ["(new file)"] + ["+" + ln.rstrip("\n") for ln in f]
    out, lines = git(root, "diff", "-U0", "--no-color", base, "--", path), []
    for ln in out.splitlines():
        if ln.startswith("@@"):
            lines.append("@" + ln.split("+", 1)[1].split(" ", 1)[0].split(",", 1)[0])
        elif ln[:1] in "+-" and not ln.startswith(("+++", "---")):
            lines.append(ln)
    return lines


def old_kv(root, path, base, use_php):
    top = git(root, "rev-parse", "--show-toplevel").strip()
    rel = os.path.relpath(os.path.realpath(path), top)
    try:
        text = git(root, "show", "%s:%s" % (base, rel))
    except subprocess.CalledProcessError:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=os.path.splitext(path)[1], delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        return load_kv(f.name, use_php) or {}
    finally:
        os.unlink(f.name)


def show(value, width):
    value = "∅" if value is None else value.replace("\n", "⏎")
    return value if len(value) <= width else value[:width - 1] + "…"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--base", default="HEAD", help="git ref to compare with (default HEAD)")
    ap.add_argument("--langs", help="comma-separated language folders/files (default: auto-detect)")
    ap.add_argument("--only", help="comma-separated units (relative paths such as faq.md)")
    ap.add_argument("--php", action="store_true", help="also diff Laravel *.php lang arrays (executes them with php)")
    ap.add_argument("--width", type=int, default=160, help="cut longer values (default 160)")
    args = ap.parse_args()

    root = args.root.rstrip("/") or "/"
    langs, units = discover(root, args.langs.split(",") if args.langs else None)
    if len(langs) < 2:
        sys.exit("found fewer than two languages under %s (use --langs uz,ru,en)" % root)
    selected = sorted(units)
    if args.only:
        wanted = set(args.only.split(","))
        selected = [u for u in selected if u in wanted]
    if args.base == "HEAD":
        ch = changed_units(root, langs)
    else:
        names = git(root, "diff", "--name-only", "--relative", args.base, "--", ".").split()
        names += git(root, "ls-files", "--others", "--exclude-standard", "--", ".").split()
        ch = {u for u in selected for lang, p in units[u].items()
              if os.path.relpath(p, root) in names}
    selected = [u for u in selected if u in ch]
    if not selected:
        print("no changed units under %s (base %s)" % (root, args.base))
        return

    for unit in selected:
        per = units[unit]
        missing = [lang for lang in langs if lang not in per]
        print("=== %s%s" % (unit, "   [missing: %s]" % ",".join(missing) if missing else ""))
        if unit.endswith(".md"):
            for lang in langs:
                if lang in per:
                    print("--- %s" % lang)
                    for ln in md_lines(root, per[lang], args.base) or ["(no change)"]:
                        print(show(ln, args.width * 2))
            continue
        if unit.endswith(".php") and not args.php:
            print("(php skipped, pass --php)")
            continue
        old = {lang: old_kv(root, per[lang], args.base, args.php) for lang in per}
        new = {lang: load_kv(per[lang], args.php) or {} for lang in per}
        keys = sorted({k for lang in per for k in set(old[lang]) | set(new[lang])
                       if old[lang].get(k) != new[lang].get(k)})
        for k in keys:
            print(k)
            for lang in langs:
                if lang not in per:
                    continue
                o, n = old[lang].get(k), new[lang].get(k)
                if o == n:
                    print("  %s  = %s   (unchanged)" % (lang, show(n, args.width)))
                elif o is None:
                    print("  %s  + %s" % (lang, show(n, args.width)))
                elif n is None:
                    print("  %s  - %s" % (lang, show(o, args.width)))
                else:
                    print("  %s  %s -> %s" % (lang, show(o, args.width // 2), show(n, args.width)))


if __name__ == "__main__":
    main()
