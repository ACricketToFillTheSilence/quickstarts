#!/usr/bin/env python3
"""Load and validate dispute-fighter configuration.

The skill is portable: Stripe is pinned as the disputes system of record, but the
auxiliary sources (customer comms, policy docs, internal chat) and the digest target
are configurable per organization. This module loads `config.yaml` / `config.json`
and merges it over built-in defaults, so the skill works out of the box and any field
can be overridden.

Resolution order for the config path:
  1. explicit path argument
  2. $DISPUTE_FIGHTER_CONFIG
  3. config.yaml / config.yml / config.json next to the skill root (parent of scripts/)

See references/configuration.md for the field-by-field guide.
"""
import copy
import json
import os
import sys

# Built-in defaults. These reproduce the original Zendesk/Drive/Slack setup so the
# skill behaves identically when no config file is supplied.
DEFAULTS = {
    "stripe": {
        "connector": "stripe",      # pinned — the disputes system of record
        "dispute_fee_cents": 1500,  # your Stripe dispute fee, for economics math
        "default_currency": "usd",
    },
    # Evaluation policy. Amounts in cents (to match dispute_fee_cents).
    "evaluation": {
        # Disputes below this amount aren't worth evaluating — the digest marks them SKIP and the
        # full evaluation recommends skipping unless the user insists. 0 = evaluate everything.
        # e.g. 50000 = $500.
        "min_amount_cents": 0,
        # At/above this amount the digest's quick lean tips to FIGHT even at moderate win-odds
        # (big absolute upside). A separate knob from min_amount_cents. Default 20000 = $200.
        "high_value_cents": 20000,
        # Minimum estimated win probability (0..1) the company is willing to fight. Below it, the
        # digest and the full evaluation recommend ACCEPT. 0.0 = no floor (consider everything);
        # e.g. 0.5 = don't fight worse-than-coin-flip.
        "min_winnability": 0.0,
    },
    # Auxiliary evidence/context sources, keyed by the ROLE the skill understands.
    # Swap `connector` and the field names to match another org's stack; set a role
    # to null to disable it.
    "sources": {
        "customer_comms": {
            "connector": "zendesk-support",
            "entity": "tickets",
            "match_field": "requester_email",   # matched against the customer's email
            "comments_entity": "ticket_comments",
        },
        "policies": {
            "connector": "google-drive",
            "entity": "files",
            "name_field": "name",
            "keywords": ["refund policy", "cancellation policy", "terms of service", "shipping"],
        },
        "internal_chat": {
            "connector": "slack",
            "entity": "messages",
            "text_field": "text",
        },
        # Optional: a usage/billing Agent connector. Disabled by default — set it in config
        # to enable. Powerful evidence: feeds Stripe's `access_activity_log` and proves the
        # customer used the service (key for subscription_canceled / fraudulent).
        #   - amplitude : event-level product usage (logins, feature use)
        #   - orb       : usage-based billing — active subscriptions + invoices for metered
        #                 usage (entities: customers/subscriptions/invoices; not raw events)
        # Must be an Airbyte Agent connector.
        "service_usage": None,
        # Optional: e-commerce order data — line items, shipping/fulfillment, tracking, the
        # address the customer provided. Disabled by default (SaaS merchants have no storefront).
        # Feeds shipping_* / product_description / receipt evidence; key for product_not_received,
        # product_unacceptable, and gift ship-to mismatches. Agent connectors:
        #   - shopify | woocommerce | amazon-seller-partner
        "order_data": None,
    },
    "digest": {
        "connector": "slack",
        "channel": "#disputes",
        "post_time": "09:00",
        "timezone": "America/Los_Angeles",
        "urgency_hours": {"red": 48, "orange": 96},
    },
    # Where generated dispute packages and the self-heal knowledge base are saved. Local only:
    # packages go under `local_dir` (pending/ -> won/ or lost/), alongside learnings.md,
    # pitfalls.md, and the index.json ledger.
    "storage": {
        "local_dir": "~/dispute-fighter-data",
    },
    # Override base winnability priors per Stripe reason code (0..1). Empty = use the
    # defaults baked into the digest script / references/stripe-disputes.md.
    "winnability_overrides": {},
}

