# Configuration Guide

This skill is portable across organizations. **Stripe is pinned** as the disputes system of record;
everything else is configurable. This guide explains the config file and how to adapt the skill to a
new org's stack.

## Easiest path: guided setup (no file editing)

Most users shouldn't edit these files by hand. Ask the skill to **"set up dispute-fighter"** and it
runs a short conversation — which tool for each role — then writes and validates `config.json` for you.
Under the hood it uses `scripts/configure.py` (`--list` to show options, `--scaffold` to start from
safe defaults, `--validate` to check the result). The rest of this doc is the reference for what those
fields mean, for power users who prefer to edit directly.

## Where config lives

`config.yaml` (or `config.json`) at the skill root. The loader (`scripts/config.py`) resolves it in
this order: an explicit `--config` flag → `$DISPUTE_FIGHTER_CONFIG` → `config.yaml`/`config.yml`/
`config.json` at the skill root. If none exists, built-in defaults (Zendesk / Google Drive / Slack)
apply, so the skill runs out of the box.

To set up a new org: `cp config.example.yaml config.yaml` and edit. YAML needs PyYAML
(`pip install pyyaml`); if you'd rather avoid the dependency, write the same structure as
`config.json`.

### On Claude.ai / Desktop with a Project (persists, no reinstall)

The Chat surface can't persist a `config.json` (the installed skill is read-only and the sandbox is
wiped between sessions). Instead, keep the config in the **Project**: run
`python scripts/configure.py --emit config.json` and paste the emitted block — bounded by
`=== DISPUTE-FIGHTER CONFIG (do not remove) ===` markers — into the Project's custom instructions (or a
Project knowledge file). Each session the skill finds that block and rebuilds a scratch `config.json`
from it, so your settings persist and you never reinstall. To change settings, edit the block.

## The role model

The skill thinks in **roles**, not tool names. Each role is filled by exactly one Airbyte connector.
This indirection is what makes the skill portable: swap the connector for a role and nothing else
changes, because all code and the fetched-context JSON are keyed by role.

Every connector below must be an **Airbyte Agent connector** — the ~60-connector catalog used by
`airbyte-agent-sdk`, *not* the larger classic ELT connector list. Confirm any choice against
<https://docs.airbyte.com/ai-agents/connectors> (or `dir(airbyte_agent_sdk.connectors)` at runtime).
The "alternatives" below are all verified Agent connectors.

| Role | Purpose | Default | Agent-connector alternatives |
|---|---|---|---|
| `stripe` | Disputes + payment records (**pinned**) | stripe | — |
| `customer_comms` | Customer contact history (evidence of communication) | zendesk-support | intercom, freshdesk, salesforce, hubspot |
| `policies` | Internal policy documents | google-drive | notion, confluence, airtable |
| `internal_chat` | Internal discussion about the customer/order | slack | *(Slack is currently the only chat Agent connector)* |
| `service_usage` *(optional)* | Proof the customer used the service (→ `access_activity_log`) | off | amplitude, orb |
| `order_data` *(optional)* | Order line items, shipping/fulfillment/tracking, provided address | off | shopify, woocommerce, amazon-seller-partner |
| `digest` | Where the daily digest posts | slack | *(Slack; Teams/email are not Agent connectors — post those via a connected non-Agent tool)* |

> Tools like Front, SharePoint, Microsoft Teams, Mixpanel, and Segment exist as classic Airbyte ELT
> connectors but are **not** in the Agent catalog today, so they can't fill a role here. If you need
> one, either use an available Agent connector for that role or post/read via a separate connected
> tool outside this skill.

## Fields

### `stripe` (pinned connector, account tunables)
- `dispute_fee_cents` — your Stripe per-dispute fee; drives the economics math (default 1500).
- `default_currency` — fallback currency code.
- `connector` is forced to `stripe` even if overridden.

### `evaluation`
- `min_amount_cents` — the smallest dispute worth evaluating, in cents (e.g. `50000` = $500). Disputes
  below it are marked **SKIP** in the digest and the full evaluation recommends skipping them (unless
  the user explicitly asks to evaluate one anyway). Default `0` = evaluate everything.
- `high_value_cents` — at/above this amount the digest's quick lean tips to **FIGHT** even at moderate
  win-odds, because the absolute upside is large. This is a *separate* knob from `min_amount_cents`
  (a "big enough to fight" cutoff, not a skip floor). Default `20000` = $200. Only affects the digest's
  cheap heuristic, never the full evaluation.
- `min_winnability` — the win-probability floor (0..1) the company is willing to fight. Below it, both
  the digest and the full evaluation recommend **ACCEPT**. `0.0` (default) = no floor (consider/fight
  everything with a case); `0.5` = don't fight worse-than-coin-flip. Note: even strong cases cap around
  ~60% (Stripe), so a floor above ~0.6 will reject almost everything.

