# Dispute Fighter

A Claude skill that helps you handle Stripe credit card disputes (chargebacks). It does four things:

1. **Evaluate** — decides whether a dispute is worth fighting, with honest reasoning (a well-argued
   "accept the loss" is a valid answer). You can set a minimum dollar threshold so small disputes are
   skipped, a minimum win-probability floor (e.g. don't fight below 50%), and evaluate several disputes
   in one request.
2. **Build** — assembles a ready-to-submit evidence package: a Stripe `dispute.evidence` JSON plus a
   human-readable rebuttal document.
3. **Daily digest** — posts a table of disputes needing a response to your chat tool (Slack). It's
   incremental — only disputes new since the last post — but re-surfaces any still-open dispute that has
   entered its "due soon" window, so nothing slips past its deadline.
4. **Self-heals from outcomes** — saves each generated package locally, and on the daily run checks
   which disputes closed won/lost, distills anonymized lessons into `learnings.md` / `pitfalls.md`, and
   feeds them back into future evaluations.

This skill pulls the following through **Airbyte** (via the Agent MCP or the Agent SDK — see Prerequisites):

* Dispute and payment records
* Customer communications
* Your policy docs
* Internal discussions about the customer
* (Optionally) e-commerce order data — line items, shipping/fulfillment, tracking
* (Optionally) product-usage data

It then maps this information into the evidence fields for each dispute reason code for submission to card networks.

> [!IMPORTANT]
> **Stripe is required and fixed** as the disputes system of record. Every other source is
> configurable, so you can run this with your own stack as long as Airbyte Agents includes the source as a connector.

## Prerequisites

An **Airbyte Agents workspace** with the connectors you need enabled (Stripe required; plus your
support / docs / chat / analytics / store tools). You connect to it one of two ways:

- **Preferred — Airbyte Agent MCP (no install, no terminal credentials).** Add the connector once in
  your client and sign in with **browser OAuth**, then authorize the sources you use:
  - Claude Desktop: Settings → Connectors → "Add custom connector" → `https://mcp.airbyte.ai/mcp`
  - Claude Code: `claude mcp add --transport http airbyte-agent https://mcp.airbyte.ai/mcp`
  - Nothing to `pip install`; **no secrets entered into a terminal or into chat.**
- **Fallback — Agent SDK (headless/cron).** `pip install airbyte-agent-sdk` (Python 3.10+) and set
  `AIRBYTE_CLIENT_ID` / `AIRBYTE_CLIENT_SECRET` in the environment.

Optional: `pip install pyyaml` only if you write config as YAML instead of JSON; a `SLACK_BOT_TOKEN`
only if you want the digest to post itself (otherwise Claude posts it via a connected Slack tool).

## Setup

**Easiest — just ask Claude.** Install the skill, then say *"set up dispute-fighter"* (or *"configure
my dispute sources"*). Claude walks you through which tool you use for each role in plain conversation
and writes the config file for you — no YAML, no command line. You just connect Airbyte once via the
Agent MCP (browser OAuth, see Prerequisites) — no credentials typed anywhere.

**On Claude.ai / Desktop, use a Project so your config persists with no reinstall.** The Chat
surface can't keep a `config.json` between sessions, so Claude will emit a config block (`python
scripts/configure.py --emit config.json`) for you to paste into your **Project's custom instructions**
(or a Project knowledge file). Each session the skill reads that block and rebuilds a scratch config
from it — change settings by editing the block, never reinstalling.

**On Claude Code / a writable install**, Claude can instead **repackage the skill with your config
baked in** (`python scripts/package_self.py`) to produce a pre-configured `dispute-fighter.skill` to
reinstall or share. This is optional — if the installed folder is writable, the `config.json` Claude
wrote is already active. Either way the config is safe to store/share: it holds no secrets (credentials
live in MCP OAuth / env vars), and the packager refuses to bundle anything secret-like.

Prefer to edit by hand? You still can:
1. Install the skill (place this folder where your Claude client loads skills, or install the `.skill` file).
2. Copy an example — `cp config.example.json config.json` (zero deps) or `cp config.example.yaml config.yaml` (needs PyYAML).
3. Point each **role** at your tools (see the table below). Keep Stripe as-is.
4. Connect Airbyte per Prerequisites (Agent MCP via browser OAuth, or the SDK with env vars).

With no config file present, the skill falls back to Zendesk, Google Drive, and Slack as 
defaults.

## Roles you configure

Every role is filled by an **Airbyte Agent connector** ([catalog](https://docs.airbyte.com/ai-agents/connectors)) —
not a classic ELT connector. The alternatives below are all verified Agent connectors.

| Role | Default | What it provides | Swap for (Agent connectors) |
|---|---|---|---|
| `stripe` *(pinned)* | Stripe | disputes + payment/risk records | — |
| `customer_comms` | zendesk-support | customer contact, refund/cancellation requests | Intercom, Freshdesk, Salesforce, HubSpot |
| `policies` | google-drive | refund / cancellation / ToS / shipping docs | Notion, Confluence, Airtable |
| `internal_chat` | slack | internal discussion about the order | *(Slack only today)* |
| `service_usage` *(optional, off)* | — | proof the customer used the service | Amplitude (event usage), Orb (usage billing) |
| `order_data` *(optional, off)* | — | order line items, shipping/fulfillment/tracking | Shopify, WooCommerce, Amazon Seller Partner |
| `digest` target | slack | where the daily digest posts | *(Slack; Teams/email need a non-Agent tool)* |

Full field-by-field reference: [`references/configuration.md`](references/configuration.md).

## How to use

### In conversation with Claude

Start a conversation with Claude with the skill installed. 
The following examples model the kinds of questions this skill can answer.

- "Is dispute dp_123 worth fighting? Build the evidence if so."
- "A customer disputed dp_456 saying they cancelled. Do we have a case?"
- "Evaluate dp_123, dp_456, and dp_789 — which are worth fighting?" (multiple at once)
- "Post today's digest of disputes that need a response."

### Run the scripts

You can run the scripts directly in a terminal. The data-fetching commands (`fetch_dispute_context.py`,
and `daily_dispute_digest.py` / `review_outcomes.py` without a `--from-file`/`--outcomes-file`) use the
**SDK mode**; in **MCP mode** Claude fetches the data and feeds these scripts files instead.

```bash
# Set up your sources without editing YAML
python scripts/configure.py --list                       # show roles + available connectors
python scripts/configure.py --scaffold config.json       # write a starter config
python scripts/configure.py --validate config.json       # check a config
python scripts/configure.py --emit config.json           # print a config block to paste into a Claude Project

# Gather everything for one dispute into dispute_context.json (SDK mode)
python scripts/fetch_dispute_context.py --dispute dp_123

# Build the Stripe evidence JSON + readable doc from an authored mapping
python scripts/build_evidence_package.py evidence_input.json --outdir ./out

# Save a generated package + register it for outcome tracking (local storage)
python scripts/save_package.py --dispute dp_123 --dir ./out --reason product_not_received --amount 129.00 --currency USD

# Daily digest (incremental: only disputes new since the last post, plus due-soon reminders)
python scripts/daily_dispute_digest.py            # print the table message
python scripts/daily_dispute_digest.py --all      # include every open dispute, not just new ones
python scripts/daily_dispute_digest.py --post     # post via Slack (needs token + channel); advances the watermark
python scripts/daily_dispute_digest.py --mark-posted   # advance the watermark after posting via a Slack tool

# Self-heal: review resolved disputes, then move them to won/ or lost/
python scripts/review_outcomes.py --scan
python scripts/review_outcomes.py --finalize dp_123 won
```

To run the digest **daily**, schedule it with your Claude client's scheduling feature (a cron-style
task) with a prompt like *"Run the dispute-fighter daily digest and post it to our disputes channel."*
The config includes defaults of 9:00 AM and a dedicated channel. Adjust these to the time and channel of
your choosing. The disputes post in a native Slack **table** block (Dispute · Amount · Reason · Due ·
Lean · Customer) under a header + summary, with a plain-text fallback for notifications. To work one,
copy its dispute id and paste `evaluate <id>` into Claude.

**Windows, macOS, and Linux are all supported.** The Claude client's scheduler runs in the cloud, so
it's OS-independent. If you'd rather schedule the script on your own machine, use **cron** (macOS/Linux)
or **Task Scheduler** (Windows) — the scripts are pure Python 3.10+ with no UNIX-only dependencies.

## Important guardrails

This skill includes the following safety measures:

- **Never auto-submits:** Disputes are effectively one-shot and irreversible, so the skill always
  stops for a human to review and submit. Treat its recommendations as decision support.
- **No invented evidence:** If a source or credential is missing, the skill says so rather than
  fabricating data.
- **No secrets travel with the skill:** Only `config.example.*` is shipped. Your connector
  credentials live in your Airbyte workspace. Don't commit a real `config.yaml` with secrets.

## What's included

```
SKILL.md                          # the skill itself (instructions Claude follows)
config.example.yaml / .json       # copy one to config.yaml/.json and edit
references/
  stripe-disputes.md              # reason-code → winnability + evidence playbook (exact Stripe fields)
  evaluation.md                   # the "worth fighting?" rubric
  airbyte-data-access.md          # MCP + SDK access modes and what's pulled per role
  configuration.md                # field-by-field config guide
scripts/
  config.py                       # config loader (roles, defaults, validation)
  configure.py                    # guided-setup helpers (--list / --scaffold / --validate / --emit)
  package_self.py                 # repackage the skill + config.json into a .skill to reinstall/share
  fetch_dispute_context.py        # gather all context for a dispute (SDK mode)
  build_evidence_package.py       # render Stripe evidence.json + readable doc
  storage.py                      # local package storage + ledger + learnings/pitfalls
  save_package.py                 # save a package and register it for outcome tracking
  daily_dispute_digest.py         # build/post the daily triage digest
  review_outcomes.py              # self-heal: scan resolved disputes, move to won/lost
assets/evidence_package_template.md

# Created at runtime in storage.local_dir (~/dispute-fighter-data), not shipped:
#   pending/ · won/ · lost/  (package folders)   index.json (ledger)
#   learnings.md · pitfalls.md  (self-heal knowledge base)   digest_state.json (last-posted watermark)
```