ROLE_KEYS = ("customer_comms", "policies", "internal_chat", "service_usage", "order_data")

# Plain-English role descriptions, for the guided-setup interview.
ROLE_DESCRIPTIONS = {
    "customer_comms": "Where your customer support / messages live (proof of contact, refund or cancellation requests).",
    "policies": "Where your refund / cancellation / terms-of-service / shipping policy documents live.",
    "internal_chat": "Your team chat, for internal discussion about an order or customer (optional).",
    "service_usage": "A product-analytics or usage/billing tool that proves the customer used the service (optional).",
    "order_data": "Your e-commerce store, for order line items, shipping/fulfillment, and tracking (optional).",
    "digest": "Where the daily digest of disputes should be posted.",
}
OPTIONAL_ROLES = {"internal_chat", "service_usage", "order_data"}

# Verified Airbyte Agent connectors per role. The Agent catalog grows over time, so an
# unknown slug is a warning (verify at docs.airbyte.com/ai-agents/connectors), not a hard error.
SUPPORTED_CONNECTORS = {
    "customer_comms": ["zendesk-support", "intercom", "freshdesk", "salesforce", "hubspot"],
    "policies": ["google-drive", "notion", "confluence", "airtable"],
    "internal_chat": ["slack"],
    "service_usage": ["amplitude", "orb"],
    "order_data": ["shopify", "woocommerce", "amazon-seller-partner"],
    "digest": ["slack"],
}


def validate_config(cfg):
    """Return a list of (level, message). level is 'error' or 'warn'. Empty = clean."""
    issues = []
    if (cfg.get("stripe") or {}).get("connector") != "stripe":
        issues.append(("error", "stripe.connector must be 'stripe' (it is the pinned system of record)."))
    for role, spec in (cfg.get("sources") or {}).items():
        if spec is None:
            continue
        if role not in ROLE_KEYS:
            issues.append(("warn", f"sources.{role} is not a recognized role; it will be ignored."))
            continue
        conn = spec.get("connector")
        if not conn:
            issues.append(("error", f"sources.{role} has no connector. Set one or remove the role."))
        elif conn not in SUPPORTED_CONNECTORS.get(role, []):
            issues.append(("warn", f"sources.{role}.connector '{conn}' isn't a known Agent connector "
                                   f"for this role. Known: {', '.join(SUPPORTED_CONNECTORS.get(role, []))}. "
                                   "Verify at docs.airbyte.com/ai-agents/connectors."))
        if not spec.get("entity"):
            issues.append(("warn", f"sources.{role} has no 'entity' set; the fetch step may not know what to query."))
    dconn = (cfg.get("digest") or {}).get("connector")
    if dconn and dconn not in SUPPORTED_CONNECTORS["digest"]:
        issues.append(("warn", f"digest.connector '{dconn}' isn't an Agent connector; post via a connected tool instead."))
    return issues


def _find_default():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # skill root
    for name in ("config.yaml", "config.yml", "config.json"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            return p
    return None


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path=None):
    path = path or os.environ.get("DISPUTE_FIGHTER_CONFIG") or _find_default()
    if not path:
        return copy.deepcopy(DEFAULTS)
    if not os.path.exists(path):
        sys.exit(f"Config file not found: {path}")
    text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # PyYAML
        except ImportError:
            sys.exit("Config is YAML but PyYAML isn't installed. Run `pip install pyyaml`, "
                     "or provide an equivalent config.json instead.")
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    cfg = _deep_merge(DEFAULTS, data)
    # Stripe stays pinned regardless of what the config says.
    cfg["stripe"]["connector"] = "stripe"
    return cfg


def enabled_sources(cfg):
    """Yield (role, spec) for configured, non-null auxiliary sources."""
    for role in ROLE_KEYS:
        spec = (cfg.get("sources") or {}).get(role)
        if spec:
            yield role, spec


if __name__ == "__main__":  # quick inspector: `python config.py [path]`
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    a = ap.parse_args()
    print(json.dumps(load_config(a.path), indent=2))
