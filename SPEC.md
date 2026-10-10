# Product specification — Enterprise-Support-Agent

> Status: draft. A coding agent must not implement product code until this specification is approved through Genesis.
> Each FR / NFR maps one-to-one to an Architectural Decision Point (ADP). The ADP document linked from each line is the detailed, binding contract; `docs/decisions/checkpoint.md` holds the reasoning and `docs/decisions/adp_groups.yaml` the decision-to-ADP mapping.
> **This is a learning project.** The ADPs describe the target production design (AWS, US/EU regions, Kubernetes, GPUs). v1 runs on the **learning deployment profile** (one free VM + free managed services, ≤ USD 10/month): where a requirement names production infrastructure, v1 satisfies it in the form given in the profile table under *Constraints*.

## Problem

Enterprise customers (B2B tenants) need support that resolves technical incidents and account / billing issues quickly, safely and auditably. Human-only support is slow and expensive; generic chatbots hallucinate, leak data across tenants, take unauthorized actions (e.g. refunds) and can't be audited. The desired outcome is an AI support agent that resolves routine and diagnostic cases end to end, takes account actions only within approval rules, escalates to humans with full context when needed, keeps each tenant's data isolated and in its region, and is measurable and improvable after launch.

## Users

- **End users of tenant organisations** (e.g. Sarah, a cloud architect at Acme): anonymous (T0), OTP-verified (T1) or SSO-authenticated (T2) users asking questions, reporting incidents and requesting account actions through the web channel.
- **Human support specialists**: receive escalations, edit drafts in the co-pilot band, take over conversations and approve actions within their rights; **senior specialists** approve amounts at or above the tenant's senior amount.
- **Tenant administrators**: configure tenant settings (approval threshold, senior amount, blackout windows, budgets, evaluation / training opt-in) and read usage reports.
- **Platform operators / on-call engineers**: run, monitor, release and recover the system in the US and EU regions.
- **Improvement / quality team**: reviews failures, maintains the failure taxonomy and ships improvements through the gated release.
- **Affected parties**: tenants' customers whose data is processed; auditors and regulators (GDPR, SOX audit trail).

## Functional requirements
### Agent Orchestration Core & Runtime

- FR-1: **ADP-01 Planning paradigm** — Hybrid dual-process statechart — deterministic LangGraph FSM for SOPs + scoped ReAct for diagnostics. Covers ADP-01. Detail: [`ADP-01`](docs/decisions/adp/00-orchestration-core/ADP-01-planning-paradigm.md).
- FR-2: **ADP-02 Workflow durability** — Temporal outer saga for durability, timers and approvals + LangGraph inner cognitive loop. Covers ADP-02. Detail: [`ADP-02`](docs/decisions/adp/00-orchestration-core/ADP-02-workflow-durability.md).
- FR-3: **ADP-03 Context engineering** — Tripartite slot allocator — 15 % system · 15 % profile · 35 % knowledge · 25 % dialogue · 10 % scratchpad. Covers ADP-03. Detail: [`ADP-03`](docs/decisions/adp/00-orchestration-core/ADP-03-context-engineering.md).
- FR-4: **ADP-04 Error recovery & replanning** — Dual-process circuit breaker — max 2 Reflexion trials, a Jev step-success check, then human handoff. Covers ADP-04. Detail: [`ADP-04`](docs/decisions/adp/00-orchestration-core/ADP-04-error-recovery-replanning.md).
- FR-5: **ADP-05 Decision model (Jev)** — Jev (TypeSafe) at triage, FSM guards and tool selection; code owns control flow; numbers and dates stay in code. Covers ADP-05. Detail: [`ADP-05`](docs/decisions/adp/00-orchestration-core/ADP-05-decision-model-jev.md).

### [1] User & Application

- FR-6: **UA-ADP-01 Channels & API transport** — Web-only v1; POST 202 with an idempotency key + a resumable SSE event stream. Covers UA-D1, UA-D2. Detail: [`UA-ADP-01`](docs/decisions/adp/01-user-application/UA-ADP-01-channels-api-transport.md).
- FR-7: **UA-ADP-02 Identity & downstream tokens** — Tiered identity (anonymous → OTP → SSO, step-up on demand) + RFC 8693 on-behalf-of tokens valid 24 h. Covers UA-D3, UA-D4, UA-D9. Detail: [`UA-ADP-02`](docs/decisions/adp/01-user-application/UA-ADP-02-identity-downstream-tokens.md).
- FR-8: **UA-ADP-03 Sessions & conversation scope** — Session → conversation → case; sessions end at 30 min idle / 12 h; any authenticated request keeps them alive. Covers UA-D5, UA-D6, UA-Q3. Detail: [`UA-ADP-03`](docs/decisions/adp/01-user-application/UA-ADP-03-sessions-conversation-scope.md).
- FR-9: **UA-ADP-04 Response contract & delivery** — Typed event envelope; replies buffered until checks pass with status events meanwhile; deferred answers go to the inbox. Covers UA-D7, UA-D8, UA-Q2. Detail: [`UA-ADP-04`](docs/decisions/adp/01-user-application/UA-ADP-04-response-contract-delivery.md).

### [2] Knowledge & Retrieval

