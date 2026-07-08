# Airbyte Data Access — two modes

The skill reaches Stripe, Zendesk, Google Drive, Slack, etc. through Airbyte. There are **two ways**,
and the skill works the same downstream because both produce the same JSON files for the local scripts
to process.

## Mode A — Agent MCP (preferred: no SDK install, no credentials in a terminal or chat)

Airbyte exposes its agent capabilities as a hosted **MCP server**. When it's connected to the Claude
client, **you (the agent) call its tools directly** to add/query connectors — there is nothing to
`pip install` and no secrets to paste anywhere.

- **Connect it once** (browser OAuth, not terminal env vars):
  - Claude Desktop: Settings → Connectors → "Add custom connector" → `https://mcp.airbyte.ai/mcp`
  - Claude Code: `claude mcp add --transport http airbyte-agent https://mcp.airbyte.ai/mcp`
  - VS Code: Command Palette → "MCP: Add Server" (HTTP) → same URL
  - First connection triggers **OAuth 2.0**; connecting each data source opens a **secure browser URL**
    for that source's OAuth/credentials. No credentials are ever entered into chat.
- **Tool names are discovered automatically** — Airbyte's MCP advertises its tools to the agent; you
  don't hardcode names. Drive the data access with intent, e.g. "get dispute dp_123 and its charge,
  customer, and refunds from Stripe," "find Zendesk tickets for <email>," "search Drive for the refund
  policy," then **write the results into the JSON files the scripts expect** (see "The file handoff").
- A subprocess (`python foo.py`) **cannot** call MCP tools — MCP is available to you, the agent, not to
  a shell child process. So in MCP mode you do the fetching and hand files to the scripts; you do not
  run `fetch_dispute_context.py`.