### `sources.<role>`
Set a role to `null` to disable it (e.g. an org with no internal chat). Common fields:
- `connector` — the Airbyte Agent SDK connector slug.
- `entity` — the Context Store entity to search (e.g. `tickets`, `files`, `messages`).
- Role-specific match fields:
  - `customer_comms.match_field` — the field holding the customer's email (e.g. `requester_email`,
    `email`, `contact_email`). Optional `comments_entity` for ticket replies.
  - `policies.name_field` + `keywords` — title field and the policy keywords to match.
  - `internal_chat.text_field` — the message body field to fuzzy-search.
  - `service_usage.match_field` — the customer identifier on usage records (`email`, `user_id`,
    `external_id`, `external_customer_id`). Defaults to `email`. This role is **off** unless you add
    it to config; if the tool keys records by an internal id rather than email, set `match_field`
    accordingly (you may need that id available on the Stripe customer).
    - **amplitude** — event-level product usage; `entity: events`, match on `email`/`user_id`.
    - **orb** — usage-based billing; the Agent connector exposes `customers`/`subscriptions`/
      `invoices` (not raw events). Use `entity: subscriptions` (an active subscription + metered
      invoices is strong evidence for `subscription_canceled`), matching on `external_customer_id`
      or `email`.
  - `order_data.match_field` — the field on the orders entity to match the customer by. Defaults to
    `email`. This role is **off** unless configured. Connector notes:
    - **shopify** — `entity: orders`; rich data (line items, shipping address, `fulfillments` with
      tracking, `order_refunds`). Match on `email`.
    - **woocommerce** — `entity: orders` (read-only); email lives at `billing.email`, so set
      `match_field: billing.email`. Has `orders`, `order_notes`, `refunds`, shipping methods/zones.
    - **amazon-seller-partner** — `entity: orders` (+ `order_items`). The buyer email is frequently
      **masked/anonymized**, so matching by email often fails — match by the Amazon order id (e.g.
      stored in Stripe charge metadata) via `match_field` instead.

> The three roles above have built-in query shapes in `fetch_dispute_context.py`. Pointing a role at
> a different tool is just connector + field-name changes. Adding a brand-new role means adding a
> small query builder there — but the four roles cover the standard dispute workflow.

### `digest`
- `connector`, `channel` — where to post (e.g. `slack` + `#disputes`).
- `post_time`, `timezone` — informational defaults for whoever sets up the schedule.
- `urgency_hours.red` / `.orange` — deadline thresholds (in hours) for the 🔴/🟠/🟢 markers.

### `storage` (where packages are saved + self-heal) — local only
- `local_dir` — the single local folder that holds everything: the package folders
  (`pending/dp_<id>/`, then `won/` or `lost/`), the self-heal knowledge base `learnings.md`,
  `pitfalls.md`, `index.json` (the ledger), and `digest_state.json` (the daily digest's "last posted"
  watermark, used to show only new disputes). Default `~/dispute-fighter-data`.

How self-heal works: each generated package is registered `pending` in the ledger. The daily run
(`scripts/review_outcomes.py`) checks resolved disputes against Stripe, appends an anonymized lesson
to `learnings.md` (won) or `pitfalls.md` (lost), then moves the package to `won/`/`lost/` and marks it
processed. Step 2 of the main workflow reads those files back, so outcomes improve future packages.
The ledger guarantees no dispute is processed twice.

### `winnability_overrides`
Map of Stripe `reason` code → prior (0..1), overriding the defaults in
[stripe-disputes.md](stripe-disputes.md). Use your own historical win rates here. Affects only the
digest's cheap "quick lean" hint, never the full evaluation.

## What a receiving org must provide

1. An **Airbyte workspace** with the connectors for their chosen roles enabled, reached either via the
   **Agent MCP** (`https://mcp.airbyte.ai/mcp`, browser OAuth — no install, no terminal credentials;
   preferred) or the **Agent SDK** (`pip install airbyte-agent-sdk` + `AIRBYTE_CLIENT_ID`/`SECRET` env
   vars; headless/cron fallback). See [airbyte-data-access.md](airbyte-data-access.md) for both modes.
2. A **Stripe** connection (pinned) for disputes.
3. A `config.yaml` mapping roles to their connectors and field names.
4. For direct digest posting: `SLACK_BOT_TOKEN` (or the connected Slack tool) — otherwise the skill
   prints the digest for a human/agent to post.

## Sharing the skill

Package the folder into a `.skill` file (e.g. with the skill-creator's `package_skill.py`) and hand
it over. **Do not ship a real `config.yaml` with secrets** — ship `config.example.yaml` only.
Connector credentials live in the recipient's own Airbyte workspace, never in the skill.
