#!/usr/bin/env python3
"""Gather all context for a Stripe dispute into one dispute_context.json.

Stripe is pinned as the disputes system of record. The auxiliary sources
(customer comms, policy docs, internal chat) are configurable per org — which
connector fills each role and which fields to match on come from config.yaml
(see references/configuration.md and scripts/config.py). All access is via the
Airbyte Agent SDK (see references/airbyte-data-access.md). Downstream steps read
ONLY the output file, so they behave identically on live data or a saved dump.

Usage:
    python fetch_dispute_context.py --dispute dp_123 [--out dispute_context.json] [--config config.yaml]
    python fetch_dispute_context.py --charge ch_123          # resolve dispute from a charge

Requires AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET in the environment. If the SDK
or creds are unavailable, the script exits with a clear message — fall back to
gathering the same fields by hand, or supply a pre-built dispute_context.json.

Output is keyed by ROLE (customer_comms / policies / internal_chat), not tool name,
so the evidence-building step is identical no matter which tools an org configures.
"""
import argparse
import asyncio
import json
import sys

from config import load_config, enabled_sources


def _require_sdk():
    try:
        from airbyte_agent_sdk import connect  # noqa: F401
    except Exception as e:  # pragma: no cover - environment dependent
        sys.exit(
            "airbyte-agent-sdk not available (%s).\n"
            "Install with `uv pip install airbyte-agent-sdk` and set "
            "AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET, or supply a pre-built "
            "dispute_context.json and skip this step." % e
        )
    from airbyte_agent_sdk import connect
    return connect


async def _first(coro):
    res = await coro
    data = res.get("data", res) if isinstance(res, dict) else res
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _build_query(role, spec, email, name):
    """Role-specific Context Store filter, using configured field names."""
    if role == "customer_comms":
        if not email:
            return None
        return {"filter": {"eq": {spec.get("match_field", "requester_email"): email}}}
    if role == "policies":
        nf = spec.get("name_field", "name")
        kws = spec.get("keywords", [])
        return {"filter": {"or": [{"like": {nf: kw}} for kw in kws]}} if kws else None
    if role == "internal_chat":
        term = name or email
        if not term:
            return None
        return {"filter": {"fuzzy": {spec.get("text_field", "text"): term}}}
    if role == "service_usage":
        # Usage/analytics events keyed by a customer identifier. The identifier field
        # is org-specific (email, user_id, external_id); default to matching on email.
        if not email:
            return None
        return {"filter": {"eq": {spec.get("match_field", "email"): email}}}
    if role == "order_data":
        # E-commerce orders. Default to matching the customer email on the orders entity.
        # If you key orders by an order id (e.g. Amazon, or a Stripe metadata order id),
        # set `match_field` and adjust how the value is sourced.
        if not email:
            return None
        return {"filter": {"eq": {spec.get("match_field", "email"): email}}}
    return None


async def gather(cfg, dispute_id=None, charge_id=None):
    connect = _require_sdk()
    stripe = connect(cfg["stripe"]["connector"])  # pinned to "stripe"
    ctx = {"source": "airbyte-agent-sdk", "_config_sources": {}}

    try:
        # --- Stripe: dispute + related objects (pinned) ---
        if dispute_id is None and charge_id is not None:
            disp = await _first(stripe.context_store_search(
                query={"filter": {"eq": {"charge": charge_id}}}, limit=1))
        else:
            disp = await stripe.execute("disputes", "get", params={"id": dispute_id})
        if not disp:
            sys.exit("No dispute found for the given id.")
        ctx["dispute"] = disp

        ch_id = charge_id or disp.get("charge")
        if ch_id:
            ctx["charge"] = await stripe.execute("charges", "get", params={"id": ch_id})
        cust_id = (ctx.get("charge") or {}).get("customer") or disp.get("customer")
        if cust_id:
            ctx["customer"] = await stripe.execute("customers", "get", params={"id": cust_id})
        for entity, key in [("invoices", "invoices"), ("subscriptions", "subscriptions"),
                            ("refunds", "refunds"), ("payment_intents", "payment_intent")]:
            try:
                if cust_id:
                    r = await stripe.context_store_search(
                        query={"filter": {"eq": {"customer": cust_id}}}, limit=25)
                    ctx[key] = r.get("data", r) if isinstance(r, dict) else r
            except Exception as e:
                ctx.setdefault("_warnings", []).append(f"stripe.{entity}: {e}")
    finally:
        await _safe_close(stripe)

    email = (ctx.get("customer") or {}).get("email")
    name = (ctx.get("customer") or {}).get("name")
    warnings = ctx.setdefault("_warnings", [])

    # --- Configurable auxiliary sources, keyed by role ---
    for role, spec in enabled_sources(cfg):
        ctx["_config_sources"][role] = spec.get("connector")
        query = _build_query(role, spec, email, name)
        ctx[role] = await _safe_source(spec["connector"], spec.get("entity"), query, warnings)

    return ctx


async def _safe_source(connector, entity, query, warnings):
    """Connect to a configured source and search; never fatal — log and continue."""
    if query is None:
        return []
    connect = _require_sdk()
    conn = None
    try:
        conn = connect(connector)
        # context_store_search is entity-scoped on some connectors and connection-level
        # on others; try the entity attribute first, then fall back.
        target = getattr(conn, entity, None) if entity else None
        searcher = target.context_store_search if target is not None and hasattr(
            target, "context_store_search") else conn.context_store_search
        res = await searcher(query=query, limit=50)
        return res.get("data", res) if isinstance(res, dict) else res
    except Exception as e:
        warnings.append(f"{connector}/{entity}: {e}")
        return []
    finally:
        await _safe_close(conn)


async def _safe_close(conn):
    try:
        if conn is not None:
            await conn.close()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dispute", help="Stripe dispute id (dp_...)")
    g.add_argument("--charge", help="Stripe charge id (ch_...) to resolve the dispute from")
    ap.add_argument("--out", default="dispute_context.json")
    ap.add_argument("--config", help="Path to config.yaml/json (defaults to skill root or $DISPUTE_FIGHTER_CONFIG)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    ctx = asyncio.run(gather(cfg, dispute_id=args.dispute, charge_id=args.charge))
    with open(args.out, "w") as f:
        json.dump(ctx, f, indent=2, default=str)
    warns = ctx.get("_warnings") or []
    print(f"Wrote {args.out}." + (f" {len(warns)} warning(s): see _warnings." if warns else ""))


if __name__ == "__main__":
    main()
