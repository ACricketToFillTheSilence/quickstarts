#!/usr/bin/env python3
"""Repackage this skill — including the user's config.json — into a distributable .skill file.

Run this at the end of guided setup (or any time the config changes) to produce a pre-configured
`dispute-fighter.skill` the user can reinstall or share. A `.skill` file is just a zip of the skill
folder, so this needs no dependencies.

Why bundle config: the loader reads config.json from the skill folder at runtime, so an in-place
config is already active without reinstalling. Bundling is for (a) sharing a ready-to-go skill, or
(b) install locations that are read-only, where you configure a working copy and reinstall the result.

Safety: config.json contains NO secrets by design (connectors, channel, thresholds, local path);
credentials live in MCP OAuth or environment variables. This script refuses to bundle a config that
looks like it contains secret-ish keys, so nothing sensitive ends up in a shared package.

Usage:
    python scripts/package_self.py                 # writes ./dispute-fighter.skill
    python scripts/package_self.py --out ~/Desktop  # writes to a chosen directory
"""
import argparse
import json
import os
import sys
import zipfile

EXCLUDE_DIRS = {"__pycache__", "node_modules", "evals", ".git"}
EXCLUDE_EXT = {".pyc", ".skill"}
EXCLUDE_NAMES = {".DS_Store"}
# Keys that would indicate secrets accidentally placed in config — refuse to bundle these.
SECRET_HINTS = ("secret", "token", "password", "api_key", "apikey", "client_secret", "private")


def _skill_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _check_config_secret_free(root):
    """Fail loudly if config.json/config.yaml appears to contain secrets."""
    for name in ("config.json",):  # only JSON is machine-checkable without PyYAML
        p = os.path.join(root, name)
        if not os.path.exists(p):
            continue
        try:
            blob = json.dumps(json.load(open(p))).lower()
        except Exception:
            continue
        hit = next((h for h in SECRET_HINTS if h in blob), None)
        if hit:
            sys.exit(f"Refusing to bundle {name}: it contains a '{hit}'-like key. Config must be "
                     "secret-free (credentials belong in MCP OAuth or env vars). Remove it and retry.")


def _included(rel_parts, name):
    if any(part in EXCLUDE_DIRS for part in rel_parts):
        return False
    if name in EXCLUDE_NAMES:
        return False
    if os.path.splitext(name)[1] in EXCLUDE_EXT:
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.getcwd(), help="Output directory (default: current dir)")
    args = ap.parse_args()

    root = _skill_root()
    name = os.path.basename(root)  # "dispute-fighter"
    if not os.path.exists(os.path.join(root, "SKILL.md")):
        sys.exit(f"SKILL.md not found in {root}; is this the skill folder?")
    _check_config_secret_free(root)

    out_dir = os.path.abspath(os.path.expanduser(args.out))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{name}.skill")

    has_config = any(os.path.exists(os.path.join(root, c)) for c in ("config.json", "config.yaml"))
    added = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            rel_dir = os.path.relpath(dirpath, root)
            rel_parts = [] if rel_dir == "." else rel_dir.split(os.sep)
            for fn in sorted(filenames):
                if not _included(rel_parts, fn):
                    continue
                abs_p = os.path.join(dirpath, fn)
                arc = "/".join([name, *rel_parts, fn])  # forward slashes: valid zip paths on all OSes
                z.write(abs_p, arc)
                added += 1

    print(f"Repackaged {added} files -> {out_path}")
    print("Config bundled: " + ("yes (config.*)" if has_config else "no config.* present — shipped defaults/examples only"))
    print("\nReinstall:")
    print("  Claude Code:   unzip -o \"%s\" -d ~/.claude/skills/" % out_path)
    print("  Claude Desktop: Settings > Capabilities > Skills > upload this .skill (replace the old one)")
    print("\nNote: if you edited config.json inside an already-installed, writable skill folder, the")
    print("skill already uses it — reinstalling is only needed to share it or for read-only installs.")


if __name__ == "__main__":
    main()
