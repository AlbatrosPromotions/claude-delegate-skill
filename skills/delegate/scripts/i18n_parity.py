#!/usr/bin/env python3
"""Deterministic parity checks for parallel multilingual texts (e.g. uz / ru / en).

Catches what is easy to miss when translations are compared by eye: missing files
or keys, link and image targets that differ, a different heading structure,
placeholders that differ, Latin and Cyrillic letters mixed inside one word, and
mixed Uzbek apostrophe styles. It does not judge meaning; that stays with the
model or a human reviewer.

Layouts under ROOT (auto-detected; language folders/files are ISO codes such as uz, ru, en, uz-Cyrl):
  ROOT/<lang>/**/*.md     articles with the same relative path in every language
  ROOT/<lang>/**/*.json   nested locale files with the same relative path
  ROOT/<lang>.json        one locale file per language
  ROOT/<lang>/**/*.php    Laravel lang arrays, only with --php (the files are executed by `php`)

Examples:
  i18n_parity.py api/resources/knowledge
  i18n_parity.py api/resources/knowledge --changed --uz-okina ʻ --uz-tutuq ʼ
  i18n_parity.py lang --php --save /tmp/base.json     # before editing
  i18n_parity.py lang --php --against /tmp/base.json  # after: only findings that are new

Exit status: 1 when an ERROR is reported (with --against: a new ERROR), otherwise 0.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

KNOWN_LANGS = {
    "ar", "az", "be", "bg", "cs", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "he",
    "hi", "hu", "hy", "id", "it", "ja", "ka", "kk", "ko", "ky", "lt", "lv", "mn", "nl", "no",
    "oz", "pl", "pt", "ro", "ru", "sk", "sr", "sv", "tg", "th", "tk", "tr", "tt", "uk", "ur",
    "uz", "vi", "zh",
}
CYRILLIC_LANGS = {"ru", "uk", "be", "bg", "kk", "ky", "mn", "tg", "tt", "sr", "oz"}
LANG_NAME = re.compile(r"^([a-z]{2})(?:[-_][A-Za-z]{2,4})?$")

CYR = re.compile(r"[\u0400-\u04FF]")
LAT = re.compile(r"[A-Za-z]")
WORD = re.compile(r"\w+")
APOS = "ʻʼ'‘’`´"
OKINA = re.compile(r"(?<=[oOgG])([%s])(?=[A-Za-z])" % re.escape(APOS))
TUTUQ = re.compile(r"(?<=[A-FH-NP-Za-fh-np-z])([%s])(?=[A-Za-z])" % re.escape(APOS))

FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"`[^`\n]+`")
MD_LINK = re.compile(r"!?\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
URL = re.compile(r"https?://[^\s)\]»\"'<>]+")
HEADING = re.compile(r"^(#{1,6})[ \t]+\S")
LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")
TABLE_ROW = re.compile(r"^[ \t]*\|")
NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
QUOTED = re.compile(r"«[^«»\n]*»")
PLACEHOLDER = re.compile(
    r"\{\{\s*[\w.\-]+\s*\}\}"                              # {{ name }}
    r"|\{[\w.\-]*\}"                                       # {name} {0} {}
    r"|(?<![\w:/\\]):[A-Za-z_]\w*"                         # :name (Laravel)
    r"|%(?:\d+\$)?[-+ 0#]*\d*(?:\.\d+)?[sdifuxXeEgGc]"     # printf
    r"|@(?:\.\w+)?:[\w.\-]+"                               # @:key, @.lower:key (vue-i18n)
    r"|</?[A-Za-z][\w-]*"                                  # HTML tag names
)


def base_lang(lang):
    m = LANG_NAME.match(lang)
    return m.group(1) if m else lang


def expects_cyrillic(lang):
    return "cyrl" in lang.lower() or base_lang(lang) in CYRILLIC_LANGS


def is_uz_latin(lang):
    return base_lang(lang) == "uz" and "cyrl" not in lang.lower()


class Findings:
    def __init__(self):
        self.items = []

    def add(self, severity, unit, check, detail, where=""):
        # `detail` must not contain line numbers: it is the identity used by --save/--against.
        self.items.append({
            "severity": severity, "unit": unit, "check": check, "detail": detail, "where": where,
            "id": "|".join((severity, unit, check, detail)),
        })


# ---------------------------------------------------------------- discovery

def is_lang_name(name):
    m = LANG_NAME.match(name)
    return bool(m) and m.group(1) in KNOWN_LANGS


def discover(root, langs_arg):
    entries = os.listdir(root)
    dir_langs = sorted(e for e in entries if os.path.isdir(os.path.join(root, e)) and is_lang_name(e))
    file_langs = sorted(e[:-5] for e in entries if e.endswith(".json") and is_lang_name(e[:-5]))
    if langs_arg:
        langs = langs_arg
    else:
        langs = dir_langs if len(dir_langs) >= 2 else file_langs
    units = {}
    for lang in langs:
        d = os.path.join(root, lang)
        if os.path.isdir(d):
            for base, dirs, files in os.walk(d):
                dirs[:] = sorted(x for x in dirs if not x.startswith("."))
                for f in sorted(files):
                    if f.endswith((".md", ".json", ".php")):
                        path = os.path.join(base, f)
                        units.setdefault(os.path.relpath(path, d), {})[lang] = path
        flat = os.path.join(root, lang + ".json")
        if os.path.isfile(flat):
            units.setdefault("<lang>.json", {})[lang] = flat
    return langs, units


def unit_of(rel_to_root, langs):
    first, _, rest = rel_to_root.partition(os.sep)
    if first in langs and rest:
        return rest
    if rel_to_root.endswith(".json") and rel_to_root[:-5] in langs:
        return "<lang>.json"
    return None


def changed_units(root, langs):
    try:
        top = subprocess.run(["git", "-C", root, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        out = subprocess.run(["git", "-C", root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit("--changed needs ROOT to be inside a git work tree")
    root_abs = os.path.realpath(root)
    parts, units, i = out.split("\0"), set(), 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        if entry[0] in "RC":
            i += 1  # the next field is the rename/copy source
        rel = os.path.relpath(os.path.realpath(os.path.join(top, entry[3:])), root_abs)
        unit = unit_of(rel, langs)
        if unit:
            units.add(unit)
    return units


# ---------------------------------------------------------------- shared text checks

def norm_target(target, langs):
    for lang in langs:
        target = re.sub(r"(?<=[-_./])%s(?=[-_./]|$)" % re.escape(lang), "{lang}", target)
    return target


def script_and_apostrophe_checks(fs, unit, lang, lines, args):
    """lines: [(lineno, prose_text)] with code already removed."""
    mixed, cyr_lines = {}, []
    styles = {"oʻ/gʻ": (OKINA, args.uz_okina, Counter(), {}), "ʼ (tutuq)": (TUTUQ, args.uz_tutuq, Counter(), {})}
    for no, text in lines:
        for w in WORD.findall(text):
            if CYR.search(w) and LAT.search(w):
                mixed.setdefault(w, []).append(no)
            elif CYR.search(w) and not expects_cyrillic(lang) and (not cyr_lines or cyr_lines[-1] != no):
                cyr_lines.append(no)
        if is_uz_latin(lang):
            outside_quotes = QUOTED.sub(" ", text)
            for regex, _expected, counts, first in styles.values():
                for m in regex.finditer(outside_quotes):
                    counts[m.group(1)] += 1
                    first.setdefault(m.group(1), []).append(no)
    for w, nos in sorted(mixed.items()):
        fs.add("ERROR", unit, "mixed-script", "%s: Latin and Cyrillic letters in one word «%s»" % (lang, w),
               "%s:%s" % (lang, ",".join(map(str, nos[:5]))))
    if cyr_lines:
        fs.add("WARN", unit, "script", "%s: Cyrillic words in a Latin-script text" % lang,
               "%s:%s" % (lang, ",".join(map(str, cyr_lines[:5]))))
    for name, (_regex, expected, counts, first) in styles.items():
        if not counts:
            continue
        if expected:
            for ch, n in sorted(counts.items()):
                if ch != expected:
                    fs.add("ERROR", unit, "apostrophe", "%s: %s written with %r ×%d (expected %r)" % (lang, name, ch, n, expected),
                           "%s:%s" % (lang, ",".join(map(str, first[ch][:5]))))
        elif len(counts) > 1:
            minority = min(counts, key=counts.get)
            fs.add("WARN", unit, "apostrophe", "%s: mixed %s styles %s" % (lang, name, " ".join("%r×%d" % kv for kv in sorted(counts.items()))),
                   "%s:%s (%r)" % (lang, ",".join(map(str, first[minority][:5])), minority))


def counter_diff(counters):
    """{lang: Counter} -> list of (key, {lang: count}) where counts differ."""
    keys = set()
    for c in counters.values():
        keys |= set(c)
    return [(k, {lang: c.get(k, 0) for lang, c in counters.items()})
            for k in sorted(keys, key=str) if len({c.get(k, 0) for c in counters.values()}) > 1]


def fmt_diff(diffs, limit=8):
    shown = ["%s (%s)" % (k, " ".join("%s×%d" % kv for kv in per.items())) for k, per in diffs[:limit]]
    more = " …+%d" % (len(diffs) - limit) if len(diffs) > limit else ""
    return "; ".join(shown) + more


# ---------------------------------------------------------------- markdown

def md_profile(text, langs):
    p = {"fm": [], "headings": [], "lists": 0, "tables": 0, "fences": 0, "links": Counter(), "link_lines": {},
         "code": Counter(), "numbers": Counter(), "quotes": [0, 0], "prose": []}
    lines = text.split("\n")
    in_fm = bool(lines) and lines[0].strip() == "---"
    fence = None
    for no, line in enumerate(lines, 1):
        if in_fm:
            if no > 1 and line.strip() == "---":
                in_fm = False
            else:
                m = re.match(r"^([\w-]+)\s*:", line)
                if m and no > 1:
                    p["fm"].append(m.group(1))
            continue
        m = FENCE_OPEN.match(line)
        if fence:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
            continue
        if m:
            fence = m.group(1)
            p["fences"] += 1
            continue
        h = HEADING.match(line)
        if h:
            p["headings"].append(len(h.group(1)))
        if LIST_ITEM.match(line):
            p["lists"] += 1
        if TABLE_ROW.match(line):
            p["tables"] += 1
        for c in INLINE_CODE.findall(line):
            p["code"][c] += 1
        prose = INLINE_CODE.sub(" ", line)
        for t in MD_LINK.findall(prose):
            t = norm_target(t, langs)
            p["links"][t] += 1
            p["link_lines"].setdefault(t, no)
        prose = MD_LINK.sub(lambda mm: " " + re.sub(r"\]\(.*$", "]", mm.group(0)) + " ", prose)
        prose = URL.sub(" ", prose)
        for n in NUMBER.findall(prose):
            p["numbers"][n] += 1
        p["quotes"][0] += prose.count("«")
        p["quotes"][1] += prose.count("»")
        p["prose"].append((no, prose))
    return p


def check_markdown(fs, unit, per_lang, langs, args):
    profiles = {}
    for lang in langs:
        path = per_lang.get(lang)
        if not path:
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            fs.add("ERROR", unit, "empty", "%s: file is empty" % lang)
            continue
        profiles[lang] = md_profile(text, langs)
        p = profiles[lang]
        if p["quotes"][0] != p["quotes"][1]:
            fs.add("WARN", unit, "quotes", "%s: unbalanced «» (« ×%d, » ×%d)" % (lang, p["quotes"][0], p["quotes"][1]))
        script_and_apostrophe_checks(fs, unit, lang, p["prose"], args)
    if len(profiles) < 2:
        return

    def seq(k):
        return {lang: p[k] for lang, p in profiles.items()}

    fm = {lang: sorted(set(v)) for lang, v in seq("fm").items()}
    if len({tuple(v) for v in fm.values()}) > 1:
        fs.add("ERROR", unit, "front-matter", "keys differ: " + "; ".join("%s=%s" % (lang, ",".join(v) or "-") for lang, v in fm.items()))
    hs = {lang: "".join(str(x) for x in v) for lang, v in seq("headings").items()}
    if len(set(hs.values())) > 1:
        fs.add("ERROR", unit, "headings", "heading levels differ: " + "; ".join("%s=%s" % kv for kv in hs.items()))
    diffs = counter_diff(seq("links"))
    if diffs:
        where = "; ".join("%s:%d" % (lang, profiles[lang]["link_lines"][k]) for k, per in diffs[:3]
                          for lang in profiles if k in profiles[lang]["link_lines"])
        fs.add("ERROR", unit, "links", "link/image targets differ: " + fmt_diff(diffs), where)
    for key, sev, label in (("lists", "WARN", "list items"), ("tables", "WARN", "table rows"), ("fences", "WARN", "code blocks")):
        counts = seq(key)
        if len(set(counts.values())) > 1:
            fs.add(sev, unit, key, "%s differ: %s" % (label, " ".join("%s=%d" % kv for kv in counts.items())))
    diffs = counter_diff(seq("code"))
    if diffs:
        fs.add("WARN", unit, "code", "inline code differs: " + fmt_diff(diffs))
    diffs = counter_diff(seq("numbers"))
    if diffs:
        fs.add("WARN", unit, "numbers", "numbers differ: " + fmt_diff(diffs))


# ---------------------------------------------------------------- key/value locale files

def load_kv(path, use_php):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        php = shutil.which("php")
        if not (use_php and php):
            return None
        out = subprocess.run([php, "-r", "echo json_encode(include $argv[1], JSON_UNESCAPED_UNICODE);", path],
                             capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "null")
    flat = {}

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                walk("%s.%s" % (prefix, k) if prefix else str(k), x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk("%s[%d]" % (prefix, i), x)
        else:
            flat[prefix] = "" if v is None else str(v)

    walk("", data)
    return flat


def check_kv(fs, unit, per_lang, langs, args):
    maps = {}
    for lang in langs:
        path = per_lang.get(lang)
        if not path:
            continue
        try:
            kv = load_kv(path, args.php)
        except (ValueError, subprocess.SubprocessError) as e:
            fs.add("ERROR", unit, "parse", "%s: cannot parse (%s)" % (lang, type(e).__name__))
            continue
        if kv is not None:
            maps[lang] = kv
    if not maps:
        return
    all_keys = set()
    for kv in maps.values():
        all_keys |= set(kv)
    for lang, kv in maps.items():
        missing = sorted(all_keys - set(kv))
        if missing:
            fs.add("ERROR", unit, "missing-keys", "%s: %d keys missing: %s%s" % (
                lang, len(missing), ", ".join(missing[:10]), " …" if len(missing) > 10 else ""))
    for key in sorted(all_keys):
        vals = {lang: kv[key] for lang, kv in maps.items() if key in kv}
        if len(vals) < 2:
            continue
        empty = [lang for lang, v in vals.items() if not v.strip()]
        if empty and len(empty) < len(vals):
            fs.add("ERROR", unit, "empty", "%s: empty in %s" % (key, ",".join(empty)))
        # Distinct placeholders must match; plural forms (a|b|c) legitimately repeat them a different number of times.
        ph = {lang: Counter(PLACEHOLDER.findall(v)) for lang, v in vals.items()}
        fmt = "; ".join("%s=[%s]" % (lang, " ".join(sorted(c.elements()))) for lang, c in ph.items())
        if len({frozenset(c) for c in ph.values()}) > 1:
            fs.add("ERROR", unit, "placeholders", "%s: %s" % (key, fmt))
        elif not any("|" in v for v in vals.values()) and len({tuple(sorted(c.items())) for c in ph.values()}) > 1:
            fs.add("WARN", unit, "placeholders", "%s: placeholder counts differ: %s" % (key, fmt))
        texts = [v for v in vals.values() if " " in v.strip() and re.search(r"[a-zа-я]", v)]
        if len(texts) >= 2 and len(set(texts)) < len(texts):
            same = [lang for lang, v in vals.items() if list(vals.values()).count(v) > 1]
            fs.add("WARN", unit, "untranslated", "%s: identical text in %s" % (key, ",".join(same)))
        for lang, v in vals.items():
            script_and_apostrophe_checks(fs, "%s [%s]" % (unit, key), lang, [(0, v)], args)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--langs", help="comma-separated language folders/files (default: auto-detect)")
    ap.add_argument("--only", help="comma-separated units (relative paths such as faq.md) to check")
    ap.add_argument("--changed", action="store_true", help="only units with uncommitted git changes")
    ap.add_argument("--uz-okina", help="required character for oʻ/gʻ in Uzbek Latin prose outside «…», e.g. ʻ")
    ap.add_argument("--uz-tutuq", help="required character for the tutuq belgisi in Uzbek Latin prose outside «…», e.g. ʼ")
    ap.add_argument("--php", action="store_true", help="also check Laravel *.php lang arrays (executes them with php)")
    ap.add_argument("--errors-only", action="store_true")
    ap.add_argument("--save", metavar="FILE", help="write all findings to FILE (a baseline for --against)")
    ap.add_argument("--against", metavar="FILE", help="report only findings not present in this baseline")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    args = ap.parse_args()

    root = args.root.rstrip("/") or "/"
    langs, units = discover(root, args.langs.split(",") if args.langs else None)
    if len(langs) < 2:
        sys.exit("found fewer than two languages under %s (use --langs uz,ru,en)" % root)
    selected = sorted(units)
    if args.only:
        wanted = set(args.only.split(","))
        selected = [u for u in selected if u in wanted]
    if args.changed:
        ch = changed_units(root, langs)
        selected = [u for u in selected if u in ch]
    skipped_php = 0
    fs = Findings()
    for unit in selected:
        per = units[unit]
        missing = [lang for lang in langs if lang not in per]
        if missing:
            fs.add("ERROR", unit, "missing-file", "missing in: " + ",".join(missing))
        if unit.endswith(".md"):
            check_markdown(fs, unit, per, langs, args)
        elif unit.endswith(".php") and not args.php:
            skipped_php += 1
        else:
            check_kv(fs, unit, per, langs, args)

    findings = fs.items
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(findings, f, ensure_ascii=False, indent=1)
    shown, fixed = findings, None
    if args.against:
        with open(args.against, encoding="utf-8") as f:
            base = json.load(f)
        base_ids = {x["id"] for x in base}
        shown = [x for x in findings if x["id"] not in base_ids]
        # Only units checked in this run can count as fixed (the baseline may cover more units).
        checked = set(selected)
        fixed = len({x["id"] for x in base if x["unit"].split(" [", 1)[0] in checked} - {x["id"] for x in findings})
    if args.errors_only:
        shown = [x for x in shown if x["severity"] == "ERROR"]
    shown = sorted(shown, key=lambda x: (x["severity"] != "ERROR", x["unit"], x["check"]))
    errors = sum(1 for x in shown if x["severity"] == "ERROR")

    if args.json:
        print(json.dumps({"langs": langs, "units_checked": len(selected), "findings": shown, "fixed_since_baseline": fixed},
                         ensure_ascii=False, indent=1))
    else:
        scope = " (changed only)" if args.changed else ""
        print("i18n parity: %s | langs: %s | units checked: %d%s" % (root, " ".join(langs), len(selected), scope))
        for x in shown:
            print("%-5s %s  [%s] %s%s" % (x["severity"], x["unit"], x["check"], x["detail"], ("   @ " + x["where"]) if x["where"] else ""))
        summary = "%d error(s), %d warning(s)" % (errors, len(shown) - errors)
        if args.against:
            summary = "new since baseline: " + summary + "; fixed since baseline: %d" % fixed
        if skipped_php:
            summary += "; %d .php unit(s) skipped (pass --php to check Laravel lang arrays)" % skipped_php
        print(summary)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