- FR-10: **KR-ADP-01 Knowledge sources & content labelling** — Curated + operational + raw tickets; per-chunk audience label, internal by default; human / agent provenance. Covers KR-D1, KR-D2, KR-D13, KR-D16. Detail: [`KR-ADP-01`](docs/decisions/adp/02-knowledge-retrieval/KR-ADP-01-knowledge-sources-labelling.md).
- FR-11: **KR-ADP-02 Ingestion & freshness** — Nightly batch re-index + instant purge for deletions; deletion-aware ingestion with a post-batch check. Covers KR-D3, KR-D15. Detail: [`KR-ADP-02`](docs/decisions/adp/02-knowledge-retrieval/KR-ADP-02-ingestion-freshness.md).
- FR-12: **KR-ADP-03 Indexing & tenant isolation** — Structure-aware parent-child chunks; shared public index + per-tenant private index; copied ACLs + live check for restricted docs. Covers KR-D4, KR-D5, KR-D6. Detail: [`KR-ADP-03`](docs/decisions/adp/02-knowledge-retrieval/KR-ADP-03-indexing-tenant-isolation.md).
- FR-13: **KR-ADP-04 Retrieval & ranking pipeline** — Standalone rewrite → BM25 + dense (RRF) → screening → validated LLM reranker → always top-10 parent sections. Covers KR-D7, KR-D8, KR-D9, KR-D10, KR-D12, KR-D14. Detail: [`KR-ADP-04`](docs/decisions/adp/02-knowledge-retrieval/KR-ADP-04-retrieval-ranking-pipeline.md).
- FR-14: **KR-ADP-05 Retrieval evaluation** — Offline golden set + online implicit signals + sampled LLM-judge scoring. Covers KR-D11. Detail: [`KR-ADP-05`](docs/decisions/adp/02-knowledge-retrieval/KR-ADP-05-retrieval-evaluation.md).

### [3] Memory & State

- FR-15: **MS-ADP-01 Conversation & case model** — Session → conversation → case (Jev links cases); last N turns verbatim + rolling summary + pinned items. Covers MS-D1, MS-D2. Detail: [`MS-ADP-01`](docs/decisions/adp/03-memory-state/MS-ADP-01-conversation-case-model.md).
- FR-16: **MS-ADP-02 Long-term fact model** — Per-user facts only, with validity dates; account facts are never stored — always read from the CRM. Covers MS-D3, MS-D4, MS-D6. Detail: [`MS-ADP-02`](docs/decisions/adp/03-memory-state/MS-ADP-02-long-term-fact-model.md).
- FR-17: **MS-ADP-03 Fact write path** — Extract at resolution from user turns + allow-listed tool fields; skip plans and third-party facts; Jev reconciles on masked text. Covers MS-D5, MS-D12, MS-D15, MS-D16, MS-D17, MS-D19. Detail: [`MS-ADP-03`](docs/decisions/adp/03-memory-state/MS-ADP-03-fact-write-path.md).
- FR-18: **MS-ADP-04 Agent state & durability** — LangGraph checkpointer is the truth; checkpoints per node and around side effects; versioned with migrations; re-check after waits > 1 h. Covers MS-D7, MS-D8, MS-D9, MS-D13, MS-D18. Detail: [`MS-ADP-04`](docs/decisions/adp/03-memory-state/MS-ADP-04-agent-state-durability.md).
- FR-19: **MS-ADP-05 Retention & erasure** — Fixed TTL per memory type; back-office erasure / correction in v1; an erasure inventory covering every store. Covers MS-D10, MS-D11, MS-D14. Detail: [`MS-ADP-05`](docs/decisions/adp/03-memory-state/MS-ADP-05-retention-erasure.md).

### [4] Tools & Actions

- FR-20: **TA-ADP-01 Tool registry & selection** — Plain Python tools with a reviewed risk class; code filters by tier / role, Jev shortlists and picks. Covers TA-D1, TA-D2, TA-D16. Detail: [`TA-ADP-01`](docs/decisions/adp/04-tools-actions/TA-ADP-01-tool-registry-selection.md).
- FR-21: **TA-ADP-02 Argument safety** — They must trace to the user's words or to allow-listed structured tool fields; never free LLM text. Covers TA-D3, TA-D13. Detail: [`TA-ADP-02`](docs/decisions/adp/04-tools-actions/TA-ADP-02-argument-safety.md).
- FR-22: **TA-ADP-03 Action validation & approval tiers** — Reads and low-risk writes automatic; financial ≥ tenant threshold (default $1,000), destructive or irreversible → approval; Jev gate can only tighten. Covers TA-D5, TA-D6, TA-D7, TA-D14. Detail: [`TA-ADP-03`](docs/decisions/adp/04-tools-actions/TA-ADP-03-action-validation-approval.md).
- FR-23: **TA-ADP-04 Execution, credentials & isolation** — On-behalf-of tokens or a scoped service account + user check; worker pool per system; output screened; safe retries + breakers. Covers TA-D4, TA-D9, TA-D10, TA-D11. Detail: [`TA-ADP-04`](docs/decisions/adp/04-tools-actions/TA-ADP-04-execution-credentials-isolation.md).
- FR-24: **TA-ADP-05 Transactional integrity & audit** — Temporal sagas with idempotent compensations; no-undo steps last and approved; append-only audit with crypto-shredding. Covers TA-D8, TA-D17, TA-D18, TA-D12, TA-D15. Detail: [`TA-ADP-05`](docs/decisions/adp/04-tools-actions/TA-ADP-05-transactional-integrity-audit.md).

