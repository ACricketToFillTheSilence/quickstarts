---
name: dispute-fighter
description: >-
  Evaluate whether a Stripe credit card dispute (chargeback) is worth fighting, and assemble a
  ready-to-submit evidence package to fight it. Pulls live context from Stripe plus your configured
  support, docs, chat, analytics, and order-data sources through Airbyte (Agent MCP or SDK), maps it to
  Stripe's dispute evidence fields, and produces both a structured JSON payload and a human-readable
  rebuttal document. Also posts a daily triage digest to your team chat. Use whenever a user mentions a
  dispute, chargeback, "representment," a Stripe dispute ID (dp_...), a disputed charge (ch_/pi_), asks
  whether a dispute is winnable or worth contesting, needs to gather or write up evidence for a
  chargeback, asks to respond to a card network / issuer dispute, wants a daily/recurring summary of
  disputes posted to Slack, or asks to set up / configure the skill or choose which data sources to
  connect — even if they don't say "Airbyte" or "evidence package."
  Always stop for human review before anything is submitted to Stripe.
---

# Dispute Fighter

Help a merchant decide **whether to fight a Stripe dispute** and, when it's worth it, **build the
evidence package** that gives the best shot at winning. The skill has three capabilities; the first
two usually run back-to-back, but each can run alone:

