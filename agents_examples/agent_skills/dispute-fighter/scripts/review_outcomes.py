#!/usr/bin/env python3
"""Outcome review & self-heal for the daily run (steps 3-4).

Two phases so the model can write qualitative lessons between them, while the ledger guarantees
each dispute is processed exactly once:

  --scan        For every PENDING dispute in the ledger, check its current Stripe status. Print a
                JSON worklist of those now closed (won/lost). Does NOT move or mark anything, so it's
                safe to re-run. Live status comes from the Airbyte Agent SDK (Stripe); for testing,
                pass --outcomes-file mapping dispute_id -> "won"/"lost"/"open".

  --finalize ID OUTCOME   Move the package to the won/ or lost/ folder and mark the ledger status,
                so it is never reviewed again.

Daily routine (see SKILL.md): run --scan; for each item, append an anonymized pattern lesson to
learnings.md (won) / pitfalls.md (lost); then --finalize each. Because --scan only returns disputes
whose ledger status is still "pending", finalized disputes never reappear.

Usage:
    python review_outcomes.py --scan [--outcomes-file outcomes.json] [--config config.json]
    python review_outcomes.py --finalize dp_123 won [--config config.json]
"""
import argparse
import asyncio
import json
import sys

from config import load_config
from storage import Ledger, resolve_storage

CLOSED = {"won", "lost"}


async def _stripe_status(dispute_id):
    from airbyte_agent_sdk import connect
    s = connect("stripe")
    try:
        d = await s.execute("disputes", "get", params={"id": dispute_id})
        return (d or {}).get("status")
    finally:
        try:
            await s.close()
        except Exception:
            pass


def _status_lookup(dispute_id, outcomes):
    if outcomes is not None:
        return outcomes.get(dispute_id)
    try:
        return asyncio.run(_stripe_status(dispute_id))
    except Exception as e:
        print(f"WARNING: could not read Stripe status for {dispute_id}: {e}", file=sys.stderr)
        return None


def do_scan(cfg, outcomes):
    ledger = Ledger(cfg)
    worklist = []
    for did, rec in ledger.pending().items():
        status = _status_lookup(did, outcomes)
        if status in CLOSED:
            worklist.append({"dispute_id": did, "reason": rec.get("reason", ""),
                             "amount": rec.get("amount", ""), "currency": rec.get("currency", ""),
                             "outcome": status, "location": rec.get("location", "")})
    print(json.dumps({"closed": worklist, "count": len(worklist)}, indent=2))
    if not worklist:
        print("# No newly-closed disputes with a package. Nothing to learn from today.", file=sys.stderr)
    return worklist


def do_finalize(cfg, dispute_id, outcome):
    if outcome not in CLOSED:
        sys.exit("outcome must be 'won' or 'lost'")
    ledger = Ledger(cfg)
    rec = ledger.get(dispute_id)
    if not rec:
        sys.exit(f"{dispute_id} not in ledger.")
    if rec.get("status") in CLOSED:
        print(f"{dispute_id} already finalized as {rec['status']} — skipping (idempotent).")
        return
    storage, note = resolve_storage(cfg)
    try:
        location = storage.move(dispute_id, outcome)
    except FileNotFoundError as e:
        # Don't mark the ledger won/lost if we couldn't preserve the package — leave it pending.
        sys.exit(f"Not finalizing {dispute_id}: {e}. The ledger stays 'pending'. "
                 "Re-save the package (save_package.py) or investigate before finalizing.")
    ledger.upsert(dispute_id, status=outcome, location=str(location))
    print(f"Finalized {dispute_id} as {outcome} ({note}); moved to {location}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--finalize", nargs=2, metavar=("DISPUTE_ID", "OUTCOME"))
    ap.add_argument("--outcomes-file", help="JSON {dispute_id: won|lost|open} for offline/testing")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    outcomes = json.load(open(args.outcomes_file)) if args.outcomes_file else None
    if args.scan:
        do_scan(cfg, outcomes)
    else:
        do_finalize(cfg, args.finalize[0], args.finalize[1])


if __name__ == "__main__":
    main()
