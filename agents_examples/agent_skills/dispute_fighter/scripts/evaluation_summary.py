#!/usr/bin/env python3
"""Render a multi-dispute evaluation summary as a native Slack `table` (Block Kit) + text fallback.

When you evaluate several disputes at once (SKILL.md Step 0), author a small JSON of the results and
run this to get a Slack-ready consolidated summary table that matches the digest's look. Post it with
`blocks` (from the output file) so the native table renders; `text` is the notification fallback.

Input JSON:
{
  "disputes": [
    {"id": "dp_1", "amount": "129.00", "currency": "USD", "reason": "product_not_received",
     "verdict": "FIGHT", "due": "due in 16d", "package": "pending/dp_1"}
  ]
}
Per dispute, `id` / `reason` / `verdict` are the essentials; the rest default to "—".
`verdict` is FIGHT | ACCEPT | SKIP. `due` is a display string (or pass `due_by`).

Usage:
    python evaluation_summary.py input.json [--now ISO] [--out eval_summary.json] [--title "..."]
"""
import argparse
import datetime as dt
import json
from collections import Counter

from slack_format import (portable_date, ascii_table, header_block, section_block,
                          context_block, table_block)

FOOTER = ("Recommendations for human review — nothing has been submitted to Stripe. "
          "`FIGHT` = contest (package built); `ACCEPT`/`SKIP` = don't. Open a package before submitting.")


def _amount_str(d):
    cur = str(d.get("currency") or "").upper()
    amt = d.get("amount")
    try:
        return f"{cur} {float(str(amt).replace(',', '')):,.2f}".strip()
    except (TypeError, ValueError):
        return str(amt) if amt not in (None, "") else "—"


def build(data, now, title=None):
    disputes = data.get("disputes", data) if isinstance(data, dict) else data
    title = title or f"Dispute evaluation summary — {portable_date(now)}"
    if not disputes:
        return (f"🛡️ *{title}*\nNo disputes evaluated.",
                [header_block(f"🛡️ {title}"), section_block("No disputes evaluated.")])

    verdicts = Counter((d.get("verdict") or "—").upper() for d in disputes)
    fight_total = 0.0
    for d in disputes:
        if (d.get("verdict") or "").upper() == "FIGHT":
            try:
                fight_total += float(str(d.get("amount", "0")).replace(",", ""))
            except (TypeError, ValueError):
                pass
    cur = next((str(d.get("currency", "")).upper() for d in disputes if d.get("currency")), "")
    parts = [f"{len(disputes)} evaluated"]
    for v in ("FIGHT", "ACCEPT", "SKIP"):
        if verdicts.get(v):
            parts.append(f"{verdicts[v]} {v.lower()}")
    if fight_total:
        parts.append(f"{cur} {fight_total:,.2f} recommended to fight")
    headline = " · ".join(parts)

    headers = ["Dispute", "Amount", "Reason", "Verdict", "Due", "Package"]
    rows = [[d.get("id", "—"), _amount_str(d), d.get("reason", "—"),
             (d.get("verdict") or "—").upper(), d.get("due") or d.get("due_by") or "—",
             d.get("package") or "—"] for d in disputes[:90]]
    col_settings = [{}, {"align": "right"}, {"is_wrapped": True}, {}, {}, {"is_wrapped": True}]

    text = "\n".join([f"🛡️ *{title}*", headline, "", "```", ascii_table(headers, rows), "```", f"_{FOOTER}_"])
    blocks = [header_block(f"🛡️ {title}"), section_block(headline),
              table_block(headers, rows, column_settings=col_settings), context_block(FOOTER)]
    return text, blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="JSON of evaluated disputes")
    ap.add_argument("--now", help="ISO timestamp for the title date")
    ap.add_argument("--out", default="eval_summary.json")
    ap.add_argument("--title")
    args = ap.parse_args()
    now = (dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now
           else dt.datetime.now(dt.timezone.utc))
    with open(args.input) as f:
        data = json.load(f)
    text, blocks = build(data, now, args.title)
    with open(args.out, "w") as f:
        json.dump({"text": text, "blocks": blocks}, f, indent=2)
    print(text)
    print(f"\n(Block Kit `blocks` for a native Slack table written to {args.out} — post with `blocks`.)")


if __name__ == "__main__":
    main()
