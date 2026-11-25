---
doc_id: kb-policy-billing-disputes
title: Billing dispute policy: how to raise a dispute, evidence, timelines, and holds
source_system: policies
source_kind: curated
audience: customer_visible
provenance: human
acl_groups: []
is_restricted: false
updated_at: 2026-09-16
intents: [BILLING_INQUIRY]
test_case: none
---

# Billing dispute policy: how to raise a dispute, evidence, timelines, and holds

## Right to dispute an invoice charge on Nimbus Cloud Platform

Business tenants on Nimbus Cloud Platform have the right to formally dispute any monthly or annual invoice charge that they believe reflects a billing calculation error, an unapproved renewal rate increase, an unauthorized seat addition, or an unapplied SLA service credit. To protect your account standing and avoid automated payment gateway blacklisting, always initiate a formal **Nimbus Billing Dispute** before filing an external credit card chargeback or ACH debit return with your bank.

### 60-calendar-day dispute filing deadline

A `Tenant Owner` or `Billing Administrator` (verified at Tier T2 via corporate SSO with MFA) must submit a formal billing dispute within **60 calendar days** of the disputed invoice's issue date (`YYYY-MM-DD`). Invoices that are not disputed within 60 calendar days of issuance are deemed final and accepted by the tenant.

## Required evidence to open a Nimbus Billing Dispute

When raising a billing dispute through **Organization Settings > Billing & Plan > Invoices > Dispute Invoice** (disputes of **$1,000 USD or higher** are reviewed by `queue-billing-ops`), provide the following mandatory information:

1. **Tenant ID and Organization Name:** For example, `tnt_01H9X8K2M4P` (`Acme Retail` or `Initech Logistics`).
2. **Disputed Invoice Number and Issue Date:** For example, `inv_2026_10_88412` issued on `2026-10-01`.
3. **Exact Disputed USD Amount vs. Undisputed USD Amount:** State clearly how much of the invoice is contested (for example, disputing a **$16,000 USD** overcharge where auto-renewal billed **$48,000 USD** instead of the **$32,000 USD** contracted rate).
4. **Supporting Documentary Evidence:** Attach or reference at least one of the following:
   - Your countersigned **Enterprise Order Form** or renewal quote showing the contracted price (such as `$32,000/year`) or contracted price-protection cap.
   - Written confirmation emails or ticket IDs proving timely non-renewal or downgrade notice (submitted at least 30 days before an annual renewal or 7 days before a monthly renewal).
   - Exported Nimbus resource audit logs or bank statements demonstrating a duplicate debit.

## Interim collection holds during active dispute investigation

### Protection against service suspension (`DISPUTE_HOLD_ACTIVE`)

As soon as Nimbus Billing Operations registers a valid, timely billing dispute, a **30-calendar-day Interim Collection Hold** (`DISPUTE_HOLD_ACTIVE`) is placed on the disputed portion of the invoice:

- **Zero Late Fees or Suspension:** While `DISPUTE_HOLD_ACTIVE` is in effect, the automated 14-day dunning clock and Day 15 Read-Only Suspension are frozen for the disputed balance. Your Nimbus DB, Nimbus Run, Nimbus Net, and Nimbus POS Gateway production workloads remain 100% operational.
- **Payment of Undisputed Balance:** For unpaid Net-30 invoices, the customer must still remit the **undisputed portion** of the invoice (for example, paying the undisputed `$32,000 USD` contracted renewal amount while the `$16,000 USD` overcharge is under investigation) by the standard invoice due date.
- **Already Charged Cards/ACH:** If an auto-renewal has already charged the full `$48,000 USD` to your credit card or bank account, Nimbus Billing places an immediate priority flag on the disputed `$16,000 USD` delta.

## Investigation timeline and resolution outcomes

Nimbus Billing Operations completes all formal billing dispute investigations within **10 business days** of receiving the required evidence:

- **Dispute Upheld (In Customer's Favor):** Nimbus voids or adjusts the erroneous invoice line item and, if the charge was already collected, initiates a refund of the overcharged amount to the original payment method in accordance with the **Internal Refund and Credit Approval Matrix** (amounts >= $1,000 require peer approval; amounts >= $5,000 require a Senior Billing Specialist).
- **Dispute Denied (Charge Validated):** Nimbus provides a written audit breakdown with contract citations and lifts the Interim Collection Hold, granting the customer **10 calendar days** from the resolution notice to settle any unpaid balance without penalty.
