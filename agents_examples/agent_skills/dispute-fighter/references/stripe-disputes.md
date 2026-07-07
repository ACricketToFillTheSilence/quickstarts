# Stripe Disputes Reference

Everything needed to evaluate winnability and map evidence correctly. Field names and reason codes
here are taken verbatim from Stripe's Dispute API and must be used exactly — a misspelled evidence
key is silently dropped by Stripe.

## Contents
- [Dispute lifecycle & key fields](#dispute-lifecycle--key-fields)
- [Reason codes → winnability & evidence playbook](#reason-codes--winnability--evidence-playbook)
- [Stripe `evidence` field reference (exact names)](#stripe-evidence-field-reference-exact-names)
- [Submission best practices (from Stripe)](#submission-best-practices-from-stripe)
- [Visa Compelling Evidence 3.0](#visa-compelling-evidence-30)
- [Economics of fighting](#economics-of-fighting)

> Source: <https://docs.stripe.com/disputes/best-practices>. Reality check up front: **even the
> strongest cases rarely exceed ~60% odds** (Stripe's top "5-dot" prediction = 60%). Treat every
> winnability number below as a ceiling, not a promise.

## Dispute lifecycle & key fields

A dispute (chargeback) is opened by the cardholder's bank. Funds and a **non-refundable dispute fee**
(commonly ~$15, varies by region/account) are withdrawn immediately. The merchant submits evidence
("representment") by `evidence_details.due_by`; the issuer rules weeks later. You generally get **one**
submission, so it must be complete.

Fields you'll read off the dispute object:
- `reason` — the category cited (drives everything below).
- `amount`, `currency` — disputed amount.
- `status` — e.g. `warning_needs_response`, `needs_response`, `under_review`, `won`, `lost`. Only
  `needs_response` / `warning_needs_response` are actionable.
- `evidence_details.due_by` (unix ts), `evidence_details.has_evidence`, `evidence_details.submission_count`.
- `evidence` — the (mostly empty) evidence hash you will populate.
- `charge` / `payment_intent` — link to the underlying payment, customer, and risk `outcome`
  (`risk_level`, `network_status`, AVS/CVC checks, customer IP).

## Reason codes → winnability & evidence playbook

Base winnability is a rough prior, not a guarantee — evidence quality dominates. "Keystone evidence"
is the piece without which you almost certainly lose; if you don't have it, lean toward **accept**.

| `reason` | Plain meaning | Base winnability | Keystone evidence | Also strengthens |
|---|---|---|---|---|
| `fraudulent` | Cardholder says they didn't authorize it | Low–moderate (cap ~60%) | Proof the legit cardholder transacted: AVS/CVC match, **3DS authentication**, `customer_purchase_ip` matching billing geo, prior undisputed orders, account login/access logs. **Check Visa CE 3.0 eligibility first** (see below). | `customer_signature`, delivery to verified `billing_address`, `customer_communication` |
| `unrecognized` | Cardholder doesn't recognize the charge | Moderate (cap ~60%) | Clear descriptor + receipt tying charge to a known purchase | `customer_communication`, `product_description`, access/usage logs; CE 3.0 if eligible |
| `product_not_received` | Goods/services never arrived | Moderate (cap ~60%, physical w/ tracking) | Delivery confirmation with the **full delivery address** (not just city/postal): `shipping_carrier`, `shipping_tracking_number`, `shipping_date`, `shipping_documentation`. For digital goods, IP/system logs proving download/use. | `service_date`/`service_documentation`, `access_activity_log`, `customer_communication` |
| `product_unacceptable` | Product defective / not as described | Low–moderate | `product_description` + proof it matched what was delivered; `refund_policy` + `refund_policy_disclosure` | photos, QA records (`uncategorized_file`), `customer_communication` |
| `subscription_canceled` | Billed after claimed cancellation | Moderate (cap ~60%, if policy disclosed) | `cancellation_policy` + `cancellation_policy_disclosure` (a clean **checkout screenshot** showing the customer agreed); proof no cancellation was received | `access_activity_log` showing continued use, `customer_communication` |
| `duplicate` | Charged twice for one purchase | Good (cap ~60%, if actually distinct) | `duplicate_charge_id` + `duplicate_charge_explanation` proving the two charges are separate purchases | `duplicate_charge_documentation`, two receipts |
| `credit_not_processed` | Promised refund/credit not given | Low (if refund truly owed) | Proof refund was issued, OR `refund_policy` + `refund_refusal_explanation` justifying refusal | `customer_communication`, refund transaction record |
| `general` | Unspecified | Varies | Whatever fits the underlying story; lead with `uncategorized_text` | all relevant |
| `customer_initiated` / others (`bank_cannot_process`, `check_returned`, `debit_not_authorized`, `incorrect_account_details`, `insufficient_funds`, `noncompliant`) | Bank/process issues | Usually not worth fighting | — | — |

`access_activity_log` is populated from the optional `service_usage` role (a product-analytics tool,
if configured) — a dated log of the customer's logins/usage. Continued use *after* a claimed
cancellation or a "didn't authorize" claim is some of the most persuasive evidence available.

The `shipping_*`, `product_description`, and `receipt` fields are populated from the optional
`order_data` role (Shopify / WooCommerce / Amazon Seller Partner, if configured) — line items,
fulfillment/tracking, and the address the customer provided. This is the backbone of a
`product_not_received` rebuttal and surfaces gift ship-to mismatches.

Cross-cutting evidence that helps almost any case: `billing_address`, `customer_name`,
`customer_email_address`, `receipt`, a strong factual `uncategorized_text` narrative, and any
`customer_communication` showing the customer engaged (especially if they never contacted support
before disputing — a classic friendly-fraud tell).

## Stripe `evidence` field reference (exact names)

Use these keys exactly. **Text fields** take strings. **File fields** take a Stripe File upload id
in production — in this skill, record the document path/Drive link and flag it for upload.

**Text fields:** `access_activity_log`, `billing_address`, `cancellation_policy_disclosure`,
`cancellation_rebuttal`, `customer_email_address`, `customer_name`, `customer_purchase_ip`,
`duplicate_charge_explanation`, `duplicate_charge_id`, `product_description`,
`refund_policy_disclosure`, `refund_refusal_explanation`, `service_date`, `shipping_address`,
`shipping_carrier`, `shipping_date`, `shipping_tracking_number`, `uncategorized_text`.

**File-upload fields:** `cancellation_policy`, `customer_communication`, `customer_signature`,
`duplicate_charge_documentation`, `receipt`, `refund_policy`, `service_documentation`,
`shipping_documentation`, `uncategorized_file`.

Notes:
- `uncategorized_text` is the free-form narrative — the single most important field. Lead with a
  crisp factual account: who bought what, when, proof of delivery/use, and why the dispute is invalid.
- Date fields (`service_date`, `shipping_date`) are free-text but use a clear, consistent format
  (e.g. `2026-03-14`).
- Don't pad with irrelevant fields. Reviewers skim; a focused package tied to the reason code beats
  a kitchen-sink dump.

## Submission best practices (from Stripe)

How evidence is packaged matters as much as what's in it. Issuers manually review thousands of
responses daily and won't dig for your argument.

**Writing the narrative (`uncategorized_text`)**
- Be concise, factual, chronological, neutral. State plainly why the claim is unreasonable and how
  the evidence proves it. A long explanation is *not* more convincing.
- Model sentence: *"Jenny Rosen purchased [product] on [date] using a Visa card. We shipped it on
  [date] to the address she provided, and it was delivered on [date] per the attached tracking, so
  the claim that it wasn't received is untrue."*
- Add a one-line "what this proves" for each piece of evidence to guide the reviewer.

**File format & limits** (uploads that violate these can be rejected)
- PDF / JPEG / PNG only; combined size < 4.5 MB; < 50 pages (< 19 for Mastercard).
- US-Letter or A4, **portrait**, 12-pt font or larger. Use bold/callouts/arrows; avoid color highlighting.

**One piece of evidence per type** — Stripe accepts a single file per evidence field. Combine related
items (e.g. multiple emails + an SMS screenshot) into one `customer_communication` file.

**Excerpt, don't dump** — paste the *relevant subsection* of a policy and emphasize it; never upload
the entire ToS or link to it on an external site (issuers won't follow links / read it all). For
policy disclosure, include a clean **screenshot of how the policy is presented at checkout**.

**Evidence-type specifics**
- Receipt: show date, currency, and amount of the disputed items.
- Shipping: full delivery address, not just city/postal verification. If the "ship to" name differs
  from the cardholder (a gift), be ready to explain why.
- Customer communication: include the customer name and the relevant message.

**Partial refunds / "credit issued"** — if a refund or partial credit was already given, **always
respond**; issuers readily fix these. Include the refund amount, date, and a Dashboard screenshot.
Populate the `credit_issued` block so the package surfaces it.

**Accepting is fine** — accepting a dispute isn't an admission of wrongdoing and doesn't change the
(already-charged) dispute fee. Note: networks count disputes *received*, not won/lost — even a
withdrawn dispute still counts — so prevention beats representment.

## Visa Compelling Evidence 3.0

For friendly-fraud (`fraudulent`/`unrecognized`) disputes, **CE 3.0 is the single biggest lever**.
It lets you show a prior non-fraudulent history with the same cardholder to overturn the dispute.

- Stripe **auto-flags** eligible disputes and pre-populates qualifying transactions; required fields
  are badged "(Required for Visa CE 3.0)" in the evidence form. Check the Dashboard for the badge.
- Qualifying evidence is typically **2 prior undisputed transactions** with the same card that share
  data points with the disputed one (e.g. IP address, device ID, shipping address, or account login).
- When eligible, gather those prior transactions and populate the `visa_ce3` block; this materially
  raises win odds for friendly fraud.

## Economics of fighting

Rough expected value:  `EV ≈ (win_probability × amount) − dispute_fee − effort_cost`.
The dispute fee is lost whether you win or lose. So for small amounts with weak evidence, fighting
is often negative-EV even though "giving up" feels bad. Call this out explicitly in the
recommendation. For large amounts, even a moderate win probability justifies fighting.
