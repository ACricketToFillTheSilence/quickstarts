#!/usr/bin/env python3
"""Build (and optionally post) a daily Slack digest of disputes needing a response.

Lightweight triage only — surfaces and prioritizes open disputes; it does NOT gather
evidence or render a verdict. A human picks ones to work and runs the full
Evaluate/Build flow. Reads Stripe via the Airbyte Agent SDK
(see references/airbyte-data-access.md).

Outputs:
  - digest.json : structured list of open disputes with quick lean + urgency
  - prints Slack mrkdwn text to stdout (send it via your Slack tool), or posts it
    directly with --post if SLACK_BOT_TOKEN + SLACK_CHANNEL are set.

Usage:
    python daily_dispute_digest.py [--out digest.json] [--now 2026-06-29T12:00:00Z]
    python daily_dispute_digest.py --post                 # post via Slack web API
    python daily_dispute_digest.py --from-file dump.json  # triage a saved disputes dump (testing)

--now lets a scheduler/test pin "today" so deadline math is deterministic; defaults
to the current time.
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys

from config import load_config
from storage import knowledge_dir

# Base winnability prior per reason code — kept in sync with references/stripe-disputes.md.
# Used only for the cheap "quick lean" hint, never as the real verdict. An org can override
# any of these via `winnability_overrides` in config.
# Capped at ~0.60: per Stripe, even the strongest disputes top out around a 60% win rate.
WINNABILITY = {
    "product_not_received": 0.6, "duplicate": 0.6, "subscription_canceled": 0.55,
    "unrecognized": 0.5, "fraudulent": 0.4, "product_unacceptable": 0.4,
    "credit_not_processed": 0.3, "general": 0.45,
    # process/bank reasons: rarely worth fighting
    "bank_cannot_process": 0.1, "check_returned": 0.1, "customer_initiated": 0.2,
    "debit_not_authorized": 0.15, "incorrect_account_details": 0.1,
    "insufficient_funds": 0.1, "noncompliant": 0.15,
}
ACTIONABLE = {"needs_response", "warning_needs_response"}


# ---- incremental state: only post disputes new since the last post ----

def _state_path(cfg):
    return os.path.join(knowledge_dir(cfg), "digest_state.json")


def load_watermark(cfg):
    """Return the last-posted timestamp (datetime) or None if never posted."""
    try:
        return _parse_ts(json.load(open(_state_path(cfg))).get("last_posted_at"))
    except Exception:
        return None


def save_watermark(cfg, now):
    json.dump({"last_posted_at": now.isoformat()}, open(_state_path(cfg), "w"), indent=2)


def is_new(dispute, watermark):
    """New = opened after the last post. No watermark (never posted) or no created ts => include."""
    if watermark is None:
        return True
    created = _parse_ts(dispute.get("created"))
    return created is None or created > watermark


def is_due_soon(dispute, now, red_days):
    """Deadline safeguard: still-open dispute whose response deadline is within the red window
    (and not already past). These re-surface even if posted before, so nothing slips."""
    due = _parse_ts((dispute.get("evidence_details") or {}).get("due_by"))
    if due is None:
        return False
    days_left = (due - now).total_seconds() / 86400
    return 0 <= days_left <= red_days


def _parse_ts(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return dt.datetime.fromtimestamp(val, tz=dt.timezone.utc)
    s = str(val).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def quick_lean(reason, amount_cents, win, days_left, fee_cents, high_value_cents, min_win):
    """Cheap hint, not a verdict.

    min_win: the company's winnability floor (evaluation.min_winnability) — below it, lean ACCEPT.
    high_value_cents: at/above this amount, lean FIGHT even at moderate odds (big absolute upside).
    """
    if days_left is not None and days_left < 0:
        return "EXPIRED"
    if reason in ("bank_cannot_process", "check_returned", "debit_not_authorized",
                  "incorrect_account_details", "insufficient_funds", "noncompliant",
                  "customer_initiated"):
        return "ACCEPT"
    if win < min_win:
        return "ACCEPT"  # below the company's winnability floor
    ev = win * (amount_cents / 100.0) - fee_cents / 100.0
    if win >= 0.55 or amount_cents >= high_value_cents:
        return "FIGHT"
    if ev <= 0:
        return "ACCEPT"  # meets the floor but negative EV and neither confident nor high-value
    return "REVIEW"


def triage(disputes, now, cfg):
    fee_cents = cfg["stripe"].get("dispute_fee_cents", 1500)
    eval_cfg = cfg.get("evaluation") or {}
    min_amt = eval_cfg.get("min_amount_cents", 0) or 0
    high_value_cents = eval_cfg.get("high_value_cents", 20000) or 20000
    min_win = eval_cfg.get("min_winnability", 0.0) or 0.0
    wins = dict(WINNABILITY, **(cfg.get("winnability_overrides") or {}))
    rows = []
    for d in disputes:
        if d.get("status") not in ACTIONABLE:
            continue
        reason = d.get("reason", "general")
        amount = d.get("amount", 0)
        due = _parse_ts((d.get("evidence_details") or {}).get("due_by"))
        days_left = round((due - now).total_seconds() / 86400, 1) if due else None
        win = wins.get(reason, 0.45)
        lean = quick_lean(reason, amount, win, days_left, fee_cents, high_value_cents, min_win)
        below = bool(min_amt) and amount < min_amt
        if below and lean != "EXPIRED":
            lean = "SKIP"  # below the configured "worth evaluating" threshold
        rows.append({
            "below_threshold": below,
            "id": d.get("id"),
            "reason": reason,
            "amount_cents": amount,
            "amount": amount / 100.0,
            "currency": (d.get("currency") or "usd").upper(),
            "customer_name": d.get("_customer_name", ""),
            "due_by": due.isoformat() if due else None,
            "days_left": days_left,
            "lean": lean,
        })
    # soonest deadline first (None last), then largest amount
    rows.sort(key=lambda r: (r["days_left"] is None, r["days_left"] if r["days_left"] is not None
                             else 1e9, -r["amount_cents"]))
    return rows


def _emoji(days_left, thresholds):
    if days_left is None:
        return "⚪"
    red = thresholds.get("red", 48) / 24.0
    orange = thresholds.get("orange", 96) / 24.0
    if days_left <= red:
        return "🔴"
    if days_left <= orange:
        return "🟠"
    return "🟢"


def _due_phrase(days_left):
    if days_left is None:
        return "no deadline set"
    if days_left < 0:
        return "OVERDUE"
    if days_left < 1:
        return "due today"
    return f"due in {int(days_left)}d"


def _ascii_table(headers, data):
    """Render a fixed-width table (renders aligned inside a Slack code block)."""
    widths = [max(len(headers[i]), *(len(row[i]) for row in data)) for i in range(len(headers))]
    def fmt(cells):
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)).rstrip()
    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), sep] + [fmt(row) for row in data])


FOOTER = ("Paste `evaluate <dispute-id>` into Claude (with the dispute-fighter skill) to run the full "
          "evaluation — it recommends fight or accept and builds the evidence package if worth fighting. "
          "`SKIP` = below your amount threshold; `EXPIRED` = deadline passed. No auto-submit.")


def _title(now):
    # Portable date (Windows strftime lacks %-d); build "Jul 1, 2026" without platform-specific codes.
    date = f"{now.strftime('%b')} {now.day}, {now.year}" if hasattr(now, "strftime") else str(now)
    return f"Disputes needing a response — {date}"


def _headline(rows, thresholds, since):
    red_days = thresholds.get("red", 48) / 24.0
    total = sum(r["amount"] for r in rows)
    due_soon = sum(1 for r in rows if r["days_left"] is not None and r["days_left"] <= red_days)
    below = sum(1 for r in rows if r.get("below_threshold"))
    reminders = sum(1 for r in rows if r.get("reminder"))
    cur = rows[0]["currency"]
    if since:
        parts = [f"{len(rows) - reminders} new since last digest"]
        if reminders:
            parts.append(f"{reminders} due-soon reminder" + ("s" if reminders != 1 else ""))
    else:
        parts = [f"{len(rows)} open"]
    parts.append(f"{cur} {total:,.2f} at risk")
    parts.append(f"{due_soon} due within {int(thresholds.get('red', 48))}h")
    if below:
        parts.append(f"{below} below evaluate threshold")
    return " · ".join(parts)


def _empty_msg(since):
    return "✅ No new disputes since the last digest." if since else "✅ No disputes awaiting a response today."


def to_slack_mrkdwn(rows, now, thresholds, since=None):
    """Plain mrkdwn text — used as the Block Kit notification fallback and for the code-block view."""
    if not rows:
        return f"🛡️ *{_title(now)}*\n{_empty_msg(since)}"
    data = [[r["id"], f"{r['currency']} {r['amount']:,.2f}", r["reason"],
             _due_phrase(r["days_left"]), r["lean"], (r["customer_name"] or "")[:18]] for r in rows]
    table = _ascii_table(["Dispute", "Amount", "Reason", "Due", "Lean", "Customer"], data)
    return "\n".join([f"🛡️ *{_title(now)}*", _headline(rows, thresholds, since), "",
                      "```", table, "```", f"_{FOOTER}_"])


def to_slack_blocks(rows, now, thresholds, since=None):
    """Native Slack Block Kit layout: header, summary, and a real Slack `table` block of the disputes."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🛡️ {_title(now)}", "emoji": True}}]
    if not rows:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _empty_msg(since)}})
        return blocks
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _headline(rows, thresholds, since)}})
    # Slack `table` block (added Aug 2025): max 100 rows, 20 cols, 10k chars total. Cap rows for headroom.
    shown, overflow = rows[:90], max(0, len(rows) - 90)
    header = ["Dispute", "Amount", "Reason", "Due", "Lean", "Customer"]
    trows = [[{"type": "raw_text", "text": h} for h in header]]
    for r in shown:
        due = _due_phrase(r["days_left"]) + (" (reminder)" if r.get("reminder") else "")
        cells = [r["id"], f"{r['currency']} {r['amount']:,.2f}", r["reason"], due, r["lean"],
                 r["customer_name"] or "—"]
        trows.append([{"type": "raw_text", "text": str(c)} for c in cells])
    blocks.append({
        "type": "table",
        "column_settings": [{}, {"align": "right"}, {"is_wrapped": True}, {}, {}, {"is_wrapped": True}],
        "rows": trows,
    })
    if overflow:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"_…and {overflow} more (see the text fallback / digest.json)._"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": FOOTER}]})
    return blocks