### [5] Multi-Agent & Communication

- FR-25: **MA-ADP-01 Topology & specialist set** — A coordinator + Generalist, Billing, Technical, Account & Ops specialists as LangGraph subgraphs; static registry. Covers MA-D1, MA-D2, MA-D10, MA-D11. Detail: [`MA-ADP-01`](docs/decisions/adp/05-multi-agent-communication/MA-ADP-01-topology-specialist-set.md).
- FR-26: **MA-ADP-02 Delegation & limits** — Jev picks the lead and extra specialists; parallel if independent; depth 1, ≤ 5 specialists, 2 steps each, 6 per turn. Covers MA-D3, MA-D6, MA-D8. Detail: [`MA-ADP-02`](docs/decisions/adp/05-multi-agent-communication/MA-ADP-02-delegation-limits.md).
- FR-27: **MA-ADP-03 Inter-agent contracts & context** — Typed task / result contracts via the coordinator; a brief plus on-demand reads of the conversation. Covers MA-D4, MA-D5. Detail: [`MA-ADP-03`](docs/decisions/adp/05-multi-agent-communication/MA-ADP-03-inter-agent-contracts-context.md).
- FR-28: **MA-ADP-04 Specialist permissions & writes** — Own identity and allow-list; low-risk writes directly only when running alone; higher-risk writes proposed; actions reported only after verification. Covers MA-D9, MA-D14, MA-D18. Detail: [`MA-ADP-04`](docs/decisions/adp/05-multi-agent-communication/MA-ADP-04-specialist-permissions-writes.md).
- FR-29: **MA-ADP-05 Merging & the single voice** — Jev scores conflicting claims, numbers go to a human; one merged reply in one voice from the coordinator, checked for redirects. Covers MA-D7, MA-D16, MA-D12, MA-D13, MA-D15, MA-D17. Detail: [`MA-ADP-05`](docs/decisions/adp/05-multi-agent-communication/MA-ADP-05-merging-single-voice.md).

### [6] Safety, Security & Governance

- FR-30: **SG-ADP-01 Input screening & injection defence** — Self-hosted Llama Guard 3 + injection classifier with pass / review / block bands, plus spotlighting; hostility via persona only. Covers SG-D1, SG-D2, SG-D3, SG-D7. Detail: [`SG-ADP-01`](docs/decisions/adp/06-safety-security-governance/SG-ADP-01-input-screening-injection-defence.md).
- FR-31: **SG-ADP-02 PII protection** — Reversible tokenization with a vault and global deterministic tokens; real values restored only into needs_pii fields; degrade if the vault is down. Covers SG-D4, SG-D5, SG-D16, SG-D18. Detail: [`SG-ADP-02`](docs/decisions/adp/06-safety-security-governance/SG-ADP-02-pii-protection.md).
- FR-32: **SG-ADP-03 Output safety** — Leakage and URL / markdown checks + a rule-based promise check; no tone check. Covers SG-D6, SG-D15. Detail: [`SG-ADP-03`](docs/decisions/adp/06-safety-security-governance/SG-ADP-03-output-safety.md).
- FR-33: **SG-ADP-04 Authorization & tool permissions** — Central Cedar policies (tickets by role, blackout windows); writes fail closed, reads use a short cache when it's down. Covers SG-D8, SG-D9, SG-D10, SG-D17. Detail: [`SG-ADP-04`](docs/decisions/adp/06-safety-security-governance/SG-ADP-04-authorization-tool-permissions.md).
- FR-34: **SG-ADP-05 Governance, retention & providers** — Every automated decision recorded with a "why" view; AI disclosure + human always available; 2-year transcript archive; standard provider terms. Covers SG-D11, SG-D12, SG-D13, SG-D14. Detail: [`SG-ADP-05`](docs/decisions/adp/06-safety-security-governance/SG-ADP-05-governance-retention-providers.md).

### [12] Human-in-the-Loop

- FR-35: **HL-ADP-01 Confidence gate** — Jev checks support and relevance; send ≥ 0.90, co-pilot 0.50–0.90, hand off below; money / SLA figures always reviewed. Covers HL-D1, HL-D2, HL-D13. Detail: [`HL-ADP-01`](docs/decisions/adp/12-human-in-the-loop/HL-ADP-01-confidence-gate.md).
- FR-36: **HL-ADP-02 Escalation & queues** — One typed escalation packet; skills-based queues with SLA timers; aging alerts; undecided approvals cancelled after 3 days. Covers HL-D3, HL-D9, HL-D12. Detail: [`HL-ADP-02`](docs/decisions/adp/12-human-in-the-loop/HL-ADP-02-escalation-queues.md).
- FR-37: **HL-ADP-03 Approval** — Rights by role and amount (senior ≥ tenant amount, default $5,000), approver ≠ handler; card with evidence, dry-run and field-by-field confirmation. Covers HL-D4, HL-D5. Detail: [`HL-ADP-03`](docs/decisions/adp/12-human-in-the-loop/HL-ADP-03-approval-workflows.md).
- FR-38: **HL-ADP-04 Handoff & waiting** — Immediate handoff on request; cold handoff once a human picks up; wait estimate and cancel option; human replies checked as warnings. Covers HL-D6, HL-D7, HL-D8, HL-D14. Detail: [`HL-ADP-04`](docs/decisions/adp/12-human-in-the-loop/HL-ADP-04-handoff-waiting-experience.md).
- FR-39: **HL-ADP-05 Feedback & console** — Approve / reject with reason codes; self-hosted Retool in each region. Covers HL-D10, HL-D11. Detail: [`HL-ADP-05`](docs/decisions/adp/12-human-in-the-loop/HL-ADP-05-feedback-console.md).

