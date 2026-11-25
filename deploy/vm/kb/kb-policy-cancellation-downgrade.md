---
doc_id: kb-policy-cancellation-downgrade
title: Cancellation and downgrade policy: effective dates and post-cancellation retention
source_system: policies
source_kind: curated
audience: customer_visible
provenance: human
acl_groups: []
is_restricted: false
updated_at: 2026-09-18
intents: [SUBSCRIPTION_UPDATE, BILLING_INQUIRY]
test_case: none
---

# Cancellation and downgrade policy: effective dates and post-cancellation retention

## How to cancel or downgrade a Nimbus Cloud Platform subscription

Only a user assigned the **`Tenant Owner`** role who has completed **Tier 2 (T2) identity verification** (authenticated via corporate SSO with Multi-Factor Authentication) may submit a subscription cancellation or tier downgrade on Nimbus Cloud Platform. Standard users, Read-Only Auditors, and T1 email-verified contacts cannot cancel a tenant or downgrade a subscription plan.

### Required advance notice deadlines

To prevent an upcoming monthly or annual term from auto-renewing at the current tier, the Tenant Owner must submit the cancellation or downgrade through **Organization Settings > Billing & Plan > Cancel or Downgrade** before the applicable notice cutoff:

| Billing Cadence | Minimum Advance Notice Required | Effective Date of Cancellation or Downgrade |
| :--- | :--- | :--- |
| **Monthly Plan (Starter / Growth)** | At least **7 calendar days** before the 1st of the next month | Last day of the current calendar month (`23:59:59 UTC`) |
| **Annual Commitment (Starter / Growth / Enterprise)** | At least **30 calendar days** before the annual renewal anniversary date | Last day of the current 12-month contract term (`23:59:59 UTC`) |

## No partial refunds for mid-term cancellations or downgrades

When you submit a cancellation or downgrade request, your tenant remains active at its current tier until the **effective date** at the end of the current paid billing period. In accordance with the **Nimbus Refund Policy**, Nimbus Systems does **not** provide partial or pro-rated cash refunds or account credits for unused days, weeks, or months remaining in a monthly or annual commitment when a customer elects to cancel early or downsize seats mid-term.

The only exceptions where a cancellation results in a refund are:

1. **First-Time 14-Day Initial Cancellation Window:** A first-time Starter or Growth tenant cancelling within **14 calendar days** of initial sign-up with under **$100.00 USD** in total metered infrastructure usage.
2. **Termination for Material Uncured Breach:** If the customer terminates an Enterprise contract for cause due to an uncured material breach by Nimbus Systems following 30 days' formal legal notice.

## Post-cancellation lifecycle and 30-day data retention window

Once a subscription reaches its cancellation effective date (`TERM_EXPIRED_CANCELLED`), Nimbus Cloud Platform transitions the tenant through a structured 3-stage decommissioning lifecycle to protect against accidental data loss:

### Stage 1: Day 1 to Day 7 post-cancellation (Grace Export Window)

- Compute workloads in **Nimbus Run**, database listeners in **Nimbus DB**, and retail payment relays in **Nimbus POS Gateway** are gracefully stopped (`STOPPED_CANCELLED`), halting all metered billing.
- The **Tenant Owner** retains read-only console and CLI access for **7 calendar days** solely to download final `pg_dump` database snapshots, Terraform state files, and compliance audit logs.

### Stage 2: Day 8 to Day 30 post-cancellation (Soft-Delete Quarantine)

- Console and API access is locked (`TENANT_QUARANTINED`), and encrypted volume snapshots are held in cold regional quarantine storage inside the tenant's original US or EU residency realm.
- During this 30-day window, a Tenant Owner can still reactivate the tenant by requesting reactivation (handled by `queue-billing-ops`) and settling a renewal invoice; all Nimbus DB and Nimbus Run state can be restored intact.

### Stage 3: Day 31 post-cancellation (Permanent Cryptographic Erasure)

- At `00:00 UTC` on **Day 31** after the cancellation effective date, Nimbus Cloud Platform permanently destroys the tenant's regional KMS encryption keys and deallocates all storage blocks (`NIST SP 800-88 Rev. 1` purge). Once Day 31 erasure completes, customer data is permanently unrecoverable.