# ---------------- data access ----------------

async def fetch_open_disputes():
    try:
        from airbyte_agent_sdk import connect
    except Exception as e:
        sys.exit("airbyte-agent-sdk unavailable (%s). Use --from-file with a saved dump, or "
                 "install the SDK and set AIRBYTE_CLIENT_ID/SECRET." % e)
    stripe = connect("stripe")
    out = []
    try:
        res = await stripe.context_store_search(
            query={"filter": {"in": {"status": list(ACTIONABLE)}}}, limit=500)
        disputes = res.get("data", res) if isinstance(res, dict) else res
        for d in disputes or []:
            # enrich with customer name (best effort)
            try:
                ch_id = d.get("charge")
                if ch_id:
                    ch = await stripe.execute("charges", "get", params={"id": ch_id})
                    d["_customer_name"] = ((ch or {}).get("billing_details") or {}).get("name", "")
            except Exception:
                pass
            out.append(d)
    finally:
        try:
            await stripe.close()
        except Exception:
            pass
    return out


def post_to_slack(text, blocks=None, default_channel=None):
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL") or default_channel
    if not (token and channel):
        sys.exit("--post needs SLACK_BOT_TOKEN (env) and a channel (env SLACK_CHANNEL or "
                 "digest.channel in config).")
    import urllib.request
    payload = {"channel": channel, "text": text, "unfurl_links": False}
    if blocks:
        payload["blocks"] = blocks  # native layout; `text` is the notification fallback
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req))
    if not resp.get("ok"):
        sys.exit(f"Slack post failed: {resp.get('error')}")
    print(f"Posted to {channel}.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="digest.json")
    ap.add_argument("--now", help="ISO timestamp to treat as 'now' (deadline math)")
    ap.add_argument("--from-file", help="Saved disputes dump (list, or {'disputes':[...]}) for testing")
    ap.add_argument("--post", action="store_true", help="Post via Slack web API (needs env vars); marks the watermark on success")
    ap.add_argument("--all", action="store_true", help="Include every open dispute, not just those new since the last post")
    ap.add_argument("--mark-posted", action="store_true", help="Advance the 'last posted' watermark to now and exit (use after posting via a Slack tool)")
    ap.add_argument("--config", help="Path to config.yaml/json (defaults to skill root or $DISPUTE_FIGHTER_CONFIG)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    digest_cfg = cfg.get("digest", {})
    thresholds = digest_cfg.get("urgency_hours", {"red": 48, "orange": 96})
    now = _parse_ts(args.now) or dt.datetime.now(dt.timezone.utc)

    if args.mark_posted:  # record that a digest was posted (for the MCP/Slack-tool flow)
        save_watermark(cfg, now)
        print(f"Watermark advanced to {now.isoformat()} — next digest shows only newer disputes.")
        return

    if args.from_file:
        with open(args.from_file) as f:
            raw = json.load(f)
        disputes = raw.get("disputes", raw) if isinstance(raw, dict) else raw
        if isinstance(disputes, dict):  # a single dispute_context.json
            disputes = [disputes.get("dispute", disputes)]
    else:
        disputes = asyncio.run(fetch_open_disputes())

    # Incremental (unless --all): keep disputes opened since the last post, PLUS any still-open
    # dispute now within the "due soon" window (deadline safeguard) so nothing slips unseen.
    watermark = None if args.all else load_watermark(cfg)
    red_days = thresholds.get("red", 48) / 24.0
    reminder_ids = set()
    if watermark is not None:
        kept = []
        for d in disputes:
            new = is_new(d, watermark)
            soon = is_due_soon(d, now, red_days)
            if new or soon:
                kept.append(d)
                if soon and not new:
                    reminder_ids.add(d.get("id"))
        disputes = kept

    rows = triage(disputes, now, cfg)
    for r in rows:
        r["reminder"] = r["id"] in reminder_ids
    text = to_slack_mrkdwn(rows, now, thresholds, since=watermark)      # notification fallback
    blocks = to_slack_blocks(rows, now, thresholds, since=watermark)    # native Slack layout
    with open(args.out, "w") as f:
        json.dump({"generated_at": now.isoformat(), "since": watermark.isoformat() if watermark else None,
                   "count": len(rows), "reminders": len(reminder_ids),
                   "text": text, "blocks": blocks, "disputes": rows}, f, indent=2)
    if args.post:
        post_to_slack(text, blocks=blocks, default_channel=digest_cfg.get("channel"))
        save_watermark(cfg, now)  # only advance the watermark once it's actually posted
    else:
        print(text)
        print("\n(Block Kit `blocks` for a native Slack layout written to " + args.out + ")")


if __name__ == "__main__":
    main()
