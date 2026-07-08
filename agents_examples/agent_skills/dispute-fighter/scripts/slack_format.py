#!/usr/bin/env python3
"""Shared Slack formatting helpers (Block Kit + text fallback).

Used by the triage digest (daily_dispute_digest.py) and the multi-dispute evaluation summary
(evaluation_summary.py) so both render with the same native Slack `table` block.
"""


def portable_date(now):
    """"Jul 1, 2026" without platform-specific strftime codes (Windows lacks %-d)."""
    return f"{now.strftime('%b')} {now.day}, {now.year}" if hasattr(now, "strftime") else str(now)


def ascii_table(headers, data):
    """Fixed-width table for the plain-text fallback (aligned inside a Slack code block)."""
    if not data:
        widths = [len(h) for h in headers]
    else:
        widths = [max(len(str(headers[i])), *(len(str(row[i])) for row in data)) for i in range(len(headers))]

    def fmt(cells):
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    sep = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers)] + [sep] + [fmt(r) for r in data])


def header_block(title):
    return {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}}


def section_block(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def context_block(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def table_block(headers, data, column_settings=None):
    """Native Slack `table` block (added Aug 2025). First row = headers.

    headers: list of column titles. data: list of row value-lists (same width as headers).
    column_settings: optional per-column {align, is_wrapped} dicts.
    """
    rows = [[{"type": "raw_text", "text": str(h)} for h in headers]]
    for r in data:
        rows.append([{"type": "raw_text", "text": str(c)} for c in r])
    block = {"type": "table", "rows": rows}
    if column_settings:
        block["column_settings"] = column_settings
    return block
