#!/usr/bin/env python3
"""Merge the delegate skill's deny rules into a Claude Code settings.json: adds the missing rules, changes nothing else.

  apply_deny.py --dry-run     # show what would be added to ~/.claude/settings.json, write nothing
  apply_deny.py               # merge references/settings-deny.json into ~/.claude/settings.json
  apply_deny.py --settings ./.claude/settings.json --rules my-rules.json

Defaults: settings = $CLAUDE_CONFIG_DIR/settings.json (else ~/.claude/settings.json); rules = ../references/settings-deny.json,
a file shaped {"permissions": {"deny": ["Bash(...)", ...]}}. Rules are compared as exact strings; every other key and the key
order of the settings file are kept. A settings file that is not valid JSON is never overwritten (exit 1).
"""
import argparse
import json
import os
import sys


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except ValueError as e:
        sys.exit("%s is not valid JSON (%s); fix it by hand, nothing was changed" % (path, e))
    except OSError as e:
        sys.exit("cannot read %s: %s" % (path, e))


def main():
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--settings", default=os.path.join(config, "settings.json"), help="settings file to update (default %(default)s)")
    ap.add_argument("--rules", default=os.path.normpath(os.path.join(here, "..", "references", "settings-deny.json")),
                    help="JSON file with the rules (default %(default)s)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be added, write nothing")
    args = ap.parse_args()

    try:
        wanted = read_json(args.rules)["permissions"]["deny"]
    except (KeyError, TypeError):
        wanted = None
    if not isinstance(wanted, list) or not all(isinstance(r, str) for r in wanted):
        sys.exit('%s: expected {"permissions": {"deny": ["Bash(...)", ...]}}' % args.rules)

    settings = read_json(args.settings) if os.path.exists(args.settings) else {}  # a missing file is an empty one
    if not isinstance(settings, dict):
        sys.exit("%s: expected a JSON object at the top level; nothing was changed" % args.settings)
    perms = settings.setdefault("permissions", {})
    if not isinstance(perms, dict) or not isinstance(perms.setdefault("deny", []), list):
        sys.exit("%s: permissions is not an object or permissions.deny is not a list; nothing was changed" % args.settings)

    deny, added = perms["deny"], []
    for rule in wanted:
        if rule not in deny:
            deny.append(rule)
            added.append(rule)
    print(args.settings + ("  (dry run, nothing written)" if args.dry_run else ""))
    for rule in added:
        print("  + " + rule)
    print("%s %d rule(s), %d already present" % ("would add" if args.dry_run else "added", len(added), len(wanted) - len(added)))
    if added and not args.dry_run:
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"  # serialize first: a failure must not truncate the file
        os.makedirs(os.path.dirname(os.path.abspath(args.settings)), exist_ok=True)
        with open(args.settings, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