### [11] Cost & Resource Management

- FR-40: **CR-ADP-01 Model tier routing** — A Jev difficulty score picks self-hosted / mid-tier / frontier; failed steps cascade one tier up; the self-hosted tier also serves easy turns. Covers CR-D1, CR-D2, CR-D9. Detail: [`CR-ADP-01`](docs/decisions/adp/11-cost-resource-management/CR-ADP-01-model-tier-routing.md).
- FR-41: **CR-ADP-02 Token & context economy** — Turn and daily conversation budgets; provider prompt caching + passage compression; cheaper tiers for helpers where proven. Covers CR-D5, CR-D6, CR-D8. Detail: [`CR-ADP-02`](docs/decisions/adp/11-cost-resource-management/CR-ADP-02-token-context-economy.md).
- FR-42: **CR-ADP-03 Answer cache** — Per-tenant cache of public-KB-only answers for 7 days, keyed by question + version + KB index; Jev decides eligibility. Covers CR-D3, CR-D14. Detail: [`CR-ADP-03`](docs/decisions/adp/11-cost-resource-management/CR-ADP-03-answer-cache.md).
- FR-43: **CR-ADP-04 Budgets & cost governance** — Exact cost from the usage counter, reconciled monthly; tenant budgets with a soft cap; reports for tenant admins; ≤ 10 % cost rise per release. Covers CR-D4, CR-D7, CR-D11, CR-D12. Detail: [`CR-ADP-04`](docs/decisions/adp/11-cost-resource-management/CR-ADP-04-budgets-cost-governance.md).
- FR-44: **CR-ADP-05 Key-management cost** — Envelope encryption under per-tenant KMS keys; wrapped keys in a separate store with 1-day backups. Covers CR-D10, CR-D13. Detail: [`CR-ADP-05`](docs/decisions/adp/11-cost-resource-management/CR-ADP-05-key-management-cost.md).

## Non-functional requirements
### [7] Data & Persistence

- NFR-1: **DP-ADP-01 Store topology & residency** — Postgres + Qdrant, deployed per region (US, EU); each tenant pinned to one region. Covers DP-D1, DP-D10. Detail: [`DP-ADP-01`](docs/decisions/adp/07-data-persistence/DP-ADP-01-store-topology-residency.md).
- NFR-2: **DP-ADP-02 Tenant isolation & encryption** — Shared tables with forced row-level security; envelope encryption with per-user data keys; an isolated token vault. Covers DP-D2, DP-D4, DP-D5. Detail: [`DP-ADP-02`](docs/decisions/adp/07-data-persistence/DP-ADP-02-tenant-isolation-encryption.md).
- NFR-3: **DP-ADP-03 Audit, archive & settings data** — Append-only audit tables; transcript archive behind a restricted role; platform definitions in code, tenant settings in the database. Covers DP-D3, DP-D8, DP-D12. Detail: [`DP-ADP-03`](docs/decisions/adp/07-data-persistence/DP-ADP-03-audit-archive-settings.md).
- NFR-4: **DP-ADP-04 Data movement & stream retention** — No event bus — direct writes; 7-day event-log replay; versioned raw snapshots per nightly batch. Covers DP-D6, DP-D7, DP-D11. Detail: [`DP-ADP-04`](docs/decisions/adp/07-data-persistence/DP-ADP-04-data-movement-stream-retention.md).
- NFR-5: **DP-ADP-05 Erasure, backups & restore** — Erasure fan-out as a Temporal workflow with a completion check; snapshots cleaned; backups expire after 35 days; restore risk accepted. Covers DP-D9, DP-D13, DP-D14, DP-D15. Detail: [`DP-ADP-05`](docs/decisions/adp/07-data-persistence/DP-ADP-05-erasure-backups-restore.md).

### [8] Evaluation & Experimentation

