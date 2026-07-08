#!/usr/bin/env python3
"""Save a generated dispute package to storage and register it in the ledger (steps 1-2).

Call this right after building a package. It copies the package folder into the local storage dir
(`local_dir`/pending/dp_<id>/) and records the dispute in the local ledger so the daily outcome
review can find it later.

Usage:
    python save_package.py --dispute dp_123 --dir ./out \
        --reason product_not_received --amount 129.00 --currency USD [--config config.json]
"""
import argparse
import sys

from config import load_config
from storage import Ledger, resolve_storage


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dispute", required=True)
    ap.add_argument("--dir", required=True, help="Folder containing the generated package files")
    ap.add_argument("--reason", default="")
    ap.add_argument("--amount", default="")
    ap.add_argument("--currency", default="")
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = load_config(args.config)
    storage, note = resolve_storage(cfg)
    ledger = Ledger(cfg)

    location = storage.save(args.dispute, args.dir)
    ledger.upsert(args.dispute, status="pending", reason=args.reason, amount=args.amount,
                  currency=args.currency, location=str(location), target=storage.kind)
    print(f"Saved package for {args.dispute} -> {note} at {location}")
    print(f"Ledger: {ledger.path}")


if __name__ == "__main__":
    main()
