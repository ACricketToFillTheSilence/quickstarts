#!/usr/bin/env python3
"""Render a Stripe-submittable evidence.json + a human-readable evidence_package.md.

You (the model) do the judgment — decide which Stripe evidence fields the gathered
context supports and what each value should be. This script does the deterministic,
error-prone parts: validating field names against Stripe's exact allowed set (a
misspelled key is silently dropped), enforcing Stripe's "one piece of evidence per
type" rule by flagging multi-file fields for consolidation, and rendering the review
document — including Stripe's file-format requirements and (when applicable) a Visa
Compelling Evidence 3.0 section. Follows https://docs.stripe.com/disputes/best-practices.

Input: a JSON file you author from the dispute context. Each evidence value can be:
  - text field  : "a string"  OR  {"value": "...", "proves": "what it demonstrates"}
  - file field  : {"file": "path"}  OR  {"files": ["a","b"], "proves": "..."}
                  (multiple files in one field are flagged to be MERGED into one upload,
                   because Stripe accepts only one file per evidence type)

Optional top-level blocks:
  - "visa_ce3":   {"eligible": true, "prior_transactions": [
                     {"charge_id":"ch_..","date":"2026-01-02","matches":["ip","device","shipping_address"]}]}
  - "credit_issued": {"amount":"49.00","date":"2026-06-10","screenshot":"path"}   # partial-refund "credit issued" response

Example:
    {
      "dispute_id":"dp_123","charge_id":"ch_123","reason":"product_not_received",
      "reason_plain":"Goods never arrived","amount":"129.00","currency":"USD","fee":"15.00",
      "due_by":"2026-07-10","days_left":"11","customer_name":"Jane Doe","customer_email":"jane@example.com",
      "recommendation":"FIGHT","rationale":"Delivery confirmed via tracking.",
      "narrative":"Jane Doe purchased ... shipped ... delivered ...",
      "evidence":{
        "shipping_carrier":"UPS","shipping_tracking_number":"1Z...","shipping_date":"2026-03-05",
        "shipping_documentation":{"file":"drive://.../pod.pdf","proves":"carrier delivery confirmation to billing address"},
        "customer_communication":{"files":["drive://.../email1.pdf","drive://.../sms.png"],"proves":"customer acknowledged receipt"},
        "uncategorized_text":{"value":"...","proves":"chronological summary of the transaction"}
      },
      "gaps":["No signature image on file."]
    }

Usage:
    python build_evidence_package.py input.json --template ../assets/evidence_package_template.md --outdir .
"""
import argparse
import json
import os
import sys

TEXT_FIELDS = {
    "access_activity_log", "billing_address", "cancellation_policy_disclosure",
    "cancellation_rebuttal", "customer_email_address", "customer_name",
    "customer_purchase_ip", "duplicate_charge_explanation", "duplicate_charge_id",
    "product_description", "refund_policy_disclosure", "refund_refusal_explanation",
    "service_date", "shipping_address", "shipping_carrier", "shipping_date",
    "shipping_tracking_number", "uncategorized_text",
}
FILE_FIELDS = {
    "cancellation_policy", "customer_communication", "customer_signature",
    "duplicate_charge_documentation", "receipt", "refund_policy",
    "service_documentation", "shipping_documentation", "uncategorized_file",
}
ALL_FIELDS = TEXT_FIELDS | FILE_FIELDS
# Policy-type file fields where Stripe wants an excerpt screenshot, NOT a link to a live page.
POLICY_FILE_FIELDS = {"cancellation_policy", "refund_policy"}


def validate(evidence):
    bad = [k for k in evidence if k not in ALL_FIELDS]
    if bad:
        sys.exit(
            "Unknown Stripe evidence field(s): %s\nAllowed text fields: %s\nAllowed file fields: %s"
            % (", ".join(bad), ", ".join(sorted(TEXT_FIELDS)), ", ".join(sorted(FILE_FIELDS)))
        )


