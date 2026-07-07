# Dispute Evidence Package — {{dispute_id}}

> Prepared for human review. **Not yet submitted to Stripe.** Respond by **{{due_by}}** ({{days_left}} left).

## Case summary
- **Dispute:** {{dispute_id}}  |  **Charge:** {{charge_id}}
- **Reason code:** `{{reason}}` — {{reason_plain}}
- **Amount:** {{amount}} {{currency}}  |  **Dispute fee (non-refundable):** {{fee}}
- **Customer:** {{customer_name}} <{{customer_email}}>
- **Recommendation:** {{FIGHT_or_ACCEPT}} — {{one_line_rationale}}

## Rebuttal narrative

{{narrative}}

## Evidence mapped to Stripe fields

| Stripe field | Type | Value / source | What it proves | Status |
|---|---|---|---|---|
{{evidence_rows}}

## Visa Compelling Evidence 3.0
{{ce3_section}}
{{credit_issued_section}}
## Files to upload as Stripe File objects
{{file_checklist}}

### Stripe file requirements (apply before uploading)
{{format_requirements}}

## Gaps & risks
{{gaps}}

## Reviewer checklist before submitting
- [ ] Narrative is accurate, concise, chronological, and matches the attached evidence (issuers skim — don't pad)
- [ ] Only fields backed by real evidence are included (a focused package beats a kitchen-sink one)
- [ ] Every file meets Stripe's format requirements (type, size, pages, portrait, 12-pt+)
- [ ] One file per evidence type (multi-item types merged into a single file)
- [ ] Policies are excerpted (relevant subsection emphasized), not dumped or linked externally
- [ ] If Visa CE 3.0 eligible, the qualifying prior transactions are included
- [ ] Odds are realistic (even strong cases rarely exceed ~60%)
- [ ] Submitting before {{due_by}}
- [ ] Confirmed this is the single, final submission
