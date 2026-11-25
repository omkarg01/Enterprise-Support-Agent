---
doc_id: kb-help-subscription-management
title: Managing your subscription: upgrades, downgrades, seats, renewals, and plan info
source_system: helpcenter
source_kind: curated
audience: customer_visible
provenance: human
acl_groups: []
is_restricted: false
updated_at: 2026-09-19
intents: [SUBSCRIPTION_UPDATE, GENERAL_INQUIRY]
test_case: none
---

# Managing your subscription: upgrades, downgrades, seats, renewals, and plan info

## Where to find your Nimbus Cloud Platform subscription tier and renewal date

### Viewing plan details in the Nimbus Cloud Platform web console

Customers can inspect their active subscription tier, contracted seat count, billing cycle, and exact auto-renewal date directly inside the Nimbus Cloud Platform web console:

1. Sign in to the regional Nimbus Cloud Platform console (`https://us.console.nimbuscloud.example.com` or `https://eu.console.nimbuscloud.example.com`).
2. Navigate to **Organization Settings > Billing & Plan > Subscription Overview**.
3. Review the **Active Plan Card**, which displays:
   - **Current Tier:** Starter, Growth, or Enterprise.
   - **Billing Cadence:** Monthly or Annual Commitment.
   - **Contracted Base Rate:** The USD recurring amount per billing cycle.
   - **Provisioned Seats:** Active seats versus included seat allowance.
   - **Next Renewal Date:** The ISO date (`YYYY-MM-DD`) when the current term renews automatically.
   - **Named Support Manager (Enterprise only):** Name and contact details of your assigned Enterprise support lead.

### What Nimbus Support can disclose about a tenant's subscription

To protect customer confidentiality across our multi-tenant platform, the Nimbus Support knowledge base never stores live customer account records, subscription tiers, or renewal dates. If you ask Nimbus Support for the current subscription tier and renewal date of a specific tenant (such as `Globex Corp` or `Acme Retail`), the support agent enforces the **Nimbus Identity Verification Policy**:

- **Unverified / Anonymous Users (Tier T0):** Support cannot disclose any tenant's subscription tier, renewal date, or billing status. Support will only explain how an authorized user can check **Organization Settings > Billing & Plan > Subscription Overview**.
- **Verified Tenant Users (Tier T1 via one-time email verification code or Tier T2 via corporate SSO with MFA):** Once your identity is verified at **T1 or T2** as an authorized member of that exact tenant, a support specialist can look up and disclose your own tenant's subscription tier and renewal date from the live Nimbus CRM/Billing tool. Support can never disclose another organization's plan details.

## Upgrading, downgrading, and modifying user seats

### Self-service upgrades and seat additions

Users with the `Tenant Owner` or `Billing Administrator` role (authenticated via T2 SSO with MFA) can upgrade from Starter to Growth or add additional user seats at any time under **Organization Settings > Billing & Plan > Change Plan**. Upgrades and seat additions take effect immediately and generate a pro-rated USD invoice for the remainder of the current billing period.

To upgrade to the **Enterprise** plan or expand an Enterprise annual seat block, click **Request Enterprise Quote** in the billing console to execute an updated Enterprise Order Form.

### Scheduling a plan downgrade or seat reduction

Before downgrading from Enterprise to Growth, or from Growth to Starter, verify that your active resource footprint falls within the target tier's quotas (for example, Starter allows a maximum of 3 single-AZ Nimbus DB clusters and zero Nimbus POS Gateway terminals). If your tenant exceeds the lower tier's limits, the console displays `Error SUB_QUOTA_EXCEEDED` until excess resources are decommissioned.

Downgrades and seat reductions take effect at the **end of your current billing term** and require advance notice:

- **Monthly Subscriptions:** Submit the downgrade or seat reduction in the console at least **7 calendar days** before your next monthly billing date.
- **Annual Subscriptions:** Submit written notice or toggle **Schedule Term Change** at least **30 calendar days** prior to your annual renewal date.

## Auto-renewal and cancellation rules

All Nimbus Cloud Platform subscriptions auto-renew at the end of their monthly or 12-month annual term unless cancelled before the notice deadline (**7 calendar days** prior to renewal for monthly plans; **30 calendar days** prior to renewal for annual contracts). A `Tenant Owner` verified at Tier T2 can cancel auto-renewal under **Organization Settings > Billing & Plan > Cancel Subscription**.