1. **Evaluate** — is this dispute worth fighting? Produce a clear recommendation with reasoning.
2. **Build** — assemble the evidence package (Stripe `evidence` JSON + a readable rebuttal doc).
3. **Daily digest** — post a triage summary of all open disputes needing a response to Slack, sorted
   by urgency, so nothing slips past its deadline. See [Daily digest](#daily-digest-triage-to-slack).

The hard part of winning a chargeback is matching the *right* evidence to the *specific reason code*
the issuer cited, and being honest about when a dispute is unwinnable so the merchant doesn't waste
the dispute fee. This skill encodes that judgment.

## Guardrail: never auto-submit

Disputes involve real money and are effectively irreversible once submitted — you usually get **one**
submission per dispute. Always present the recommendation and the package for a human to review.
Do **not** call any Stripe write/submit API on your own. If the user explicitly asks you to submit
after reviewing, confirm once more, then proceed only if they have a Stripe write tool available.

## Setup: data access via Airbyte (two modes)

All source data is reached through Airbyte. There are two access modes — read
[references/airbyte-data-access.md](references/airbyte-data-access.md) for the details. Both feed the
same JSON files to the local scripts, so the rest of the workflow is identical either way.

- **Mode A — Agent MCP (preferred).** The Airbyte MCP server (`https://mcp.airbyte.ai/mcp`) is
  connected to the client once via **browser OAuth** — no `pip install`, and **no credentials typed
  into a terminal or into chat**. In this mode *you* (the agent) call the connected Airbyte MCP tools
  to fetch data, then write the results to the JSON files the scripts expect (`dispute_context.json`,
  a disputes dump, an outcomes map). A shell subprocess can't reach MCP, so don't run
  `fetch_dispute_context.py` in this mode — gather via MCP and hand files to the other scripts.
- **Mode B — Agent SDK (fallback).** For headless/cron use with no connector. The scripts import
  `airbyte-agent-sdk` and read `AIRBYTE_CLIENT_ID` / `AIRBYTE_CLIENT_SECRET` from the environment.

Prefer Mode A whenever the Airbyte MCP is connected (you'll see its tools available). Fall back to
Mode B only if it isn't. Either way: never invent data — if you can't reach a source, say so.

### Configurable sources (portable across organizations)

This skill is built to be shared. **Stripe is pinned as the disputes system of record**, but the
supporting sources are defined by *role*, and each role points at whatever connector an org uses:

All roles are filled by **Airbyte Agent connectors** (the `airbyte-agent-sdk` catalog,
<https://docs.airbyte.com/ai-agents/connectors>) — not classic ELT connectors.

| Role | Default connector | What it provides | Swap for (Agent connectors) |
|---|---|---|---|
| `customer_comms` | zendesk-support | customer contact, refund offers, cancellation requests | Intercom, Freshdesk, Salesforce, HubSpot |
| `policies` | google-drive | refund/cancellation/ToS/shipping policy docs | Notion, Confluence, Airtable |
| `internal_chat` | slack | internal discussion about the customer/order | *(Slack only today)* |
| `service_usage` *(optional)* | — (off) | proof the customer used the service → `access_activity_log` | Amplitude (event usage), Orb (usage billing) |
| `order_data` *(optional)* | — (off) | order line items, shipping/fulfillment/tracking, provided address | Shopify, WooCommerce, Amazon Seller Partner |
| `digest` target | slack | where the daily digest is posted | *(Slack; Teams/email need a non-Agent tool)* |

The mapping lives in a config file at the skill root (loaded by `scripts/config.py`). If no config is
present, the defaults above apply. Code and the fetched-context JSON are keyed by **role**, never by
tool name, so the evaluation and evidence-building logic is identical no matter which tools are
configured.

**Most users should not hand-edit config files — use Guided setup below instead.**

### Guided setup (configure by conversation, no file editing)

When a user asks to set up or configure the skill, or to change which tools they connect, **run the
setup as a conversation and write the config file for them** — they should never need to touch YAML or
the command line. Steps:

1. Run `python scripts/configure.py --list` to see the roles and the Agent connectors available for
   each. Then ask the user, in plain language and one role at a time, which tool they use — offering
   the supported connectors as choices. Make it easy: Stripe is already handled; the optional roles
   (`internal_chat`, `service_usage`, `order_data`) can be skipped with a simple "we don't use one."
   Keep it short — accept the default `entity`/`match_field` unless the user has a reason to change it,
   and only ask follow-ups (e.g. WooCommerce's `billing.email`, Amazon's order-id matching) when the
   chosen connector needs them (see [references/configuration.md](references/configuration.md)).
2. Also confirm the **digest channel**, the **dispute fee** (`stripe.dispute_fee_cents`) if relevant,
   a **minimum dispute amount worth evaluating** (`evaluation.min_amount_cents`) — e.g. "$500"
   becomes `50000`; below it, disputes are marked SKIP; default 0 = evaluate everything — and a
   **minimum winnability** (`evaluation.min_winnability`, 0..1) — the win-probability floor the company
   will fight below which it accepts; default 0 = no floor, and note anything above ~0.6 fights almost
   nothing (Stripe's realistic ceiling). Generated
   packages and the self-heal knowledge base save **locally** to `storage.local_dir` (default
   `~/dispute-fighter-data`) — only ask about this if they want a different local path.
3. Persist their answers. **Pick the path that fits the surface:**
   - **Chat / Claude.ai with a Project (persists, no reinstall):** build the config, then run
     `python scripts/configure.py --emit config.json` and give the user the emitted block to paste
     into their **Project's custom instructions** (or save as a Project knowledge file). That block is
     the durable source of truth; each session the skill rebuilds `./config.json` from it (see "Config
     in a Project"). Skip the repackage step below — it isn't needed here.
   - **Claude Code / SDK (writable install):** write the answers to **`config.json`** at the skill root
     with the file-writing tool. Best practice: start from `python scripts/configure.py --scaffold
     config.json` (defaults with every optional role disabled) and edit — configs merge over defaults,
     so to *disable* an optional role set it to `null`, don't just omit it.
4. Validate with `python scripts/configure.py --validate config.json` and read back the configured
   sources to the user for confirmation. Fix anything it flags.
5. Explain how they actually connect to Airbyte — recommend the **no-terminal** path:
   - **Preferred (Agent MCP):** connect the Airbyte MCP once in their client (Claude Desktop:
     Settings → Connectors → Add custom connector → `https://mcp.airbyte.ai/mcp`; Claude Code:
     `claude mcp add --transport http airbyte-agent https://mcp.airbyte.ai/mcp`). Auth is **browser
     OAuth**, and each data source is authorized via a secure browser URL — **no `pip install`, and no
     credentials entered into a terminal or into chat.** Then they authorize the connectors they chose.
   - **Fallback (SDK):** only for headless/cron use — `pip install airbyte-agent-sdk` and set
     `AIRBYTE_CLIENT_ID` / `AIRBYTE_CLIENT_SECRET` in the environment.
   Offer to walk them through whichever they pick.
6. **(Code/SDK installs only — skip on Chat/Project.) Repackage with the config to reinstall or share.**
   Once the config is written and validated, run `python scripts/package_self.py` — it bundles the
   skill *plus* `config.json` into a
   fresh `dispute-fighter.skill` (refusing if the config somehow contains secret-like keys) and prints
   reinstall instructions. Relay those to the user: the output `.skill` path, and how to reinstall
   (Claude Code: `unzip -o <path> -d ~/.claude/skills/`; Claude Desktop: re-upload it under
   Settings → Capabilities → Skills). **Be clear that reinstalling is only needed to share the
   pre-configured skill or when the install folder is read-only — if `config.json` was written into an
   already-installed, writable skill folder, the skill uses it immediately with no reinstall.**

Manual editing still works for power users — copy [config.example.yaml](config.example.yaml) or
[config.example.json](config.example.json) and edit — but the guided flow above is the recommended
path. Full field reference: [references/configuration.md](references/configuration.md).

### Config in a Project (Claude.ai / Desktop — persists with no reinstall)

On the Chat surface the installed skill is **read-only at runtime** and the sandbox filesystem is
wiped between sessions, so a `config.json` written mid-chat does **not** persist. Instead, keep the
config in the **Project** (which does persist) and rebuild the scratch file each session.

**Config resolution — at the start of every run, load config in this order:**
1. A **Project config block** — look in the Project's custom instructions (and knowledge files) for a
   block between the marker lines `=== DISPUTE-FIGHTER CONFIG (do not remove) ===` and
   `=== END DISPUTE-FIGHTER CONFIG ===`. If present, parse its JSON and **write it to `./config.json`**
   in the working dir so the scripts can read it this session (it's scratch — the Project is the
   source of truth).
2. Otherwise a writable **`config.json`** already next to the skill (Code/SDK installs).
3. Otherwise built-in **defaults**.

To change settings, the user edits the block in the Project — no reinstall, no repackaging. `config.json`
holds no secrets, so it's safe to keep in the Project; credentials still come from MCP OAuth / env vars.

**Testing / no-credentials mode:** if you're handed a context JSON file (a saved dump of the
dispute and its related records) instead of live access, skip the fetch step and work from that
file. The evaluation and package-building logic is identical.

## Workflow

### Step 0 — Identify the dispute(s)

You need a Stripe dispute id (`dp_...`) or the disputed charge / payment intent (`ch_...` / `pi_...`).
If the user gives a customer name, order number, or Zendesk ticket instead, resolve it to a dispute
first. If more than one dispute matches an ambiguous reference, list them and ask which one.

**Multiple disputes at once.** The user can hand you several ids in one request (e.g. "evaluate
dp_1, dp_2, dp_3" — from the digest, a list, or a paste). When they do, run the **full workflow
(Steps 1–4) independently for each dispute**, then present a **consolidated summary** — one row per
dispute (id, amount, reason, verdict FIGHT/ACCEPT/SKIP, deadline, package location) — followed by the
individual recommendations/packages. Keep going through the whole list even if some are ACCEPT/SKIP;
don't stop after the first. Apply the amount threshold (Step 2) to each, and still stop for human
review at the end — never auto-submit any of them.

### Step 1 — Gather context

Produce a single `dispute_context.json` keyed by role. How you produce it depends on the access mode:

- **MCP mode (preferred):** use the connected Airbyte MCP tools to fetch the records below, then write
  them into `dispute_context.json` yourself (shape per
  [references/airbyte-data-access.md](references/airbyte-data-access.md) → "The file handoff"). Don't
  run the fetch script — a subprocess can't reach MCP.
- **SDK mode (fallback):** run [scripts/fetch_dispute_context.py](scripts/fetch_dispute_context.py)
  with the dispute id; it reads the config, pulls everything via the SDK, and writes the same file.

Either way, gather:

- **Stripe** (pinned) — the dispute (reason, amount, status, `evidence_details.due_by`, current
  evidence), the charge, payment intent, customer, invoice/subscription, line items/product, refunds,
  and the charge's `outcome` / risk signals (AVS, CVC, IP, risk level).
- **`customer_comms`** — tickets/comments tied to the customer email (proof of contact, refund
  offers, cancellation requests, delivery complaints). Default Zendesk; configurable.
- **`policies`** — the relevant internal policy docs (refund, cancellation, ToS, shipping/SLA).
  Default Google Drive; configurable.
- **`internal_chat`** — internal discussion about this customer or order, if any. Default Slack;
  configurable.
- **`service_usage`** *(optional)* — product/usage events for the customer (logins, feature use,
  downloads). Off unless configured. When present, it's high-value evidence: it populates Stripe's
  `access_activity_log` and is often decisive for `subscription_canceled` and `fraudulent`/
  `unrecognized` disputes. Treat its absence as "no usage data," not "no usage."
- **`order_data`** *(optional)* — the order behind the charge: line items, the shipping address the
  customer provided, fulfillment status, carrier/tracking. Off unless configured. Feeds
  `product_description`, `shipping_address`/`shipping_carrier`/`shipping_tracking_number`, and
  `receipt`; essential for `product_not_received` / `product_unacceptable` and for spotting a
  gift ship-to mismatch. Default Shopify; also WooCommerce or Amazon Seller Partner.

If the script can't run (no creds in this environment), gather the same fields yourself using the
patterns in the data-access reference, or accept a pre-built `dispute_context.json`.

### Step 2 — Evaluate whether to fight

**First, learn from past outcomes (self-heal).** Read `learnings.md` and `pitfalls.md` from the
storage `local_dir` (default `~/dispute-fighter-data/`) if they exist — they capture, per reason
code, what won and what lost previous disputes. Apply the relevant lessons to this evaluation and to
how you'll build the package. These files are maintained automatically by the daily outcome review
(see "Outcome review & self-heal" below), so the skill gets sharper over time.

**Amount threshold (check first).** If `evaluation.min_amount_cents` is set in config and this
dispute's amount is below it, the user has decided it's not worth the effort. Recommend **SKIP** with a
one-line reason (e.g. "$24 is below your $500 evaluate threshold — accept it") and don't build a
package — *unless* the user explicitly asked to evaluate this specific dispute anyway, in which case
proceed but note it's below threshold. A threshold of 0 (default) means evaluate everything.

**Winnability floor (company policy).** After you estimate the win probability, compare it to
`evaluation.min_winnability` (0..1). If your estimate is **below** the floor, recommend **ACCEPT** —
the company has decided it won't fight odds that low — and state both numbers (e.g. "estimated ~40%
vs. your 50% floor"). A floor of 0.0 (default) imposes no policy limit; fight wherever there's a case.
The user can override for a specific dispute, but note it's below their floor.

Then read [references/evaluation.md](references/evaluation.md) for the full rubric, and assess:

- **Reason code & base winnability** — different reason codes have very different odds and require
  different evidence (see the reason-code table in
  [references/stripe-disputes.md](references/stripe-disputes.md)).
- **Evidence strength** — do we actually *have* the evidence this reason code needs (e.g. delivery
  confirmation for `product_not_received`, a disclosed refund policy for `subscription_canceled`)?
  Missing the keystone evidence usually means don't fight.
- **Economics** — disputed amount vs. the non-refundable dispute fee and the effort; small amounts
  with weak evidence are usually not worth contesting.
- **Risk / friendly-fraud signals** — AVS/CVC match, prior successful charges, product access logs,
  prior customer contact all strengthen a `fraudulent`/`unrecognized` rebuttal.
- **Deadline** — is there time left before `evidence_details.due_by`? If it's passed, you can't fight.
- **Visa CE 3.0** — for `fraudulent`/`unrecognized`, check whether the dispute is CE 3.0 eligible
  (Stripe badges it). If so, prior undisputed transactions with the same cardholder become the
  strongest evidence and should raise your assessment. See the disputes reference.
- **Realistic ceiling** — even strong cases top out around ~60% per Stripe; don't overstate odds.

Output a recommendation using the **Recommendation template** below. Be decisive and honest — a
well-reasoned "accept the loss" is a valid, valuable answer.

### Step 3 — Build the evidence package (only if fighting)

Run [scripts/build_evidence_package.py](scripts/build_evidence_package.py) on the context JSON, or
assemble by hand. It produces two artifacts:

1. **`evidence.json`** — keyed by Stripe's exact `dispute.evidence` field names (verified list in
   [references/stripe-disputes.md](references/stripe-disputes.md)). Only include fields that are
   genuinely supported by gathered evidence. Text fields get the actual text; file fields get the
   path/Drive link to the supporting document plus a note that it must be uploaded as a Stripe File.
2. **`evidence_package.md`** — a human-readable rebuttal built from
   [assets/evidence_package_template.md](assets/evidence_package_template.md): the narrative
   `uncategorized_text` argument, the field-by-field evidence list, a checklist of files to upload,
   and any gaps the reviewer should be aware of.

Map evidence to fields according to the reason-code playbook in the disputes reference — for
example, a `product_not_received` rebuttal centers on `shipping_carrier` / `shipping_tracking_number`
/ `shipping_date` / `shipping_documentation`, while `duplicate` centers on `duplicate_charge_id` and
`duplicate_charge_explanation`.

Follow Stripe's submission best practices (see [stripe-disputes.md](references/stripe-disputes.md) →
"Submission best practices") — they're enforced/surfaced by the build script:
- **Concise, chronological narrative** in `uncategorized_text`; add a one-line "what this proves" per
  evidence item (pass `{"value": ..., "proves": ...}` for text, `{"file": ..., "proves": ...}` for files).
- **One file per evidence type** — if several items support one field, pass `{"files": [...]}` and the
  script flags them to be merged into a single upload.
- **Excerpt policies**, don't dump or link out; include a checkout-presentation screenshot for
  disclosure fields. The script warns if a policy file is an external URL.
- **Visa CE 3.0**: for `fraudulent`/`unrecognized`, if eligible, pass a `visa_ce3` block with the
  qualifying prior transactions. For an already-refunded charge, pass a `credit_issued` block.
- The script also emits Stripe's **file-format requirements** (PDF/JPEG/PNG, <4.5 MB, <50 pages,
  portrait, 12-pt+) into the package's upload checklist.

### Step 3.5 — Save the package

After building, save the package so the outcome loop can track it. Put the generated files
(`recommendation.md`, `evidence.json`, `evidence_package.md`) in one folder, then:

```
python scripts/save_package.py --dispute <dp_id> --dir <that folder> \
    --reason <reason_code> --amount <amount> --currency <CUR>
```

This copies the package into local storage (`storage.local_dir`, default `~/dispute-fighter-data/`,
under `pending/dp_<id>/`) and records the dispute in the local ledger as `pending`. Storage is local
in both access modes — the same folder holds `learnings.md`, `pitfalls.md`, and the ledger.

### Step 4 — Hand off for review

Summarize: the recommendation, the strongest evidence, the weakest points, what files still need to
be uploaded, and the `due_by` deadline. Note where the package was saved. Stop here for the human.

## Recommendation template

ALWAYS use this structure for the evaluation output:

```
# Dispute <dp_id> — Recommendation: FIGHT | ACCEPT | SKIP (below threshold)

**Reason code:** <code> — <plain-English meaning>
**Amount:** <amount currency>   **Dispute fee:** <fee>   **Respond by:** <due_by> (<days left>)

## Verdict
<One-paragraph call: fight or accept, and the core reason.>

## Why
- Winnability: <base odds for this reason code + how our evidence shifts them>
- Evidence we have: <bullets>
- Evidence we're missing: <bullets, or "none material">
- Economics: <amount vs. fee/effort>

## If fighting, the package will center on
<the key Stripe evidence fields for this reason code>
```

## Daily digest (triage to Slack)

Post a once-a-day summary of disputes that need a response, as a table, so the team sees what's on the
clock without opening Stripe. This is **lightweight triage, not full evaluation** — the point is to
surface and prioritize, then a human picks ones to work and runs the full Evaluate/Build flow.

**Incremental by default:** each run shows disputes **opened since the last time the digest was posted**
(tracked by a local `digest_state.json` watermark), so the channel isn't spammed with the same disputes
daily. **Deadline safeguard:** it *also* re-surfaces any still-open dispute that has entered the
"due soon" (red) window even if it was posted before — flagged as a **due-soon reminder** — so nothing
slips toward its deadline unseen. Pass `--all` for a full digest of every open dispute.

When to run: on a daily schedule (see "Scheduling" below) or whenever someone asks for a summary of
disputes needing attention. **The daily run also performs the outcome review & self-heal below.**

### Outcome review & self-heal (runs with the daily digest)

Closed disputes are how the skill learns. As part of every daily run, after (or before) posting the
digest:

1. **Scan for resolved disputes.** In **MCP mode**, look up the current Stripe status of each
   still-`pending` dispute in the ledger via the Airbyte MCP, write a `{dispute_id: status}` map, and
   run [scripts/review_outcomes.py](scripts/review_outcomes.py) `--scan --outcomes-file <map>.json`. In
   **SDK mode**, run `--scan` alone (it queries Stripe via the SDK). It prints a worklist of disputes
   now **won** or **lost**, and only returns still-pending ones, so nothing is reviewed twice.
2. **Record an anonymized lesson per resolved dispute.** For each item in the worklist, read its saved
   package and append one concise, **pattern-level, PII-free** bullet to the knowledge file:
   - **won →** `learnings.md` (what evidence/argument worked for that reason code)
   - **lost →** `pitfalls.md` (what was missing or unconvincing)

   Keep it general enough to help future disputes (e.g. *"`subscription_canceled`: wins needed the
   checkout cancellation-policy screenshot, not just the policy text"*). No customer names/emails.
3. **Finalize (move + mark).** Run `review_outcomes.py --finalize <dp_id> won|lost`. This moves the
   package into the `won/` or `lost/` folder and flips its ledger status, so it's never reviewed again.

These `learnings.md` / `pitfalls.md` files are read back in **Step 2** of the main workflow, closing
the loop: outcomes shape future recommendations and packages.

### How it works

1. **Pull open disputes.** In **MCP mode**, fetch *all* disputes with status `needs_response` /
   `warning_needs_response` (with each one's charge/customer for amount and reason) via the Airbyte MCP
   tools, write them to a dump, and run
   [scripts/daily_dispute_digest.py](scripts/daily_dispute_digest.py) `--from-file <dump>.json`. In
   **SDK mode**, just run the script (it fetches via the SDK). Fetch *all* actionable disputes (not
   only new ones) so the script can apply the incremental filter **and** the deadline safeguard: it
   keeps disputes created after the `last_posted_at` watermark (`digest_state.json`) **plus** any
   still-open dispute now within the "due soon" window (marked a *reminder*). Use `--all` to include
   every open dispute regardless. It then computes a per-dispute **quick lean** and **urgency**.
   - *Quick lean* (`FIGHT` / `ACCEPT` / `REVIEW` / `SKIP`) is a cheap heuristic from the reason code's
     base winnability (see [references/stripe-disputes.md](references/stripe-disputes.md)), amount, and
     the `min_winnability` floor — it deliberately does **not** gather evidence. `SKIP` means below
     `evaluation.min_amount_cents`.
   - *Urgency* is driven by `evidence_details.due_by`.
2. **Format the message.** The script writes `digest.json` containing **both** a native **Block Kit
   `blocks`** layout (the preferred, table-like Slack rendering) and a plain **`text`** version (the
   notification fallback). Disputes are sorted by soonest deadline then largest amount.
3. **Post to Slack, then mark it posted.** Post to the configured channel, **passing `blocks` (from
   `digest.json`) plus `text` as the fallback** — e.g. via the connected Slack tool / Airbyte Slack
   connector `messages.create` with `{channel, text, blocks}`, or set `SLACK_BOT_TOKEN` +
   `SLACK_CHANNEL` and use `--post` (which sends both). Passing `blocks` is what gives the native
   Slack look instead of a monospace block. **After a successful post, advance the watermark** so the
   next run only shows newer disputes: `--post` does this automatically; if you posted via a Slack tool
   instead, run `daily_dispute_digest.py --mark-posted`. Don't @-mention individuals unless asked.

### Digest format

The digest posts as native **Slack Block Kit**, and the disputes render in a real Slack **`table`
block** (the native table type added Aug 2025 — not a monospace/CLI code block). Structure:

- **Header block:** `🛡️ Disputes needing a response — {date}`.
- **Summary section:** `{N} new since last digest · {R} due-soon reminder(s) · {CUR} {total} at risk ·
  {M} due within 48h` (the reminder count shows only when re-surfaced disputes are present; says "open"
  instead of "new since last digest" under `--all`; appends "· {k} below evaluate threshold" when any
  are below the amount floor).
- **`table` block** with a header row + one row per dispute, columns: *Dispute · Amount · Reason · Due
  · Lean · Customer* (Amount right-aligned; Reason/Customer wrap). The **Lean** cell carries the hint
  (`FIGHT`/`ACCEPT`/`REVIEW`/`SKIP`/`EXPIRED`); a re-surfaced item shows "(reminder)" in its Due cell.
- **Context footer:** `Paste evaluate <dispute-id> into Claude …` — copy a Dispute id to work it.

Slack's table block allows ≤100 rows / ≤20 cols / ≤10k chars, so very large digests show the first ~90
disputes and note the overflow (the full list is in the `text` fallback / `digest.json`). The message
always includes the plain-`text` code-block table too, as the notification fallback and for any client
that doesn't render the table block. If there are no new disputes, it's just the header + "✅ No new
disputes since the last digest."

**Acting on `evaluate dp_<id>`:** this phrase is the handoff from the digest. When a user sends it, run
the full workflow (Steps 0–4) for that dispute id — the real evaluation, an honest fight/accept
recommendation, and (only if fighting) the evidence package. It is *not* the digest's quick lean;
gather evidence and decide properly.

### Scheduling

The skill describes *what* to post; a scheduled task makes it *recur*. Set up a daily run with the
`schedule` skill (a cron-style cloud agent) whose prompt is roughly: *"Run the dispute-fighter daily
digest and post it to the disputes channel."* The schedule needs the time, timezone, and target
channel — keep those in the schedule, not hard-coded here, so they're easy to change.

**Works on any OS.** The recommended path — the Claude client's scheduling feature — runs in the
cloud, so it's platform-independent (Windows, macOS, Linux). If instead you schedule the script
directly on your own machine, use **cron** on macOS/Linux or **Task Scheduler** (`schtasks`) on
**Windows**; the scripts are pure Python 3.10+ with no UNIX-only dependencies, so they run on Windows
as well. (The `#!/usr/bin/env python3` shebang lines are ignored on Windows — invoke with
`python scripts\daily_dispute_digest.py`.)

Intended default for this workspace: **9:00 AM daily**, posting to a **dedicated disputes channel**
(create one if it doesn't exist yet). Adjust freely when you create the schedule.

## Output files

Write artifacts next to the context JSON (or to a user-specified folder):
`dispute_context.json`, `evidence.json`, `evidence_package.md`, `digest.json`.
