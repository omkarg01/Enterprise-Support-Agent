---
doc_id: kb-help-privacy-data-deletion-retention
title: Data privacy, tenant exports, right-to-erasure deletion requests, and retention
source_system: helpcenter
source_kind: curated
audience: customer_visible
provenance: human
acl_groups: []
is_restricted: false
updated_at: 2026-09-23
intents: [GENERAL_INQUIRY, SUBSCRIPTION_UPDATE]
test_case: none
---

# Data privacy, tenant exports, right-to-erasure deletion requests, and retention

## Roles of Data Controller and Data Processor on Nimbus Cloud Platform

Under the Nimbus Cloud Platform Data Processing Addendum (DPA) and applicable privacy regulations (including GDPR and CCPA/CPRA), the business customer (tenant) acts as the **Data Controller** for all data stored inside Nimbus DB, Nimbus Run, Nimbus Net, and Nimbus POS Gateway, while Nimbus Systems, Inc. acts as the **Data Processor**. For administrative account records (such as console user login emails `<EMAIL_1>` and billing invoices), Nimbus Systems acts as an independent Data Controller.

## Exporting your data from Nimbus Cloud Platform

### Self-service infrastructure and audit log exports

Tenant Owners and Infrastructure Operators can export all customer data in standard, portable formats at any time without opening a support ticket:

- **Nimbus DB Exports:** Generate logical PostgreSQL dumps (`pg_dump` format) or export decrypted snapshot archives to an external object storage bucket via `nimbus db export create --cluster <CLUSTER_ID> --format custom`.
- **Nimbus Run Configurations:** Export cluster manifests, Helm values, and workload definitions via `nimbus run export-manifests --cluster <CLUSTER_ID>`.
- **Audit & Access Logs:** Export up to 365 days of JSON-formatted control-plane audit logs from **Organization Settings > Compliance & SLA > Audit Log Export**.

## Submitting right-to-erasure and data deletion requests

### Deleting individual end-customer records inside your workloads

Because Nimbus Systems does not inspect or query the interior of customer databases, when one of your own retail or business end-users submits a GDPR Article 17 Right to Erasure request to your company, your engineering team is responsible for deleting that individual's record from your Nimbus DB tables and application logs. Once deleted from your active Nimbus DB database, the record automatically ages out of rolling Write-Ahead Logs and daily automated backups according to your configured backup retention window (**7 to 35 days**).

### Requesting erasure of a Nimbus Cloud Platform console user (`<EMAIL_1>`)

To permanently erase a former employee's Nimbus Cloud Platform user profile (`<EMAIL_1>`, `<NAME_1>`, `<PHONE_1>`):

1. A **Tenant Owner** verified at Tier T2 removes the user from **Organization Settings > Users & Roles > Delete User**.
2. To purge the user's personal email and name from historical support tickets and identity logs ahead of standard retention, submit a **Privacy Erasure Request** from the console after completing T2 verification.
3. Nimbus Privacy Operations completes verified user-profile erasure requests within **30 calendar days** and replaces personal identifiers in historical support records with anonymized tokens (`<REDACTED_USER>`), while preserving immutable security audit timestamps.

## Standard Nimbus Cloud Platform data retention schedules

| Data Category | Active Subscription Retention | Post-Cancellation / Deletion Retention |
| :--- | :--- | :--- |
| **Nimbus DB Automated Backups** | Configurable: **7 to 35 days** (default 14 days) | Retained for **30-day soft-delete recovery window**, then cryptographically erased |
| **Nimbus Run Container & Pod Logs** | **30 days** hot searchable retention | Purged **30 days** after tenant cancellation |
| **Nimbus Net VPC Flow Logs** | **90 days** in regional log store | Purged **30 days** after tenant cancellation |
| **Control-Plane Security Audit Logs** | **365 days** online retention | Retained **365 days** for security compliance, then purged |
| **Support Tickets & Chat Transcripts** | Active duration + **24 months** (PII redacted) | Anonymized after **24 months** |
| **Tax Invoices & Financial Ledger** | **7 years** (statutory tax/audit requirement) | **7 years** (statutory tax/audit requirement) |

When a tenant cancels their subscription and the **30-calendar-day** post-cancellation recovery period expires, Nimbus Systems performs cryptographic erasure by destroying the tenant's regional Key Management Service (KMS) master keys (`NIST SP 800-88 Rev. 1` compliant), rendering all underlying storage blocks permanently unreadable.