def normalize(key, v):
    """Return a uniform entry dict for a text or file field."""
    if key in TEXT_FIELDS:
        if isinstance(v, dict):
            return {"type": "text", "text": v.get("value", ""), "proves": v.get("proves", "")}
        return {"type": "text", "text": str(v), "proves": ""}
    # file field
    if isinstance(v, dict):
        srcs = v.get("files") or ([v["file"]] if v.get("file") else [])
        proves = v.get("proves", "")
    elif isinstance(v, list):
        srcs, proves = v, ""
    else:
        srcs, proves = [v], ""
    return {"type": "file", "files": [str(s) for s in srcs], "proves": proves}


def build(evidence):
    """Return (stripe_evidence_dict, entries) where entries carry proves/consolidation info."""
    out, entries, warnings = {}, [], []
    for k, v in evidence.items():
        e = normalize(k, v)
        e["key"] = k
        entries.append(e)
        if e["type"] == "text":
            out[k] = e["text"]
        else:
            srcs = e["files"]
            if len(srcs) > 1:
                out[k] = f"<<UPLOAD one merged file ({len(srcs)} sources) -> Stripe File id>>"
            elif srcs:
                out[k] = f"<<UPLOAD: {srcs[0]} -> Stripe File id>>"
            else:
                out[k] = "<<UPLOAD: (missing file) -> Stripe File id>>"
            # Stripe: upload an excerpt screenshot, never a link to an external page.
            if k in POLICY_FILE_FIELDS:
                for s in srcs:
                    if s.startswith(("http://", "https://")):
                        warnings.append(
                            f"`{k}`: '{s}' looks like a live URL — Stripe won't follow links. "
                            "Upload a screenshot/PDF excerpt of the relevant policy instead.")
    return out, entries, warnings


FORMAT_REQUIREMENTS = """\
- **File types:** PDF, JPEG, or PNG only.
- **Combined size:** under 4.5 MB total.
- **Page count:** under 50 pages (Mastercard: under 19).
- **Layout:** US-Letter or A4, **portrait**, 12-pt font or larger.
- **Emphasis:** use bold text, callouts, or arrows to point at the relevant line. Avoid color highlighting.
- **One file per evidence type:** merge multiple items of the same type into a single file (e.g. all
  customer messages → one `customer_communication` file).
- **Excerpt, don't dump:** paste only the relevant subsection of a policy; never upload the entire ToS
  or link to an external page."""


def render_evidence_rows(entries):
    rows = []
    for e in entries:
        k = e["key"]
        if e["type"] == "text":
            shown = e["text"]
            shown = (shown[:90] + "…") if len(shown) > 90 else shown
            status = "ready"
        else:
            n = len(e["files"])
            shown = (e["files"][0] if n == 1 else f"{n} files → MERGE into one") if n else "(missing)"
            status = "needs upload" + (" + merge" if n > 1 else "")
        proves = e.get("proves", "") or "—"
        rows.append(f"| `{k}` | {e['type']} | {shown} | {proves} | {status} |")
    return "\n".join(rows)


def render_file_checklist(entries):
    items = []
    for e in entries:
        if e["type"] != "file":
            continue
        if len(e["files"]) > 1:
            items.append(f"- [ ] `{e['key']}` — **merge into one file**, then upload:")
            items += [f"    - {s}" for s in e["files"]]
        elif e["files"]:
            items.append(f"- [ ] `{e['key']}` — upload `{e['files'][0]}`")
    return "\n".join(items) or "- (no files)"


DEFAULT_CE3_NOTE = (
    "_Not flagged for Visa CE 3.0. For `fraudulent`/`unrecognized` disputes, check the Stripe "
    "Dashboard for a CE 3.0 badge — if eligible, supplying 2 prior undisputed transactions with "
    "matching data points is the strongest lever._")


