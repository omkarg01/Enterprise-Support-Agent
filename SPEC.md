# Product specification — Enterprise Support Agent

> Status: draft. A coding agent must not implement product code until this specification is approved through Genesis.

## Problem

Enterprise customer support operations face high operational costs, slow resolution times, and elevated risk of non-deterministic errors when deploying generative AI. Standard open-loop conversational agents hallucinate policy details, breach compliance and tenant data boundaries, and fail to survive multi-day asynchronous workflows (such as tier-3 human specialist handoffs or customer wait periods). The enterprise requires a deterministic, durable, multi-tier cognitive agent platform that safely resolves customer requests, enforces strict data governance and RBAC, executes transactional actions under human supervision, and self-recovers from transient faults.

## Users

- **Tier-0 Anonymous Customers**: Unauthenticated visitors accessing public product documentation, FAQs, and general troubleshooting.
- **Tier-1 OTP Authenticated Customers**: Low-to-medium assurance users performing standard self-service account queries.
- **Tier-2 SSO Authenticated Enterprise Customers**: High-assurance enterprise principals managing subscriptions, billing, API keys, and sensitive account mutations.
- **Support Specialists & Escalation Engineers (HITL)**: Enterprise agents reviewing draft responses, approving high-risk transactions, and taking over escalated tickets.
- **Support Operations & Governance Admins**: Enterprise administrators monitoring SLOs, managing tenant configurations, and auditing compliance and cryptographic data shredding.

## Functional requirements

- FR-1: Decoupled API Ingress & Resumable SSE — Ingest client turns via POST 202 with client-generated idempotency keys and deliver responses via resumable Server-Sent Events (SSE) typed envelopes with sequence numbering (UA-D1, UA-D2, UA-D7).
- FR-2: Dual-Process Hybrid Orchestration — Execute deterministic LangGraph statechart FSM for SOPs/compliance workflows and route to bounded ReAct subgraphs exclusively for open-ended technical diagnostic steps (ADP-01).
- FR-3: Durable Outer Saga Management — Run ticket lifecycles, SLA timers, customer wait periods, and human approval queues within Temporal.io durable workflows that survive worker crashes and node redeployments (ADP-02, MS-D8).
- FR-4: Fast System-1 Decision Modeling — Employ TypeSafe Jev decision models for intent triage, transition guard validation, and tool selection, keeping numeric calculations and control flow logic strictly in deterministic code (ADP-05).
- FR-5: Bounded Working Memory & Context Allocation — Enforce tripartite context slot allocation dividing available token budget into 15% System Persona, 15% User Profile, 35% Grounded RAG, 25% Dialogue Buffer, and 10% Scratchpad (ADP-03).
- FR-6: Tiered Identity Assurance & Delegation — Enforce tiered principal identity (T0 anonymous, T1 OTP, T2 SSO with step-up MFA) and mint RFC 8693 on-behalf-of (`sub` + `act`) tokens for downstream system operations (UA-D3, UA-D4, UA-D9).
- FR-7: Multi-Stage Hybrid Knowledge Retrieval — Retrieve knowledge using BM25 + dense embedding Reciprocal Rank Fusion (RRF), parent-child document chunking, pre-rerank screening, and schema-validated LLM reranking (KR-D4, KR-D7, KR-D9, KR-D12).
- FR-8: Long-Term Principal Memory & Reconciliation — Store customer preferences and verified interaction facts in tenant-isolated vector storage with temporal validity bounds (`valid_from`/`valid_to`), reconciled via Jev decision models (MS-D4, MS-D12).
- FR-9: Secure Action Validation & Approval Tiers — Enforce strict tool argument provenance tracking, automatic execution for read-only tools, and mandatory human specialist approval tripwires for mutations exceeding financial thresholds ($1,000 default) or irreversible destructive actions (TA-D1, TA-D3, TA-D5).
- FR-10: Metacognitive Circuit Breaker & Escalation — Limit autonomous error correction to maximum 2 Reflexion trials verified by Jev success evaluations; immediately trip to Human-in-the-Loop with a full diagnostic bundle upon persistent failure (ADP-04, HL-D1, HL-D2).