- NFR-6: **EV-ADP-01 Datasets & data governance** — Framework episodes + seeds + opted-in production conversations, tokenized, regional and erasable. Covers EV-D1, EV-D10, EV-D15. Detail: [`EV-ADP-01`](docs/decisions/adp/08-evaluation-experimentation/EV-ADP-01-datasets-data-governance.md).
- NFR-7: **EV-ADP-02 Scoring & calibration** — Assertions + a cross-family LLM judge calibrated on human labels; thresholds calibrated per route each release. Covers EV-D2, EV-D9. Detail: [`EV-ADP-02`](docs/decisions/adp/08-evaluation-experimentation/EV-ADP-02-scoring-calibration.md).
- NFR-8: **EV-ADP-03 Suites, environment & cadence** — Component suites + a risk-tier end-to-end suite; staging, else fakes or recordings; smoke per commit, full nightly. Covers EV-D3, EV-D4, EV-D6, EV-D14. Detail: [`EV-ADP-03`](docs/decisions/adp/08-evaluation-experimentation/EV-ADP-03-suites-environment-cadence.md).
- NFR-9: **EV-ADP-04 Release gate & approval evidence** — Component thresholds, zero risk-tier violations, pass^k, cost / latency budgets, live CSAT / FCR / CES; evidence only for routes covered. Covers EV-D5, EV-D13. Detail: [`EV-ADP-04`](docs/decisions/adp/08-evaluation-experimentation/EV-ADP-04-release-gate-approval-evidence.md).
- NFR-10: **EV-ADP-05 Live evaluation & experiments** — Implicit signals + sampled judge scoring; shadow (writes never executed) and canary on read-only routes; replies buffered until checked. Covers EV-D7, EV-D8, EV-D12, EV-D11. Detail: [`EV-ADP-05`](docs/decisions/adp/08-evaluation-experimentation/EV-ADP-05-live-evaluation-experiments.md).

### [9] Observability & Monitoring

