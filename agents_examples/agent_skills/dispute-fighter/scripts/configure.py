#!/usr/bin/env python3
"""Helpers for the skill's GUIDED SETUP — so users never hand-edit YAML.

The skill (Claude) runs the setup conversation, then uses this script to:
  - `--list`              show each role, whether it's optional, and the Agent connectors available
  - `--validate PATH`     check a config file (connector slugs valid, required fields present)
  - `--scaffold PATH`     write a starter config.json with defaults + every optional role disabled,
                          which Claude then edits to match the user's answers

This is NOT an interactive prompt — the user answers Claude's questions in chat, and Claude
writes/validates the file. Keeping config as JSON means zero extra dependencies (no PyYAML).

Examples:
    python configure.py --list
    python configure.py --scaffold "../config.json"
    python configure.py --validate "../config.json"
"""
import argparse
import json
import os
import sys

from config import (DEFAULTS, ROLE_KEYS, ROLE_DESCRIPTIONS, OPTIONAL_ROLES,
                    SUPPORTED_CONNECTORS, load_config, validate_config)


def do_list():
    print("Stripe is always the pinned disputes source — you only configure the rest.\n")
    for role in ROLE_KEYS + ("digest",):
        tag = " (optional — leave off if you don't use it)" if role in OPTIONAL_ROLES else ""
        print(f"• {role}{tag}\n    {ROLE_DESCRIPTIONS.get(role,'')}")
        print(f"    Agent connectors: {', '.join(SUPPORTED_CONNECTORS.get(role, [])) or '—'}\n")
    print("• storage — generated packages + learnings/pitfalls are saved LOCALLY (storage.local_dir).\n")
    print("Catalog grows over time — see docs.airbyte.com/ai-agents/connectors for the latest.")


def do_scaffold(path):
    # Start from defaults, but disable all optional roles so the user opts in explicitly.
    cfg = json.loads(json.dumps(DEFAULTS))
    for role in OPTIONAL_ROLES:
        cfg["sources"][role] = None
    cfg.pop("winnability_overrides", None)
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    print(f"Wrote starter config to {path}. Edit the connectors to match the user's tools, "
          "then run --validate.")


CONFIG_START = "=== DISPUTE-FIGHTER CONFIG (do not remove) ==="
CONFIG_END = "=== END DISPUTE-FIGHTER CONFIG ==="


def do_emit(path):
    """Print a paste-ready config block for a Claude Project (persists with no reinstall)."""
    if path and path.endswith(".json") and os.path.exists(path):
        cfg = json.load(open(path))          # emit exactly what the user set (minimal)
    else:
        cfg = load_config(path)              # fall back to merged/defaults
    print("Paste this whole block into your Claude Project's custom instructions (or save it as a")
    print("Project knowledge file). Keep the marker lines — the skill finds the config by them.\n")
    print(CONFIG_START)
    print("```json")
    print(json.dumps(cfg, indent=2))
    print("```")
    print(CONFIG_END)


def do_validate(path):
    cfg = load_config(path)
    issues = validate_config(cfg)
    errors = [m for lvl, m in issues if lvl == "error"]
    warns = [m for lvl, m in issues if lvl == "warn"]
    sources = cfg.get("sources") or {}
    enabled = [(r, (sources[r] or {}).get("connector"))
               for r in ROLE_KEYS if sources.get(r)]
    print("Configured sources:")
    print(f"  stripe (pinned)")
    for r, c in enabled:
        print(f"  {r}: {c}")
    print(f"  digest: {(cfg.get('digest') or {}).get('connector')} -> {(cfg.get('digest') or {}).get('channel')}")
    for m in errors:
        print("ERROR: " + m)
    for m in warns:
        print("warn:  " + m)
    if not issues:
        print("\nConfig looks good. ✅")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--scaffold", metavar="PATH")
    g.add_argument("--validate", metavar="PATH")
    g.add_argument("--emit", nargs="?", const="", default=argparse.SUPPRESS, metavar="PATH",
                   help="Print a paste-ready config block for a Claude Project (from PATH if given)")
    args = ap.parse_args()
    if args.list:
        do_list()
    elif args.scaffold:
        do_scaffold(args.scaffold)
    elif args.validate:
        sys.exit(do_validate(args.validate))
    else:  # --emit (the only remaining required-group choice)
        do_emit(getattr(args, "emit", "") or None)


if __name__ == "__main__":
    main()
