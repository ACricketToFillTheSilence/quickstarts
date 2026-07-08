#!/usr/bin/env python3
"""Storage + self-heal bookkeeping for generated dispute packages.

Design (see SKILL.md "Storage & self-heal"):
- Packages and the knowledge base are saved LOCALLY under `local_dir`:
    learnings.md, pitfalls.md, and index.json (the authoritative ledger), plus the package folders.
- The ledger guarantees idempotency: each dispute is processed once. won/lost placement is the
  human-facing organization; the ledger `status` is the source of truth for "already reviewed".

Folder layout under the package target:
    <base>/pending/dp_<id>/   ... newly generated, awaiting outcome
    <base>/won/dp_<id>/       ... closed in our favor
    <base>/lost/dp_<id>/      ... closed against us

This module is imported by save_package.py and review_outcomes.py.
"""
import json
import os
import shutil
import datetime

from config import load_config


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def knowledge_dir(cfg):
    d = os.path.expanduser((cfg.get("storage") or {}).get("local_dir", "~/dispute-fighter-data"))
    os.makedirs(d, exist_ok=True)
    return d


class Ledger:
    """Local JSON ledger of every dispute we've generated a package for."""
    def __init__(self, cfg):
        self.path = os.path.join(knowledge_dir(cfg), "index.json")
        self.data = {"disputes": {}}
        if os.path.exists(self.path):
            try:
                self.data = json.load(open(self.path))
            except Exception:
                pass

    def save(self):
        json.dump(self.data, open(self.path, "w"), indent=2)

    def get(self, dispute_id):
        return self.data["disputes"].get(dispute_id)

    def upsert(self, dispute_id, **fields):
        rec = self.data["disputes"].setdefault(dispute_id, {})
        rec.update(fields)
        self.save()
        return rec

    def pending(self):
        return {k: v for k, v in self.data["disputes"].items() if v.get("status") == "pending"}


class LocalStorage:
    kind = "local"

    def __init__(self, base):
        self.base = os.path.expanduser(base)
        for sub in ("pending", "won", "lost"):
            os.makedirs(os.path.join(self.base, sub), exist_ok=True)

    def save(self, dispute_id, src_dir):
        dest = os.path.join(self.base, "pending", dispute_id)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(src_dir, dest)
        return dest

    def move(self, dispute_id, outcome):  # outcome in {"won","lost"}
        src = os.path.join(self.base, "pending", dispute_id)
        dest = os.path.join(self.base, outcome, dispute_id)
        if not os.path.exists(src):
            # No pending package to preserve — signal the caller so it doesn't falsely finalize.
            raise FileNotFoundError(f"pending package not found for {dispute_id} at {src}")
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.move(src, dest)
        return dest


def resolve_storage(cfg):
    """Return (storage, note). Packages are stored locally."""
    return LocalStorage(knowledge_dir(cfg)), "local"


# ---- knowledge files (learnings / pitfalls), always local ----

LEARNINGS_HEADER = ("# Dispute Learnings\n\nWhat *worked* in disputes we WON. Pattern-level and "
                    "anonymized (no customer PII). The skill reads this before building new packages.\n")
PITFALLS_HEADER = ("# Dispute Pitfalls\n\nWhat *failed* in disputes we LOST. Pattern-level and "
                   "anonymized (no customer PII). The skill reads this to avoid repeating mistakes.\n")


def _kfile(cfg, name, header):
    p = os.path.join(knowledge_dir(cfg), name)
    if not os.path.exists(p):
        open(p, "w").write(header)
    return p


def learnings_path(cfg):
    return _kfile(cfg, "learnings.md", LEARNINGS_HEADER)


def pitfalls_path(cfg):
    return _kfile(cfg, "pitfalls.md", PITFALLS_HEADER)


def append_lesson(cfg, won, reason, text):
    """Append an anonymized, dated bullet to learnings.md (won) or pitfalls.md (lost)."""
    path = learnings_path(cfg) if won else pitfalls_path(cfg)
    line = f"\n- [{_now()[:10]}] `{reason}` — {text.strip()}\n"
    with open(path, "a") as f:
        f.write(line)
    return path