### The file handoff (what to produce in MCP mode)
- For a single dispute → write **`dispute_context.json`** with the same shape `fetch_dispute_context.py`
  would have produced: top-level `dispute`, `charge`, `customer`, optional `invoices`/`subscriptions`/
  `refunds`, and the role keys `customer_comms`, `policies`, `internal_chat`, `service_usage`,
  `order_data` (omit what you don't have). Then continue at Step 2.
- For the daily digest → write a disputes dump (`{"disputes": [...]}` with each dispute's `status`,
  `reason`, `amount`, `currency`, `evidence_details.due_by`, and `_customer_name`) and run
  `daily_dispute_digest.py --from-file <dump>.json`.
- For outcome review → write `{dispute_id: "won"|"lost"|"open"}` from Stripe and run
  `review_outcomes.py --scan --outcomes-file <that>.json`.
Package storage is **local** in both modes (`save_package.py` writes to `storage.local_dir`), so
there's nothing to upload — the file handoff above is only about *reading* source data via MCP.

Everything else (`build_evidence_package.py`, the digest formatting, the ledger, won/lost moves,
field validation, package storage) is pure local Python and runs identically in both modes.

## Mode B — Agent SDK (scripts call Airbyte directly)

Use this when MCP isn't available (e.g. headless/cron with no connector). The scripts import the SDK
and authenticate from environment variables. This is the original path and requires the install +
credentials below. Patterns are from `docs.airbyte.com/ai-agents`; the SDK evolves, so if an import or
method fails, confirm with `python -c "import airbyte_agent_sdk as a; print(dir(a))"`.

## Install & auth

```bash
uv pip install airbyte-agent-sdk      # or: uv add airbyte-agent-sdk   (Python >= 3.10)
```

Auth is via environment variables the SDK reads automatically:

```bash
export AIRBYTE_CLIENT_ID=...
export AIRBYTE_CLIENT_SECRET=...
```

## Core pattern

The primary documented interface is `connect("<source>")`, returning a connection you query two ways:

- **`context_store_search(...)`** — search Airbyte's pre-indexed Context Store replica. Preferred:
  fast, cached, fewer tokens. Use for reads of entities the store carries.
- **`.execute(entity, action, params=...)`** — call the live source API. Use for entities not in
  the store, for fresh reads, or when you need a specific record by id.

```python
import asyncio
from airbyte_agent_sdk import connect

async def main():
    stripe = connect("stripe")
    # Discover what's queryable:
    print(stripe.list_entities())

    # Live read by id:
    dispute = await stripe.execute("disputes", "get", params={"id": "dp_123"})

    # Context Store search with structured filters:
    tickets = await zendesk.context_store_search(
        query={"filter": {"eq": {"requester_email": "buyer@example.com"}}},
        limit=50,
    )
    await stripe.close()

asyncio.run(main())
```

### `context_store_search` query syntax

```
context_store_search(query, limit=1000, cursor=None, fields=None)
```
- `query` — dict with `filter` (and optional `sort`). Operators: `eq, neq, gt, gte, lt, lte, in,
  like, fuzzy, keyword, not, and, or`. Combine with `and`/`or`, e.g.
  `{"filter": {"and": [{"eq": {"status": "open"}}, {"gte": {"created_at": "2026-01-01"}}]}}`.
- `limit` — default 1000.
- `cursor` — pass `meta.cursor` from a prior response to paginate.
- `fields` — field selection, e.g. `[["id"], ["requester", "email"]]`.

> The exact entity/action names per connector live in each connector's REFERENCE page
> (`docs.airbyte.com/ai-agents/connectors/<source>/REFERENCE`). Treat the names below as the
> expected shape and verify against `list_entities()` at runtime.

## What to pull per source

The connector slug for each role (`customer_comms`, `policies`, `internal_chat`, `digest`) comes from
`config.yaml` — see [configuration.md](configuration.md). Stripe is pinned. The examples below use the
default connectors; an org may have swapped them (e.g. Intercom for Zendesk), but the *role* and the
fields the skill needs are the same.

### Stripe (`connect("stripe")`, pinned)
- `disputes` — the dispute: `reason`, `amount`, `currency`, `status`, `evidence`,
  `evidence_details.due_by`, `charge`/`payment_intent`.
- `charges` — `outcome` (risk_level, network_status), `payment_method_details`, AVS/CVC checks,
  `billing_details`, receipt url.
- `payment_intents`, `customers`, `invoices`, `subscriptions`, `products`/line items, `refunds`.
- Goal: reconstruct the full transaction story and the risk signals.

### Role `customer_comms` — default Zendesk (`connect("zendesk-support")`)
- `tickets`, `ticket_comments` (or `comments`), `users`.
- Filter by the customer email / name from Stripe. Look for: prior contact, refund offers/requests,
  cancellation requests, delivery complaints, and *absence* of contact (friendly-fraud signal).

### Role `policies` — default Google Drive (`connect("google-drive")`)
- `files` — search internal policy docs: refund policy, cancellation policy, Terms of Service,
  shipping/SLA. Match by product/plan where possible. Capture the doc text/snippet and a link.

### Role `internal_chat` — default Slack (`connect("slack")`)
- `messages` / `channels` — internal discussion referencing the customer, order id, or dispute.
  Useful for context and for any prior decision (e.g. "we already refunded this").

### Role `service_usage` — optional, off unless configured
Available Agent connectors for this role:
- **Amplitude** (`connect("amplitude")`) — `events`: logins, sessions, feature use, downloads for the
  customer. Match on the configured identifier (`email`/`user_id`).
- **Orb** (`connect("orb")`) — usage-based billing. Entities: `customers`, `subscriptions`, `plans`,
  `invoices` (no raw event stream). Pull the customer's `subscriptions` (status, start/end) and
  `invoices` (metered line items): an active subscription with ongoing metered invoices proves the
  account was live and consuming the service.

Summarize whatever you get into a dated activity log for the `access_activity_log` evidence field —
activity after the purchase/cancellation date is the point. This is decisive for
`subscription_canceled` (use/billing continued past the claimed cancel) and strengthens `fraudulent`/
`unrecognized` (the legitimate account was active).

### Role `order_data` — optional, off unless configured (e.g. `connect("shopify")`)
The order behind the disputed charge. Match on the configured identifier (default `email`).
- **Shopify** (`connect("shopify")`) — `orders` (line items, shipping address the customer
  provided), `fulfillments` (carrier + tracking + ship date), `order_refunds`, `transactions`.
- **WooCommerce** (`connect("woocommerce")`) — `orders` (read-only), `order_notes`, `refunds`,
  shipping methods/zones. Email is at `billing.email`.
- **Amazon Seller Partner** (`connect("amazon-seller-partner")`) — `orders`, `order_items`,
  financial events. Buyer email is often masked → match by the Amazon order id instead.

Use this to fill `product_description` (what was actually ordered), the `shipping_*` fields
(carrier/tracking/date and the **full** delivery address), and `receipt`. It's the backbone of a
`product_not_received` rebuttal and reveals gift ship-to mismatches (a "ship to" name differing from
the cardholder — be ready to explain it, per Stripe).

## Output contract

`fetch_dispute_context.py` consolidates all of the above into one `dispute_context.json` (see the
script for the exact shape). Downstream steps read only that file, so they work identically whether
the data came from live sources or a saved dump.