- NFR-11: **OB-ADP-01 Instrumentation & trace pipeline** — OpenTelemetry + SDK spans; collector scrubs PII; tail sampling keeps errors, escalations, risk-tier and slow traces with full content. Covers OB-D1, OB-D3, OB-D4, OB-D14. Detail: [`OB-ADP-01`](docs/decisions/adp/09-observability-monitoring/OB-ADP-01-instrumentation-trace-pipeline.md).
- NFR-12: **OB-ADP-02 Telemetry storage, retention & erasure** — Jaeger + OpenSearch per region; JSON logs with trace IDs; 7-day retention; delete by user / conversation ID. Covers OB-D2, OB-D5, OB-D6, OB-D12. Detail: [`OB-ADP-02`](docs/decisions/adp/09-observability-monitoring/OB-ADP-02-telemetry-storage-retention-erasure.md).
- NFR-13: **OB-ADP-03 Service levels & alerting** — Internal SLOs (no tenant-facing targets) with burn-rate alerts + specific hand-off alerts; Temporal UI for runs. Covers OB-D7, OB-D8, OB-D10. Detail: [`OB-ADP-03`](docs/decisions/adp/09-observability-monitoring/OB-ADP-03-service-levels-alerting.md).
- NFR-14: **OB-ADP-04 Token & cost telemetry** — Per tenant / conversation / component, estimated from kept traces (exact numbers come from Cost's counter). Covers OB-D9, OB-D13. Detail: [`OB-ADP-04`](docs/decisions/adp/09-observability-monitoring/OB-ADP-04-token-cost-telemetry.md).
- NFR-15: **OB-ADP-05 Failure classification** — Rules on error codes and decision records, then a Jev Choice over the failure taxonomy. Covers OB-D11. Detail: [`OB-ADP-05`](docs/decisions/adp/09-observability-monitoring/OB-ADP-05-failure-classification.md).

### [10] Reliability / Performance / Scale

- NFR-16: **RP-ADP-01 Admission control** — Limits per tenant, user, conversation and tokens/min (exact counter); over the limit → knowledge-base-only answer, then 429; one active turn per conversation. Covers RP-D1, RP-D2, RP-D3, RP-D14. Detail: [`RP-ADP-01`](docs/decisions/adp/10-reliability-performance-scale/RP-ADP-01-admission-control.md).
- NFR-17: **RP-ADP-02 Dependency failures & fallbacks** — Self-hosted fallback LLM; fallback classifier for Jev; tool requests held up to 24 h; one retry layer with retry budgets. Covers RP-D4, RP-D5, RP-D7, RP-D13. Detail: [`RP-ADP-02`](docs/decisions/adp/10-reliability-performance-scale/RP-ADP-02-dependency-failures-fallbacks.md).
- NFR-18: **RP-ADP-03 Bursts, latency & capacity** — Request coalescing + incident mode; first status ≤ 1 s, p95 targets per route; autoscaling with a pre-warmed minimum. Covers RP-D6, RP-D8, RP-D9. Detail: [`RP-ADP-03`](docs/decisions/adp/10-reliability-performance-scale/RP-ADP-03-bursts-latency-capacity.md).
- NFR-19: **RP-ADP-04 Disaster recovery & regions** — Multi-AZ inside each region, no cross-region failover; point-in-time recovery; monthly drills into a warm standby. Covers RP-D10, RP-D11, RP-D15. Detail: [`RP-ADP-04`](docs/decisions/adp/10-reliability-performance-scale/RP-ADP-04-disaster-recovery-regions.md).
- NFR-20: **RP-ADP-05 Resilience testing** — Load tests before every release + chaos tests on staging. Covers RP-D12. Detail: [`RP-ADP-05`](docs/decisions/adp/10-reliability-performance-scale/RP-ADP-05-resilience-testing.md).

### [13] Testing & Quality

- NFR-21: **TQ-ADP-01 Testing model-driven logic** — Models mocked; scripted and property-based tests of the safety invariants; a fixed injection set. Covers TQ-D1, TQ-D2, TQ-D3. Detail: [`TQ-ADP-01`](docs/decisions/adp/13-testing-quality/TQ-ADP-01-testing-model-driven-logic.md).
- NFR-22: **TQ-ADP-02 Contracts & policy checks** — Contract tests for shared formats; Cedar, RLS and tool-declaration checks in CI; end-to-end trace tests. Covers TQ-D4, TQ-D5, TQ-D11. Detail: [`TQ-ADP-02`](docs/decisions/adp/13-testing-quality/TQ-ADP-02-contracts-policy-checks.md).
- NFR-23: **TQ-ADP-03 End-to-end tests & test data** — One test per framework scenario on staging; synthetic personas + opted-in tokenized samples. Covers TQ-D6, TQ-D12. Detail: [`TQ-ADP-03`](docs/decisions/adp/13-testing-quality/TQ-ADP-03-end-to-end-tests-data.md).
- NFR-24: **TQ-ADP-04 Merge gate & flaky tests** — Unit + integration + contracts + policy + EV smoke; retries allowed except for safety tests. Covers TQ-D8, TQ-D9, TQ-D13. Detail: [`TQ-ADP-04`](docs/decisions/adp/13-testing-quality/TQ-ADP-04-merge-gates-flaky-tests.md).
- NFR-25: **TQ-ADP-05 Migrations & non-functional tests** — SQL migrations by review; old checkpoints must load through migrations; load + chaos before each release. Covers TQ-D7, TQ-D14, TQ-D10. Detail: [`TQ-ADP-05`](docs/decisions/adp/13-testing-quality/TQ-ADP-05-migrations-non-functional-tests.md).

### [14] Deployment & LLMOps

- NFR-26: **DL-ADP-01 Environments & infrastructure** — Dev + one US staging + production per region on Kubernetes with IaC; only US samples in staging. Covers DL-D1, DL-D9, DL-D12. Detail: [`DL-ADP-01`](docs/decisions/adp/14-deployment-llmops/DL-ADP-01-environments-infrastructure.md).
- NFR-27: **DL-ADP-02 Release tracks & cadence** — Code deploys continuously; prompts, Jev questions, thresholds and model config are held for a scheduled gated release. Covers DL-D2, DL-D10. Detail: [`DL-ADP-02`](docs/decisions/adp/14-deployment-llmops/DL-ADP-02-release-tracks-cadence.md).
- NFR-28: **DL-ADP-03 Rollout & rollback** — Code all at once per region; behaviour and model releases shadow → canary → all; manual rollback. Covers DL-D4, DL-D5. Detail: [`DL-ADP-03`](docs/decisions/adp/14-deployment-llmops/DL-ADP-03-rollout-rollback.md).
- NFR-29: **DL-ADP-04 Versioning of models, indexes & workflows** — Latest aliases for LLM tiers, embedding model and Jev pinned; in-place re-index; Continue-As-New for in-flight workflows. Covers DL-D3, DL-D6, DL-D11. Detail: [`DL-ADP-04`](docs/decisions/adp/14-deployment-llmops/DL-ADP-04-versioning-models-indexes-workflows.md).
- NFR-30: **DL-ADP-05 Secrets & key custody** — Secrets manager per region with scheduled rotation; the shared token key stored per region and not rotated. Covers DL-D7, DL-D8. Detail: [`DL-ADP-05`](docs/decisions/adp/14-deployment-llmops/DL-ADP-05-secrets-key-custody.md).

### [15] Continuous Improvement

- NFR-31: **CI-ADP-01 Feedback signals** — Thumbs, reason codes, implicit signals and a post-resolution survey; free text classified and ranked by Jev. Covers CI-D1, CI-D11. Detail: [`CI-ADP-01`](docs/decisions/adp/15-continuous-improvement/CI-ADP-01-feedback-signals.md).
- NFR-32: **CI-ADP-02 Failure analysis** — Hierarchical taxonomy in code; weekly reviews by volume × severity; postmortems for safety or financial incidents. Covers CI-D2, CI-D3. Detail: [`CI-ADP-02`](docs/decisions/adp/15-continuous-improvement/CI-ADP-02-failure-analysis.md).
- NFR-33: **CI-ADP-03 Improvement levers** — Prompts, Jev questions, thresholds, tools, KB articles (agent drafts, human publishes), curated few-shot, per-region fine-tuning. Covers CI-D4, CI-D5, CI-D6. Detail: [`CI-ADP-03`](docs/decisions/adp/15-continuous-improvement/CI-ADP-03-improvement-levers.md).
- NFR-34: **CI-ADP-04 Validation & approval of changes** — Every failure becomes a test first; gate + before / after + read-only A/B; normal code review. Covers CI-D7, CI-D8, CI-D10. Detail: [`CI-ADP-04`](docs/decisions/adp/15-continuous-improvement/CI-ADP-04-validation-approval-changes.md).
- NFR-35: **CI-ADP-05 Long-term evolution & retraining** — Monthly user-distribution check; quarterly owned-risk review; quarterly retraining without erased users' data. Covers CI-D9, CI-D12. Detail: [`CI-ADP-05`](docs/decisions/adp/15-continuous-improvement/CI-ADP-05-long-term-evolution-retraining.md).

### v1 deployment profile

- NFR-36: **Learning deployment profile** — v1 runs on one Oracle Cloud Always Free ARM VM (Docker Compose) plus free managed services, as listed in the profile table under *Constraints*; total monthly spend stays ≤ USD 10, enforced by provider spend limits (Anthropic console limit; no paid plan on any other service).

## Constraints

- **Purpose and budget:** learning project, not sold to businesses; total spend ≤ **USD 10/month** (NFR-36).
- **Target design vs. v1 runtime:** the ADPs keep the production target (AWS in US + EU, Kubernetes, multi-AZ, GPUs, KMS). v1 implements every requirement on the learning profile below; capabilities that only exist at production scale are listed under *Non-goals*.
- **Models:** free tiers by default: Groq / Gemini free tiers (or Ollama on the VM) serve the easy and mid-difficulty turns, the fallback and most evaluation; Anthropic Claude serves only the hardest turns (frontier tier) under a hard USD 10 console spend limit, and when the limit is reached those turns fall back to the best free model; **Jev (TypeSafe)** for decision points (free tier if offered, else the RP-D5 fallback LLM classifier); the evaluation judge comes from a non-Claude family (Gemini free tier); screening via Llama Guard 3 1B on Ollama.
- **Runtime stack:** Python; LangGraph (inner loop, Postgres checkpointer) inside Temporal (outer workflows); Qdrant; Cedar via open-source `cedarpy`; OpenTelemetry; Streamlit specialist console; Docker Compose. All images must run on **ARM64** (the Oracle free VM is Ampere ARM).

**Learning deployment profile (v1 form of the production infrastructure):**

| Production target (ADPs) | v1 form | Affects |
| :--- | :--- | :--- |
| AWS, US + EU regions, tenant pinned to a region | One deployment on an Oracle Cloud Always Free ARM VM (4 OCPU, 24 GB); `tenant.region` still stored and enforced in code, all tenants on the one deployment | DP-ADP-01, RP-ADP-04 |
| EKS / Kubernetes + IaC; dev + staging + production | Docker Compose; **dev** = laptop, **staging / production** = the VM; Compose files and a VM setup script are the infrastructure as code | DL-ADP-01 |
| Multi-AZ, warm standby, point-in-time recovery | Daily `pg_dump` to R2 + a scripted restore drill on the laptop; no standby | RP-ADP-04, DP-ADP-05 |
| RDS Postgres ×3 (main, vault, key store) | Neon or Supabase free Postgres, with separate databases / roles for vault and key store (or Postgres on the VM) | DP-ADP-02, SG-ADP-02, CR-ADP-05 |
| Temporal HA cluster | `temporal server start-dev` on the VM (SQLite-backed) | ADP-02, TA-ADP-05 |
| Qdrant cluster | Qdrant Cloud free cluster (1 GB) or Qdrant on the VM | KR-ADP-03 |
| KMS + Secrets Manager, per-tenant KMS keys | OpenBao (dev / file storage) on the VM for master keys and secrets; envelope encryption in code via `cryptography`; GitHub Actions secrets for CI | DP-ADP-02, CR-ADP-05, DL-ADP-05 |
| S3 (blobs, snapshots, backups) | Cloudflare R2 free (10 GB, S3-compatible) | DP-ADP-04, DP-ADP-05 |
| Rate-limit / usage counters | Upstash Redis free | RP-ADP-01 |
| Self-hosted GPU LLM (third tier, fallback) | Groq / Gemini free tiers, Ollama small model on the VM as last resort | CR-ADP-01, RP-ADP-02 |
| Llama Guard 3 + injection classifier on GPU | Llama Guard 3 1B + Prompt Guard on Ollama / CPU | SG-ADP-01 |
| Jaeger + OpenSearch, 7-day retention | Jaeger all-in-one on the VM (in-memory) + Langfuse Cloud or Grafana Cloud free tier; JSON logs on disk | OB-ADP-02 |
| Retool per region | Streamlit on Hugging Face Spaces (free) or on the VM | HL-ADP-05 |
| Web channel | Next.js / static frontend on Vercel free; API behind Cloudflare | UA-ADP-01 |
| Load + chaos tests at production scale | Small load test (Locust) and fault injection (stopping containers) against the VM | RP-ADP-05, TQ-ADP-05 |
| Per-region fine-tuning on GPUs | Not run in v1 (data and pipeline only); see *Non-goals* | CI-ADP-03 |
- **Trust boundaries:** models see PII tokens only; untrusted text (messages, passages, tool output) is screened before any LLM reads it; tools act with on-behalf-of user authority or a scoped service account plus an agent-side check; Jev and model providers receive only masked input.
- **Legal / compliance:** GDPR erasure across every store (backups within 35 days); 2-year transcript archive; append-only audit with crypto-shredding; AI disclosure and human review for significant decisions; enterprise contracts must reflect accepted risks (no cross-region failover, no tenant-facing SLOs).
- **Delivery:** ordered milestones, each a set of bounded tasks with executable gates, defined in the Genesis plan. Behaviour changes (prompts, Jev questions, thresholds, policies, model config) ship only through the scheduled gated release.

## Non-goals

- Channels other than web in v1 (no Slack, Teams, mobile SDK or email ingress); no email notifications for deferred answers.
- Cross-region failover; tenant-facing uptime or latency SLOs.
- A self-service "what the agent remembers" UI (back-office erasure and correction only in v1).
- MCP tool servers, third-party or remote agents (A2A), or a runtime-editable prompt registry.
- A cross-tenant answer cache, or caching any answer that depends on account data.
- Numeric business targets (deflection, CSAT, FCR) at launch; they are set after one month of production data.
- Tone checking of replies, and hostility handling beyond persona guidance.
- In v1 (learning profile): real multi-region deployment, multi-AZ, warm standby, GPU serving, HSM/KMS key custody, production-scale load, and actually running per-region fine-tuning. These remain the target design in the ADPs.

## Acceptance criteria

- AC-1: Framework scenario 1 (expert API user, routine fast path) passes its end-to-end test on staging.
- AC-2: Framework scenario 2 (panicked non-technical user) passes: the agent asks a clarifying question instead of guessing.
- AC-3: Framework scenario 3 (high-risk financial dispute) passes: no money moves before human approval, and no success is claimed before read-back.
- AC-4: Framework scenario 4 (mid-flight state invalidation) passes: a resumed case re-checks pre-conditions before any side effect.
- AC-5: Framework scenario 5 (adversarial ingress attack, e.g. Base64 injection) passes: the attack is blocked or contained before reaching a tool.
- AC-6: Framework scenario 6 (tool degradation, breaker trip) passes as decided: the request is held and later delivered to the inbox, or handed to a human after 24 h; no cached account facts are served.
- AC-7: Framework scenario 7 (multi-specialist audit) passes: one merged reply, and numeric disagreements go to a human.
- AC-8: Framework scenario 8 (rapid duplicate messages) passes as decided: one turn runs, and messages sent while it runs are rejected with a "still working" response.
- AC-9: The EV-D5 release gate passes: component thresholds met, zero safety violations and pass^k on the risk tier, cost and latency budgets met.
- AC-10: Property-based tests show no financial write at or above the tenant threshold executes without approval from someone other than the conversation's handler.
- AC-11: Isolation tests show no API, tool, retrieval or memory read returns another tenant's data, and every tenant table enforces row-level security.
- AC-12: For a seeded test user, the erasure workflow reports completion and every store in the erasure inventory returns no readable personal data for that user.
- AC-13: Under the release load test on the VM, the first status event arrives within 1 s and final-reply p95 is within 5 s (FAQ), 20 s (diagnostics) and 45 s (multi-specialist) for turns without human approval, served by API-hosted model tiers.
- AC-14: A test conversation seeded with PII shows no raw PII in any model, Jev or provider request, or in any stored trace or log.
- AC-15: The whole stack starts on a fresh Oracle Always Free VM from the repository (setup script + `docker compose up`) with no paid service, and the month's bills across all providers total ≤ USD 10.

## Risks

- **Jev dependency:** a single hosted vendor; outages fall back to an LLM classifier (RP-D5); calibration can drift on version upgrades (version pinned, DL-D3).
- **Human workload:** approvals, co-pilot reviews of money / SLA drafts and handoffs may exceed staffing (HL KU2); queue metrics alert early.
- **Accepted data risks:** restores can resurrect erased data (DP-D14); global tokens link a person across tenants (SG UU1); the unrotated shared token key can re-identify tokens if stolen (DL-D8); audit tables are not tamper-evident (DP-D3).
- **Quality drift:** LLM tiers use "latest" aliases and can change between releases (DL-D3); mitigated by live sampling and exact cost tracking.
- **Untested integrations:** integration failures outside the risk tier aren't end-to-end tested (EV-Q1); stateful fakes can drift from real systems (EV KU5).
- **Region loss** means downtime for that region's tenants (RP-D10).
- **Free-tier limits (v1):** free tiers change or get rate-limited; Oracle free capacity can be unavailable and idle accounts reclaimed; Neon / Supabase pause idle databases; Hugging Face Spaces sleep. Each service sits behind an interface so it can be swapped, and the VM can also run Postgres and Qdrant locally.
- **Single VM (v1):** one machine is a single point of failure; backups go to R2 and restores are drilled on the laptop.
- Full owned-risk lists per component are in `checkpoint.md` §5–§19 and are reviewed quarterly (CI-D9).

## Open questions

- Does TypeSafe offer a free tier for Jev? If not, v1 uses the RP-D5 fallback LLM classifier at the decision points until a Jev key is available.
- Exact Claude model IDs per tier, and which free model (Groq / Gemini / Ollama) serves the third tier.
- `docs/analysis/user_evaluation_framework.md` scenarios 6 and 8 describe behaviour superseded by MS-D6 / RP-D7 and RP-D3; AC-6 and AC-8 follow the decisions. Should the framework text be updated?
- Milestone boundaries and order (to be proposed in the plan).
