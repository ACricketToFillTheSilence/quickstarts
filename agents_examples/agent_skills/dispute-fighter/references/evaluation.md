# Evaluation Rubric — Is This Dispute Worth Fighting?

The goal is an honest, decisive call: **FIGHT** or **ACCEPT** (or **SKIP** if it's below the
configured threshold). Winning chargebacks is about evidence fit, not effort or principle. A clean
"accept" that saves the merchant wasted time is as valuable as a winnable "fight." Walk these factors,
then synthesize.

## 0. Currency (read before the money gates)
Judge the dispute in its own `currency` — **never convert between currencies.** Stripe's `amount` is
in the currency's **smallest unit**: cents for USD/EUR/GBP, but **whole units** for zero-decimal
currencies (JPY, KRW, HUF, VND, …) where `amount: 1000` means ¥1,000, not ¥10.00. The money config
fields are in that same smallest unit and may be **per-currency maps** — resolve each for this
dispute's currency: `map[CURRENCY]` → `map["default"]` → the scalar value.

## 0a. Amount threshold (gate — check first)
If `evaluation.min_amount_cents` (resolved for this currency) is configured and the disputed amount is
below it, the merchant has decided this dispute isn't worth the effort. Recommend **SKIP** (accept the
loss) with a one-line reason and stop — don't gather evidence or build a package — unless the user
explicitly asked to evaluate this specific dispute anyway. Threshold 0 (default) evaluates everything.
Example: a ¥30,000 JPY dispute with `min_amount_cents: {"JPY": 50000}` is below the ¥50,000 floor → SKIP.

## 0b. Winnability floor (company policy gate)
After you estimate the win probability (factors 1–5), compare it to `evaluation.min_winnability`
(0..1). If your estimate is below the floor, recommend **ACCEPT** and stop — the company won't fight
odds that low. State both numbers. A floor of `0.0` (default) imposes no limit. Remember Stripe's
reality: even strong cases cap around ~60%, so a floor above ~0.6 means fighting almost nothing.

## 1. Reason code & base winnability
Look up the dispute's `reason` in the reason-code table in
[stripe-disputes.md](stripe-disputes.md). This sets the prior. Process/bank reasons
(`bank_cannot_process`, `check_returned`, `debit_not_authorized`, `incorrect_account_details`,
`insufficient_funds`, `noncompliant`, `customer_initiated`) are rarely worth fighting — note that
and stop unless there's an unusual angle.

## 2. Evidence strength (the deciding factor)
Identify the **keystone evidence** for this reason code and check whether the gathered context
actually contains it:
- `product_not_received` → is there real delivery/tracking confirmation?
- `subscription_canceled` → is there a *disclosed* cancellation policy the customer agreed to, and
  proof no valid cancellation was received?
- `duplicate` → are the two charges provably separate purchases?
- `fraudulent`/`unrecognized` → do AVS/CVC match, is there a purchase IP, prior undisputed orders,
  or product-access logs?

If the keystone is missing and can't be obtained before the deadline, that's a strong **ACCEPT**
signal regardless of amount.

## 3. Economics
`EV ≈ (win_probability × amount) − dispute_fee − effort`. The dispute fee is lost either way. Small
amounts (e.g. under a few multiples of the fee) with anything less than strong evidence usually
don't clear the bar. Large amounts justify fighting even at moderate odds. State the rough math.

## 4. Risk & friendly-fraud signals
These shift `fraudulent`/`unrecognized` odds and color the narrative:
- AVS/CVC match, low Stripe `risk_level`, matching billing/shipping/IP geography.
- Prior successful, undisputed charges from the same customer.
- Product was accessed/used after purchase (access logs).
- Customer never contacted support before disputing (textbook friendly fraud) — call this out.
- *Against* fighting: high risk_level, AVS/CVC mismatch, first-time customer, no usage.

## 5. Deadline
Check `evidence_details.due_by`. If it has passed, you cannot submit — say so. If it's close, flag
the time pressure and prioritize gathering the keystone evidence.

## Synthesis
Combine into a single verdict. Be explicit about *why* and about what would change the call (e.g.
"ACCEPT now, but FIGHT if delivery confirmation can be located before <due_by>"). Use the
Recommendation template in SKILL.md. Avoid hedging into "it depends" — give the call, then the
caveats.
