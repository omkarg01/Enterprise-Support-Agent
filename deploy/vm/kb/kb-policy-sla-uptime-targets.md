---
doc_id: kb-policy-sla-uptime-targets
title: Nimbus Cloud Platform Service Level Agreement (SLA) and uptime measurement
source_system: policies
source_kind: curated
audience: customer_visible
provenance: human
acl_groups: []
is_restricted: false
updated_at: 2026-09-15
intents: [BILLING_INQUIRY, TECHNICAL_OUTAGE, GENERAL_INQUIRY]
test_case: none
---

# Nimbus Cloud Platform Service Level Agreement (SLA) and uptime measurement

## Monthly uptime targets by Nimbus Cloud Platform subscription plan

This Nimbus Cloud Platform Service Level Agreement (SLA) governs the monthly availability commitment provided by Nimbus Systems, Inc. to paying business tenants on the **Growth** and **Enterprise** subscription plans across the US (`us-east-1`, `us-west-2`) and EU (`eu-central-1`, `eu-west-1`) regions. Tenants subscribed to the **Starter** plan receive best-effort operational availability and are not eligible for SLA service credits.

### Monthly Uptime Percentage commitments per tier

Nimbus Systems guarantees that each covered production component—**Nimbus DB** (multi-AZ clusters), **Nimbus Run** (multi-zone container control plane and worker nodes), **Nimbus Net** (regional VPC routing and NAT gateways), and **Nimbus POS Gateway** (regional payment relay)—will achieve at least the following **Monthly Uptime Percentage** during every calendar month:

| Subscription Tier | Monthly Uptime Target | Approx. Max Allowed Unscheduled Downtime (30-Day Month) | SLA Credit Eligibility |
| :--- | :--- | :--- | :--- |
| **Starter Plan** | No SLA commitment (best-effort) | N/A | Not eligible for service credits |
| **Growth Plan** | **99.90%** Monthly Uptime | **43.2 minutes** per calendar month | Eligible (10% to 30% credit tiers) |
| **Enterprise Plan** | **99.95%** Monthly Uptime | **21.6 minutes** per calendar month | Eligible (10% to 30% credit tiers) |

## How Nimbus Cloud Platform measures Monthly Uptime Percentage

### Formula for Monthly Uptime Percentage

Nimbus Cloud Platform calculates the Monthly Uptime Percentage separately for each covered service within each regional deployment (for example, `Nimbus DB - eu-central-1` for tenant `Globex Corp`) over a full calendar month using 1-minute evaluation intervals:

```text
Monthly Uptime % = ((Total Minutes in Month - Unscheduled Downtime Minutes) / Total Minutes in Month) * 100
```

A 1-minute interval is counted as an **Unscheduled Downtime Minute** for a covered Nimbus Cloud Platform service only when all customer requests to that multi-AZ resource fail with server-side errors (`HTTP 500`, `502`, `503`, `504`, `GATEWAY_TIMEOUT`, or total PostgreSQL connection refusal across both primary and standby nodes) continuously for 60 consecutive seconds, as verified by Nimbus control-plane telemetry and external synthetic health probes.

## SLA exclusions (when downtime does not count toward SLA breach)

Minutes of unavailability or degraded performance resulting from any of the following events are excluded from `Unscheduled Downtime Minutes` under the Nimbus Cloud Platform SLA:

1. **Scheduled Maintenance Windows:** Planned infrastructure upgrades announced at least **72 hours in advance** via `https://status.nimbuscloud.example.com` and email notice, provided total scheduled maintenance does not exceed **4 hours per calendar quarter**.
2. **Single-AZ Configurations:** Any Nimbus DB instance or Nimbus Run node pool deployed by the customer in a single-Availability-Zone configuration without multi-AZ failover enabled.
3. **Customer-Caused Issues and Misconfigurations:** Outages caused by customer application bugs, invalid Terraform scripts, `OOMKilled` container pods, exhausted database `max_connections` due to unpooled client traffic, expired customer SAML/TLS certificates, or administrative credential lockouts.
4. **Rate Limiting and Security Throttling:** Requests rejected with `HTTP 429 Too Many Requests` or blocked by Nimbus Security due to a DDoS attack or violation of the Acceptable Use Policy.
5. **Force Majeure Events:** Circumstances beyond Nimbus Systems' reasonable control, including widespread submarine cable cuts, upstream tier-1 ISP backbone failures, war, or government network shutdowns.

## How and by when to submit an SLA credit claim

To claim a service credit for a verified SLA breach on the Growth or Enterprise plan, a `Tenant Owner` or `Billing Administrator` must submit a formal SLA claim through **Organization Settings > Compliance & SLA > File SLA Claim** **no later than 30 calendar days after the end of the calendar month** in which the outage occurred (for example, a claim for a June outage in Q2 must be filed on or before July 30). Claims submitted after the 30-calendar-day deadline are waived. Every claim must specify the Tenant ID, affected service and region, UTC timestamps of the outage, and supporting incident IDs from `https://status.nimbuscloud.example.com`.