## Non-functional requirements

- NFR-1: Latency & Responsiveness — Deliver first SSE status/progress event within 500ms; achieve P95 complete turn latency under 2.5s for cached/read queries and under 6.0s for multi-stage diagnostic turns (RP-D3).
- NFR-2: PII Governance & Data Boundary — Strip or mask all Personally Identifiable Information (PII) using Presidio token vault before LLM inference, embedding generation, or external API transmission (SG-D2, SG-D4).
- NFR-3: Multi-Tenant Data Isolation & Encryption — Guarantee strict tenant logical separation via PostgreSQL Row-Level Security (RLS); enforce envelope encryption with tenant KMS keys and per-principal Data Encryption Keys (DEKs) (DP-D2, CR-D10).
- NFR-4: GDPR Cryptographic Shredding — Support GDPR Article 17 right-to-erasure by purging principal DEKs within 24 hours from the dedicated key store, rendering encrypted database backups cryptographically unrecoverable (DP-D5, CR-D13).
- NFR-5: High Availability & Durability — Guarantee 99.9% service availability with multi-burn-rate SRE alerting, OpenTelemetry distributed tracing, and automated failover across active-active regional deployments (RP-D4, OB-D1, OB-D3).

## Constraints

- Runtime substrate must run on Python 3.11+ with Pydantic v2 data models for all inter-component contracts and state envelopes.
- Durable outer state machine must be backed by Temporal.io server cluster; inner state persistence must use PostgreSQL checkpointers.
- TypeSafe Jev API credentials must be secured via secret vault and invoked only over PII-masked envelopes.
- V1 implementation is restricted to Web channel; telephony, email, and native mobile messaging adapters are isolated to future phases (UA-D1).

## Non-goals

- V1 will not support live telephony voice streams or direct IVR integration.
- V1 will not autonomously write back unreviewed agent responses into canonical knowledge repositories; knowledge curation remains human-in-the-loop (CI-D6).
- V1 will not perform automated financial transactions exceeding tenant thresholds without human approval (TA-D5).
- V1 will not support cross-tenant shared memory or cross-tenant episodic knowledge transfer (MS-D3).

## Acceptance criteria

- AC-1: Client submitting a valid turn via `POST /api/v1/conversations/{id}/turns` receives HTTP 202 Accepted with a valid SSE stream URI within 100ms.
- AC-2: SSE stream delivers ordered, monotonically incremented event envelopes (`heartbeat`, `status`, `token`, `final`) and resumes cleanly from `Last-Event-ID` upon client reconnect.
- AC-3: Any transaction labeled as high-risk or exceeding threshold ($1,000) deterministically halts in Temporal workflow at `AWAITING_APPROVAL` and resumes only upon human specialist sign-off.
- AC-4: Input text containing credit cards, SSNs, or email addresses is tokenized into surrogate UUIDs in all logs and LLM prompts, and detokenized only at the final response egress layer.
- AC-5: Circuit breaker trips to `ESCALATED_HITL` state with structured diagnostic packet if a tool call fails 2 consecutive Reflexion attempts.
- AC-6: `genesis spec check .` executes and passes with zero template placeholders or syntactic failures.

## Risks

- Model provider rate-limiting during burst traffic: Mitigated by token bucket admission control and priority queuing (RP-D1, RP-D3).
- Small language model arithmetic inaccuracies: Mitigated by keeping numeric, date, and threshold comparisons strictly in deterministic code (ADP-05).
- Knowledge base drift or stale documentation: Mitigated by nightly batch re-indexing and instant purge queues (KR-D3, KR-D15).

## Open questions

- None. All 230 architectural decisions and 79 ADPs have been finalized, checkpointed, and verified in `docs/decisions/checkpoint.md`.