def render_ce3(data):
    ce3 = data.get("visa_ce3")
    # Treat a defensively-passed empty/ineligible block the same as absent.
    if not ce3 or (not ce3.get("eligible") and not ce3.get("prior_transactions")):
        return DEFAULT_CE3_NOTE
    lines = [f"**Eligible:** {ce3.get('eligible', 'unknown')}",
             "Prior undisputed transactions with the same cardholder (matching data points are "
             "_Required for Visa CE 3.0_):", "",
             "| Charge | Date | Matching data points |", "|---|---|---|"]
    for t in ce3.get("prior_transactions", []):
        lines.append(f"| {t.get('charge_id','')} | {t.get('date','')} | {', '.join(t.get('matches', []))} |")
    if len(ce3.get("prior_transactions", [])) < 2:
        lines.append("\n> ⚠️ CE 3.0 generally needs **2** qualifying prior transactions — fewer may not qualify.")
    return "\n".join(lines)


def _is_zero(amount):
    try:
        return float(str(amount).replace(",", "") or 0) == 0
    except ValueError:
        return False


def render_credit_issued(data):
    ci = data.get("credit_issued")
    # Skip when absent or a defensively-passed zero/empty credit.
    if not ci or not ci.get("amount") or _is_zero(ci.get("amount")):
        return ""
    return ("\n## Credit-issued response\n"
            "A refund/credit was already issued — Stripe advises always responding in these cases.\n"
            f"- **Amount:** {ci.get('amount','')}  **Date:** {ci.get('date','')}\n"
            f"- **Screenshot to upload:** {ci.get('screenshot','(add Dashboard refund screenshot)')}\n")


def render(data, entries, template):
    repl = {
        "dispute_id": data.get("dispute_id", ""), "charge_id": data.get("charge_id", ""),
        "reason": data.get("reason", ""), "reason_plain": data.get("reason_plain", ""),
        "amount": data.get("amount", ""), "currency": data.get("currency", ""),
        "fee": data.get("fee", ""), "due_by": data.get("due_by", ""),
        "days_left": data.get("days_left", ""), "customer_name": data.get("customer_name", ""),
        "customer_email": data.get("customer_email", ""),
        "FIGHT_or_ACCEPT": data.get("recommendation", ""),
        "one_line_rationale": data.get("rationale", ""),
        "narrative": data.get("narrative", ""),
        "evidence_rows": render_evidence_rows(entries),
        "file_checklist": render_file_checklist(entries),
        "format_requirements": FORMAT_REQUIREMENTS,
        "ce3_section": render_ce3(data),
        "credit_issued_section": render_credit_issued(data),
        "gaps": "\n".join(f"- {g}" for g in data.get("gaps", [])) or "- None noted.",
    }
    out = template
    for key, val in repl.items():
        out = out.replace("{{%s}}" % key, str(val))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Authored evidence-mapping JSON")
    ap.add_argument("--template", default=os.path.join(
        os.path.dirname(__file__), "..", "assets", "evidence_package_template.md"))
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    with open(args.input) as f:
        data = json.load(f)
    evidence = data.get("evidence", {})
    validate(evidence)
    stripe_evidence, entries, warnings = build(evidence)

    os.makedirs(args.outdir, exist_ok=True)
    ev_path = os.path.join(args.outdir, "evidence.json")
    with open(ev_path, "w") as f:
        json.dump({"evidence": stripe_evidence}, f, indent=2)

    with open(args.template) as f:
        template = f.read()
    doc = render(data, entries, template)
    with open(os.path.join(args.outdir, "evidence_package.md"), "w") as f:
        f.write(doc)

    print(f"Wrote {ev_path} and evidence_package.md.")
    merges = [e["key"] for e in entries if e["type"] == "file" and len(e["files"]) > 1]
    if merges:
        print(f"Consolidation needed (one file per type): {', '.join(merges)}.")
    for w in warnings:
        print("WARNING: " + w)


if __name__ == "__main__":
    main()
