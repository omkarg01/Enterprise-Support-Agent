# Enterprise Support Agent: Architectural Decision Checkpoint

> **Status:** ✅ ACTIVE & CHECKPOINTED (Component [ 5 ] Finalized)  
> **Component Under Review:** Component [ 5 ] — Agent Orchestration Core & Runtime  
> **Genesis Readiness:** READY FOR SCAFFOLDING (Initial Specifications Checkpointed)  
> **Reference Master Report:** [`Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md`](<./Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md>)  
> **Visual Whiteboard Canvas:** [`architecture.tldr`](./architecture.tldr) (Page 2: `LLD - Agent Orchestration & Planning Core`)  
> **Component Loop (thoughts.md order, excl. Orchestration):** [1/15] User & Application — ✅ CLOSED (page `LLD - [1] User & Application`; page-1 nodes trimmed to pointers)  
> &nbsp;&nbsp;&nbsp;&nbsp;[2/15] Knowledge & Retrieval — ✅ CLOSED (page `LLD - [2] Knowledge & Retrieval`; page-1 node trimmed to pointer)  
> &nbsp;&nbsp;&nbsp;&nbsp;[3/15] Memory & State — 🟡 IN PROGRESS (steps 1–4 logged, forks awaiting user decision)

---

## 1. Executive Purpose & Genesis Integration

This checkpoint document records all architectural decisions, trade-off selections, and design rationales across the enterprise agent lifecycle. 

When initializing the **Genesis** code generation agent, this file serves as the definitive source of architectural truth:
1. Every component will adhere strictly to the options chosen herein.
2. No assumptions or unilateral framework defaults will be introduced without explicit checkpointing.
3. Every decision maps directly to engineering constraints, class hierarchies, and execution contracts.

---

## 2. Component [ 5 ]: Agent Orchestration Core & Runtime

The Orchestration Core is the central cognitive control plane. It decomposes customer intent, governs state transitions, compiles context windows, dispatches tools, and recovers from runtime exceptions.

Below are the finalized **Architectural Decisions** established for Component [ 5 ]:

---

### ADP-01: Planning & Deliberative Cognition Paradigm
* **Selected Architecture:** **Option C — Hybrid Dual-Process Statechart (Deterministic FSM + Scoped ReAct)**
* **Literature Foundations:**
  * Dual-Process Theory (Stanovich & West, 2000; Kahneman, 2011)
  * Deterministic Reducer Transitions: $S_{t+1} = \text{Reducer}(S_t, \Delta_t)$
  * Localized ReAct Trajectory: $\tau_t = (o_0, r_0, a_0, \dots)$ (Yao et al., 2023)
* **Operational Rationale:**
  * Enterprise customer support cannot tolerate open-loop hallucination or non-deterministic branching on regulatory, billing, or security operations (e.g., initiating refunds, resetting credentials, tenant migrations).
  * High-risk operations follow deterministic statechart reducers with strict pre/post-condition invariants.
  * Generative ReAct and Plan-and-Solve reasoning is strictly scoped *within* bounded edge nodes for open-ended diagnostic steps and log analysis.
* **Genesis Directives:**
  * Implement `core/orchestrator/statechart.py` using `langgraph.graph.StateGraph`.
  * Define explicit state enums (`INGESTED`, `TRIAGED`, `COLLECTING_PARAMS`, `EXECUTING_SOP`, `DELIBERATING`, `AWAITING_APPROVAL`, `RESOLVED`).
  * Ensure high-risk transitions are hardcoded code edges, while open-ended tasks route through deliberative subgraphs.

---

### ADP-02: Workflow Execution & Durability Substrate
* **Selected Architecture:** **Option C — Two-Tier Hybrid (Temporal.io Outer Saga + LangGraph Inner Cognitive Loop)**
* **Literature Foundations:**
  * Distributed Saga Patterns & Compensating Transactions (Garcia-Molina & Salem, 1987)
  * Multi-tier Durable Execution (Temporal.io State Machine)
* **Operational Rationale:**
  * Contact center workflows often span multiple hours or days (e.g., awaiting customer reply, human specialist review, tier-3 engineering escalations). In-memory or simple database polling is vulnerable to node crashes and deploy restarts.
  * **Temporal.io** guarantees event-sourced execution replay, crash durability, and reliable multi-day timers for ticket lifecycles and human escalation queues.
  * **LangGraph** runs inside Temporal Activity workers, providing sub-second execution velocity for rapid, multi-turn LLM reasoning.
* **Genesis Directives:**
  * Implement `workflows/ticket_lifecycle_workflow.py` (Temporal workflow definition for ticket SLA, timeout timers, and human signals).
  * Implement `activities/cognitive_reasoning_activity.py` (LangGraph invocation wrapper with thread snapshot commits).

---

### ADP-03: Working Memory & Context Engineering Strategy
* **Selected Architecture:** **Option C — Tripartite Structured Slot Allocator**
* **Literature Foundations:**
  * Baddeley Multicomponent Working Memory Model (Baddeley, 2000)
  * Mitigating "Lost in the Middle" Attention Degradation (Liu et al., 2023)
* **Operational Rationale:**
  * Naive FIFO turn buffers suffer from the "context cliff" (dropping earliest user constraints) or token bloat.
  * Allocating explicit token budget quotas prevents prompt dilution and guarantees room for high-precision retrieval:
    - **15%**: Immutable System Persona, Role Boundaries, and Hard Safety Invariants.
    - **15%**: Customer Identity & Chronic Account Knowledge Graph (extracted facts).
    - **35%**: Grounded RAG Knowledge Chunks (Hybrid BM25 + dense (RRF), LLM-reranked; top-10 parent sections. *Label corrected 2026-09-24 per KR-Q6; was "ColBERT MaxSim + BM25".*)
    - **25%**: Chronological Dialogue Buffer (verbatim recent turns).
    - **10%**: Ephemeral Scratchpad / Working Memory Buffer for intermediate plan thoughts.
* **Genesis Directives:**
  * Implement `core/context/assembler.py` with typed Pydantic slot models: `ContextEnvelope`, `SystemSlot`, `ProfileSlot`, `RAGSlot`, `DialogueSlot`, and `ScratchpadSlot`.
  * Enforce dynamic token counting with `tiktoken` and automatic compaction when budget bounds are approached.

---

### ADP-04: Metacognitive Error Recovery & Deliberative Replanning
* **Selected Architecture:** **Option C — Dual-Process Circuit Breaker (Reflexion with Max 2 Trials + HITL Tripwire)**
* **Literature Foundations:**
  * Reflexion Verbal Reinforcement (Shinn et al., 2023): $c_t = \text{LLM\_reflect}(\tau_t, S_t, \Omega)$
  * High-Reliability Organizing (HRO) & Preoccupation with Failure (Weick & Sutcliffe, 2007)
* **Operational Rationale:**
  * Pure autonomous reflection loops risk "hallucination spiraling" (the agent rationalizes errors and repeatedly retries incorrect operations).
  * Conversely, a purely rigid fallback ladder fails to recover from minor syntactic errors (e.g. malformed JSON parameters or transient HTTP 503 retries).
  * Dual-Process Circuit Breaker gives the agent **one to two trials** to self-diagnose and correct tool invocation errors via verbal critique. If the failure persists on trial 3, the circuit breaker trips immediately, packaging a diagnostic state bundle and escalating to a Human Specialist (HITL).
* **Genesis Directives:**
  * Implement `core/recovery/circuit_breaker.py` with `StepCeilingGuard(max_steps=4)` and `TrialCounter(max_reflexion=2)`.
  * *Amended by ADP-05-Q1 (2026-09-25):* success or failure of each attempt is decided by a Jev `Noul` "did this step succeed?", not by the LLM. The LLM still writes the verbal critique. A low-confidence answer counts as failure.
  * Emit structured diagnostic packets (`IncidentDiagnosticPacket`) containing the full trajectory $\tau_t$ upon circuit trip.

---

### ADP-05: Decision Model at Orchestration Decision Points (Jev)
* **Selected Architecture:** **Jev (TypeSafe System One model) at the orchestrator's decision points; LangGraph stays the graph runner; Temporal stays the durability layer.** (User decision, 2026-09-24. Option (b), replacing LangGraph with a plain typed Python FSM, was rejected.)
* **What Jev is (and is not):**
  * A decision model, not an orchestration framework. It takes `state` + typed questions and returns typed answers with probabilities and a 0–1 confidence ([docs](https://docs.typesafe.ai/introduction)).
  * Three question types: **Choice** (pick one option), **Score** (rate against ordered levels), **Noul** (probability of yes). All questions in one request are evaluated in parallel.
  * It does **not** generate text, call tools, persist state or run loops. Per TypeSafe: "Code handles deterministic work and owns the control flow." This matches ADP-01: LangGraph edges stay code; Jev only picks which edge.
* **Scope (confirmed): three decision points inside Component [ 5 ]:**
  1. **Triage gate (ADP-01 `TRIAGED`):** one request replaces the Instructor/SLM classifier: `Choice` intent, `Score` urgency (acuity), `Noul` "required details missing?". Routes to System 1 FSM / System 2 ReAct / clarification / human by answer **and** confidence ([intent-routing pattern](https://docs.typesafe.ai/patterns/intent-routing.md)). Replaces the "51/49 intent tie" handling.
  2. **FSM transition guards:** `Noul` checks on conditional edges, e.g. "has the customer confirmed the fix?" before `RESOLVED` (Q3 "Premature Ticket Close").
  3. **System 2 tool selection:** `Choice` over the tool list and closed-set arguments ([function_calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling.md)). Plan-and-Solve plan generation and free-text arguments stay with the generative LLM.
* **Unchanged:** ADP-02 (Temporal + LangGraph), ADP-03 (slot allocator), customer-facing response generation (generative LLM), MCP tool execution, two-phase mutation checks.
* **Constraints from Jev's known limits** ([jev-1.13](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md)):
  * Not a calculator and weak on dates: amount limits (> $1,000 review), SLA intervals and date ordering stay in code.
  * Degrades with irrelevant state: send each request a narrow, purpose-built `state`, not the full `ContextEnvelope`.
  * Susceptible to prompt injection: Jev answers never authorize a high-risk transition on their own; ADP-01 hard-coded edges and pre/post-conditions still gate them.
  * Literal reading of instructions: question wording is versioned and tested like code (Comp 9 golden sets).
* **Genesis Directives:**
  * Add `typesafe-sdk`; use `AsyncTypeSafeClient` (API key in `TYPESAFE_API_KEY`), with `RetryPolicy` and a timeout.
  * `state` sent to Jev must come from the PII-masked envelope (Q3). Confidence thresholds are loaded per route from config (Q2).
  * Implement `core/orchestrator/decisions.py`: typed question sets per decision point (`TriageQuestions`, `TransitionGuards`, `ToolSelection`) returning Pydantic results with `confidence`.
  * `triage.py` and LangGraph conditional-edge functions call `decisions.py`; they never parse free text.
  * On Jev timeout/error: triage → clarification or human queue; guard → treat as "no" (do not transition); tool selection → HITL. Never fail open.
  * Log every Jev request/answer/confidence to the trace (Langfuse / OpenTelemetry) for threshold calibration.

**Follow-up questions:**

| ID | Question | Resolution | Status |
| :--- | :--- | :--- | :--- |
| **ADP-05-Q1** | ADP-04 Reflexion: Jev cannot write the verbal critique. | **(i)** Keep the LLM verbal critique. After each attempt, a Jev `Noul` "did this step succeed?" decides: success → continue; failure → next Reflexion trial (max 2); failure after trial 2, or low confidence → trip to HITL. Amends ADP-04. | ✅ CONFIRMED 2026-09-25 |
| **ADP-05-Q2** | Confidence thresholds per route. | **TypeSafe's bands as starting thresholds:** > ~0.9 act · 0.5–0.9 confirm / clarify / flag · < 0.5 human or clarify. Routes that change data (refunds, credential resets, account changes) get stricter thresholds. All thresholds are calibrated on golden sets (Comp 9) and kept in config, not code. | ✅ CONFIRMED 2026-09-25 |
| **ADP-05-Q3** | Data leaving the boundary: Jev is a hosted API, so `state` (customer text) goes to TypeSafe. | **(i)** Send `state` to Jev only after Comp 7 PII masking. `decisions.py` accepts only the masked envelope, and nothing is sent before masking runs. | ✅ CONFIRMED 2026-09-25 |
| **ADP-05-Q4** | Jev outside orchestration (not in scope of this decision). | Candidates, each to be decided in its own loop: KR-D9/D12/D14 reranker (reopens a closed component) · KR-D11 judge · Comp 7 guardrails · Comp 9 confidence gate · Comp 10 supervisor routing · Comp 12 model-tier routing · Memory fact reconciliation (MS loop) | 🔵 OPEN |

---

## 3. Genesis Architectural Checkpoint Log

| Decision ID | Component | Title | Selected Option | Key Rationale & Constraints | Date | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **ADP-01** | Agent Runtime | Planning Paradigm | **Option C: Hybrid Dual-Process Statechart** | Deterministic LangGraph FSM for compliance/SOPs + scoped ReAct in edge nodes. | 2026-09-11 | ✅ CONFIRMED |
| **ADP-02** | Agent Runtime | Workflow Durability | **Option C: Two-Tier Hybrid (Temporal + LangGraph)** | Temporal manages multi-day ticket sagas & HITL; LangGraph runs inner cognitive turns. | 2026-09-11 | ✅ CONFIRMED |
| **ADP-03** | Agent Runtime | Context Engineering | **Option C: Tripartite Structured Slot Allocator** | Bounded token quotas: System (15%), Profile (15%), RAG (35%), Chat (25%), Scratchpad (10%). | 2026-09-11 | ✅ CONFIRMED |
| **ADP-04** | Agent Runtime | Error Recovery & Replanning | **Option C: Dual-Process Circuit Breaker** | Max 2 Reflexion critique trials; trips immediately to HITL on repeated failure. | 2026-09-11 | ✅ CONFIRMED |
| **ADP-05** | Agent Runtime | Decision model | **Jev (TypeSafe) at orchestration decision points; LangGraph kept as runner** | Triage, FSM transition guards, System 2 tool selection. Code owns control flow; numbers/dates in code. Q1: LLM critique kept, Jev step check trips HITL · Q2: TypeSafe bands, stricter for data-changing routes · Q3: only PII-masked state sent · Q4 (scope beyond Comp 5) open. | 2026-09-24 / 25 | ✅ CONFIRMED |
| **UA-D1** | User & Application | Channels | **Web-only v1** | Channel-agnostic envelope keeps later adapters additive. | 2026-09-23 | ✅ CONFIRMED |
| **UA-D2** | User & Application | API transport | **Decoupled POST 202 + resumable SSE** | One path for live, reconnect and deferred (HITL) delivery; idempotent submit. | 2026-09-23 | ✅ CONFIRMED |
| **UA-D3** | User & Application | Identity assurance | **Tiered T0 anon → T1 OTP → T2 SSO + step-up** | Low friction for FAQs; account actions behind real identity. | 2026-09-23 | ✅ CONFIRMED |
| **UA-D4** | User & Application | Downstream identity | **RFC 8693 on-behalf-of tokens (`sub` + `act`)** | T0 gets a minimal read-only token (UA-Q1 → ii); T1/T2 normal on-behalf-of tokens. | 2026-09-23 | ✅ CONFIRMED – conditional |
| **UA-D9** | User & Application | Token validity | **User access token valid 24 h** | Outlives the 12 h session cap, which fixes KK3's expiry variant; revocation and theft window remain owned. | 2026-09-23 | ✅ CONFIRMED |
| **KR-D1** | Knowledge & Retrieval | Sources | **Curated + operational + raw tickets/chats** | Max coverage; ticket placement/PII pending KR-Q1/Q2. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D2** | Knowledge & Retrieval | Internal content | **Per-chunk `audience` label; only customer-visible cited** | Internal shown to specialists only; granularity pending KR-Q4. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D3** | Knowledge & Retrieval | Freshness | **Nightly batch re-index** | ≤24 h stale; deletion/ACL-revocation timing pending KR-Q3. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D4** | Knowledge & Retrieval | Chunking | **Structure-aware + parent-child small-to-big** | No indexing-time LLM cost. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D5** | Knowledge & Retrieval | Tenant isolation | **Shared public index + per-tenant private index** | T0 anonymous → public only. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D6** | Knowledge & Retrieval | ACL freshness | **Copied ACLs + live OBO check for restricted docs** | Low latency on the common path. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D7** | Knowledge & Retrieval | First-stage retrieval | **Hybrid BM25 + dense, RRF** | Exact IDs + concepts; ADP-03 label mismatch pending KR-Q6. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D8** | Knowledge & Retrieval | Query transform | **Standalone rewrite + identifier extraction** | No HyDE / multi-query. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D9** | Knowledge & Retrieval | Reranker | **LLM-as-reranker** | Injectable by retrieved text; uncalibrated scores; cost → Comp 12. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D10** | Knowledge & Retrieval | Admission | **Fixed top-k, no abstain** | No `no_evidence`; answer/abstain decision moves to Comp 9; k pending KR-Q5. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D11** | Knowledge & Retrieval | Retrieval eval | **Offline golden + online signals + sampled LLM judge** | Gap signal redefined (no no-evidence rate). | 2026-09-24 | ✅ CONFIRMED |
| **KR-D12** | Knowledge & Retrieval | Reranker validation | **Schema-check wrapper; fall back to RRF order** | Fixes KK3; fallback rate → Comp 10. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D13** | Knowledge & Retrieval | Default audience | **Internal by default; explicit override for public** | Fixes UK4; bulk labeling needed at onboarding. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D14** | Knowledge & Retrieval | Pre-rerank screening | **Screen retrieved text before the LLM reranker** | Mitigates UU1; Comp 7 engine invoked inside retrieval. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D15** | Knowledge & Retrieval | Deletion-aware ingestion | **Check → filter → ingest → verify + post-batch check** | Fixes UU4. | 2026-09-24 | ✅ CONFIRMED |
| **KR-D16** | Knowledge & Retrieval | Ticket provenance | **`source = human \| agent`; agent text kept but labeled "agent reply, unverified"** | KR-Q7 → (ii); mitigates UU5. | 2026-09-24 | ✅ CONFIRMED |
| **UA-Q2** | User & Application | Deferred delivery | **Inbox only (no email notification in v1)** | Accepted risk: users who never return miss outcomes. | 2026-09-23 | ✅ CONFIRMED |
| **UA-Q3** | User & Application | Idle-timer activity | **Any authenticated request incl. open SSE** | Open tab keeps session to the 12 h cap; accepted (KK3/UK5/UU5). | 2026-09-23 | ✅ CONFIRMED |
| **UA-D5** | User & Application | Session scoping | — | Revisit at Comp. 4 / 13. | 2026-09-23 | 🔵 OPEN |
| **UA-D6** | User & Application | Session timeouts | **Fixed 30 min idle / 12 h absolute** | NIST AAL2; delivery must not depend on a live session. | 2026-09-23 | ✅ CONFIRMED |
| **UA-D7** | User & Application | Response contract | **Typed event envelope** | Citations and action cards are first-class; channel-degradable. | 2026-09-23 | ✅ CONFIRMED |
| **UA-D8** | User & Application | Stream vs. safety gate | — | Revisit after Comp. 7 / 9; likely experiment. | 2026-09-23 | 🔵 OPEN |

---

## 4. Genesis Codebase Mapping (Pre-Execution Blueprint)

When Genesis is triggered, the following module scaffolding will be instantiated:

```
src/
├── core/
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── statechart.py        # LangGraph StateGraph, Node reducers, hardcoded FSM edges
│   │   ├── triage.py            # Layer 0 Acuity Classifier (ESI / MTS protocol)
│   │   ├── decisions.py         # ADP-05 Jev question sets (triage, transition guards, tool selection)
│   │   └── planner.py           # Layer 2 Deliberative ReAct / Plan-and-Solve engine
│   ├── context/
│   │   ├── __init__.py
│   │   ├── assembler.py         # Tripartite slot budget allocator & token packing
│   │   └── models.py            # Pydantic schemas for ContextEnvelope and Slots
│   └── recovery/
│       ├── __init__.py
│       ├── circuit_breaker.py   # Anti-loop detector, StepCeilingGuard (N<=4)
│       └── reflexion.py         # Verbal self-critique generator (max 2 trials)
├── workflows/
│   ├── ticket_saga.py           # Temporal workflow for durable ticket lifecycle & HITL
│   └── activities.py            # Temporal activities invoking LangGraph cognitive turns
└── schemas/
    ├── state.py                 # Thread state schema S_t and Delta_t reducers
    └── diagnostic.py            # IncidentDiagnosticPacket for HITL escalation
```

---

## 5. Component Loop [1/15] — User & Application

> **Loop status:** ✅ CLOSED (2026-09-24) · Steps 1–11 complete · Page-1 duplicate trimmed to pointers (see §5.11)  
> **Decision ID prefix:** `UA-` (forks `UA-F#`, decisions `UA-D#`), which keeps these IDs apart from ADP-01..04  
> **Architecture mapping:** thoughts.md Component 1 ≈ architecture tiers `[1] User Channels` + `[2] API Gateway & Identity` + `[13/14] Response Delivery → User Client` (transport part only)

### 5.1 GROUND — Raw Material (not decisions)

**From the master report (thin for this component; it mainly positions ingress):**
* End-to-end lifecycle: `[Ingress & Edge Security] → [API Gateway & Identity] → [Input Guardrails & PII Masking]` … `[Response Delivery Engine] → [User UI]`.
* "PII redaction executes at the network edge before model ingestion" (Presidio, 15–30 ms, Network Ingress & Egress), so the ingress path must leave room for a synchronous redaction hop that **Safety (Comp. 7) owns**.
* Confidence gate (≥0.90 auto-send / 0.70–0.90 co-pilot / <0.70 warm handoff): the final response is only known *after* a full-draft evaluation. This conflicts with token streaming (see UA-F8).
* GDPR Art. 22 (human review right), EU AI Act (human-oversight logging), *Moffatt v. Air Canada* (2024): agent statements are binding corporate representations, so the delivered response is a **legal record** and must be persisted exactly as rendered.
* Dixon et al. (2010) Customer Effort Score: forced channel switching and repeating information are the top effort drivers, which argues for cross-channel continuity.
* Erlang A (Garnett, Mandelbaum & Reiman, 2002): impatient customers abandon, and a perceived wait counts as abandonment risk. SLA 80/20 applies.

**From general engineering practice / standards:**
* **Identity:** OAuth 2.0 (RFC 6749), OpenID Connect Core 1.0, JWT (RFC 7519) and the JWT access-token profile (RFC 9068), Token Introspection (RFC 7662), **Token Exchange / on-behalf-of (RFC 8693)**. NIST SP 800-63B Authenticator Assurance Levels (AAL1–3) with re-authentication and inactivity limits (rev.3: AAL2 ≤12 h / ≤30 min idle). Step-up authentication for high-risk actions.
* **Sessions:** OWASP ASVS v4 §V3 (session management), OWASP API Security Top 10 (2023) **API1 BOLA**: guessable `session_id`/`conversation_id` means cross-tenant transcript leakage.
* **Transport:** WebSocket (RFC 6455), Server-Sent Events (WHATWG HTML §9.2, with `Last-Event-ID` resume), HTTP semantics (RFC 9110), Problem Details (RFC 9457), 429 + `Retry-After` (RFC 6585), IETF draft *Idempotency-Key HTTP header*.
* **Channel platform contracts:** Slack Events API requires a 200 ack within **3 s** and otherwise retries (`X-Slack-Retry-Num`), with HMAC signing-secret verification. MS Bot Framework Activity schema + Teams Adaptive Cards. Twilio `X-Twilio-Signature`. Email threading via RFC 5322 `Message-ID`/`In-Reply-To`.
* **Event envelope:** CNCF CloudEvents 1.0 (canonical, transport-agnostic event metadata).
* **Perceived latency:** Miller (1968) / Nielsen (1993): 0.1 s / 1 s / 10 s response-time limits. Past about 1 s users need progress feedback, and past 10 s they lose the task.
* **Accessibility / disclosure:** WCAG 2.2 (ARIA live regions for streamed chat). EU AI Act Art. 50(1): users must be told they are interacting with an AI system.

### 5.2 DECOMPOSE

| Sub-component | Mechanic (what it does) |
| :--- | :--- |
| **Channels / UI** | Per-channel adapters (Web widget, Mobile, Slack, Teams, Email, later Voice). They verify platform signatures and translate native payloads to and from the canonical envelope. Each declares a capability descriptor (rich cards? streaming? max length?). |
| **API** | Public ingress contract: turn submission, delivery stream, attachment upload, error model (RFC 9457), idempotency keys, API versioning. |
| **Identity** | Establishes *who* (principal) and *which tenant*. Validates tokens, sets the assurance level, and mints the internal identity context that is handed downstream. |
| **Sessions** | Binds principal ↔ channel ↔ conversation thread. Covers session creation, resume, expiry, and non-enumerable IDs. Owns the *keys*, not the *content*. |
| **Request / Response** | Normalizes the inbound turn into a canonical request envelope. Accepts the final approved response envelope and renders and delivers it per channel (stream, push, or deferred). |

### 5.3 TRACE THE TRAJECTORY

**Flow A — Synchronous interactive turn (Web portal, Sarah @ acme-corp)**
1. **Channels/UI**: The widget sends the message with the OIDC access token plus a client-generated `turn_id` (idempotency).
2. **API**: Ingress validates size limits, content-type and API version, and dedupes on `Idempotency-Key`.
3. **Identity**: Verifies the JWT (signature, `exp`/`nbf` with clock-skew, `aud`, issuer), resolves `tenant_id`, `principal_id`, roles, and assurance level (AAL2 via SSO).
4. **Sessions**: Resolves or creates `session_id`, then checks that the session belongs to the principal *and* the tenant (BOLA guard). Attaches `conversation_id`.
5. **Request/Response (in)**: Builds the canonical `InboundTurnEnvelope {tenant, principal, assurance, session_id, conversation_id, turn_id, channel, channel_capabilities, content, attachment_refs, received_at}`.
   → **HANDOFF** to Safety (Comp. 7: PII masking + injection screening), then Orchestration (Comp. 2).
6. *(Out of scope: orchestration, retrieval, tools, confidence gate)*
7. **Request/Response (out)**: Receives the `OutboundResponseEnvelope` *after* the output guardrails and confidence gate have run. Renders per `channel_capabilities` and delivers over the open stream.
8. **Channels/UI**: Widget renders text, citations and action cards, and acknowledges receipt (`delivered` event).
   → **HANDOFF** to Data & Persistence (Comp. 8): the transcript *as rendered*, as a legal record.

**Flow B — Asynchronous channel + deferred delivery (Slack, or a HITL approval that resolves hours later)**
1. **Channels/UI**: The Slack adapter verifies the HMAC signature and returns **200 within 3 s** (it must not wait for the LLM).
2. **Identity**: Maps the Slack user to an enterprise principal (identity linking). An unlinked user drops to low assurance.
3. **Sessions**: The thread `ts` maps to `conversation_id`.
4. **Request/Response (in)**: Emits the same canonical envelope, so the downstream pipeline is identical to Flow A.
5. …the agent proposes `apply_credit_memo($12,400)` → Tools/HITL (Comp. 5/13) pause via a Temporal signal. **Not owned here.**
6. **Request/Response (out)**: Hours later the approved outcome arrives as an outbound event. The user is offline, so delivery goes out via the channel's **push** path (Slack `chat.postMessage` into the thread / email / web notification + inbox on next load).
7. Delivery receipt, or retry with backoff. If the preferred channel stays unreachable, fall back to email.

**Flow C — Session lifecycle (resume · step-up · channel switch · expiry)**
1. **Resume**: The browser reconnects with `Last-Event-ID`, and any missed events are replayed from the outbound log.
2. **Step-up**: A high-risk intent arrives (downstream classifies it, and Identity is *asked*). Identity issues an assurance challenge (re-auth / MFA). On success it raises the assurance level on the session, and the turn resumes.
3. **Channel switch**: Sarah continues by email. Whether this joins the same conversation depends on **UA-F5** (undecided).
4. **Expiry**: Idle / absolute timeout invalidates the session token. The conversation and case persist (owned by Memory/Data), and a new session can re-attach to them.

### 5.4 BOUNDARY

| | |
| :--- | :--- |
| **Receives (upstream)** | Raw end-user input from channel platforms (HTTP / WSS / webhooks / SMTP) + IdP tokens + platform signatures. **Downstream-return:** final, gated `OutboundResponseEnvelope` from Output Safety / Confidence Gate (Comp. 7/9/13); deferred outcome events from Orchestration workflows (Comp. 2). |
| **Hands off (downstream)** | Canonical `InboundTurnEnvelope` (identity-bound, session-bound, idempotent) → Safety (Comp. 7) → Orchestration (Comp. 2). Delivery receipts and rendered transcripts → Data & Persistence (Comp. 8), Observability (Comp. 10). |
| **Does NOT own** | PII redaction, injection/jailbreak detection, output guardrails (Comp. 7) · **Rate limiting** (Comp. 11 per thoughts.md) · Tool/action authorization and permissions (Comp. 5/7) · Conversation memory *content* and summarization (Comp. 4) · Workflow timers and durable waits (Comp. 2, Temporal) · Confidence gating (Comp. 9) · Escalation/handoff *decision* and human queues (Comp. 13, which Channels only render) · Model token generation (Comp. 2 Model Interaction) · Transcript storage (Comp. 8). |

### 5.5 SURFACE THE FORKS (undecided; awaiting user)

| Fork | Sub-component | Tension | Options | Status |
| :--- | :--- | :--- | :--- | :--- |
| **UA-F1** | Channels/UI | Build vs. buy channel adapters | (a) In-house adapters behind the canonical envelope · (b) Omnichannel platform (Bot Framework / Twilio Conversations) · (c) Web-only MVP, adapters later | ✅ (c) — see UA-D1 |
| **UA-F2** | API | Coupling of submit and delivery | (a) POST returns SSE stream per turn · (b) Persistent bidirectional WebSocket · (c) Decoupled: `POST /turns` → 202 + `turn_id`; a separate subscribable event stream (SSE, resumable) serves both live and deferred delivery | ✅ (c) — see UA-D2 |
| **UA-F3** | Identity | Who may talk to the agent | (a) Authenticated only (SSO/OIDC) · (b) Anonymous allowed for FAQ-only · (c) Tiered assurance: anonymous → verified (OTP) → authenticated, with step-up gating by action risk | ✅ (c) — see UA-D3 |
| **UA-F4** | Identity | What identity is propagated downstream | (a) Forward the user's token as-is · (b) Agent service account + user claims as context (confused-deputy risk) · (c) RFC 8693 token exchange: short-lived, audience-scoped on-behalf-of tokens carrying both `sub` (user) and `act` (agent) | ✅ (c) conditional — see UA-D4 |
| **UA-F5** | Sessions | Session ≠ conversation ≠ case? | (a) Channel-bound sessions, each its own conversation · (b) One cross-channel conversation per principal · (c) Three-level model: transport *session* → *conversation* thread → durable *case* ID; channels link to a case | 🔵 OPEN — see UA-D5 |
| **UA-F6** | Sessions | Timeout policy | (a) Fixed idle/absolute (e.g. 30 min / 12 h, per NIST AAL2) · (b) Per-channel (web short, Slack/email long-lived) · (c) Tied to assurance level + tenant config | ✅ (a) — see UA-D6 |
| **UA-F7** | Request/Response | Response contract | (a) Markdown text stream · (b) Typed event envelope (`text_delta`, `citation`, `action_card`, `status`, `handoff`, `final`) that each adapter degrades to its capabilities | ✅ (b) — see UA-D7 |
| **UA-F8** | Request/Response | **Streaming latency vs. output safety gate** | (a) Buffer the full answer until the gate passes (safe, TTFT = full generation + gate) · (b) Stream raw tokens and retract on failure (fast, but unsafe text was already seen) · (c) Stream immediate `status` events ("Checking your invoice…") while the answer is buffered, then send the gated answer · (d) Sentence-chunked streaming through an incremental guard | 🔵 OPEN — see UA-D8 |

### 5.6 RESOLVE — Step 6 (user decisions, 2026-09-23)

| ID | Sub-component | Decision | Status | Reasoning captured |
| :--- | :--- | :--- | :--- | :--- |
| **UA-D1** | Channels/UI | **Web widget is the only channel in v1.** The canonical envelope stays channel-agnostic, so Slack/Teams/Email adapters can be added later without changing downstream contracts. | ✅ CONFIRMED | Smallest surface for v1: one set of signature/ack contracts and one renderer. Flow B's Slack leg (3 s ack, identity linking) is **out of v1 scope**. Flow B's *deferred delivery* leg still applies to web (HITL resolves while the user is offline). |
| **UA-D2** | API | **Decoupled submit/receive.** `POST /v1/conversations/{id}/turns` (Idempotency-Key = `turn_id`) → `202 {turn_id}`. `GET /v1/conversations/{id}/events` is a resumable SSE stream (`Last-Event-ID` replay from the outbound event log). | ✅ CONFIRMED | One delivery path serves live answers, reconnects and HITL-resolved answers that arrive hours later (Flow B/C). Client retries of the POST are deduplicated. SSE runs over plain HTTP/2 and passes corporate proxies more reliably than WebSocket. Consequence: server-side outbound event log per conversation (storage owned by Comp. 8). |
| **UA-D3** | Identity | **Tiered assurance:** `T0 anonymous` → `T1 verified` (one-time code to email/phone on file) → `T2 authenticated` (enterprise SSO/OIDC). A higher tier is required (step-up) when downstream classifies an intent/action as needing it. | ✅ CONFIRMED | Lets FAQ-type traffic be served without login friction (CES) while keeping account actions behind real identity. Identity *issues* the step-up challenge. *Which* actions need which tier is a policy owned by Safety/Tools (Comp. 7/5); Identity only enforces the tier. |
| **UA-D4** | Identity | **RFC 8693 token exchange:** short-lived, audience-scoped on-behalf-of tokens with `sub` = user, `act` = agent. **Tier rule (UA-Q1 → ii):** T0 anonymous gets a minimal token with `sub` = anonymous session ID and a read-only audience only. T1/T2 get normal on-behalf-of tokens scoped to their tier. | ✅ CONFIRMED – conditional (rule stated) | Every downstream call carries a token that names both the principal and the agent, including anonymous traffic, so audit is uniform. **Which tools sit in the T0 read-only audience is owned by Tools/Safety (Comp. 5/7)**; Identity only mints the token. |
| **UA-D5** | Sessions | Session / conversation / case scoping | 🔵 OPEN | **Deferred on purpose. Revisit during Comp. 4 (Memory & State) and Comp. 13 (Human-in-the-Loop)**, which key their data on this. **What it blocks until then:** Flow C step 3 (channel switch, though with web-only v1 this is moot); where a deferred answer lands after the session expires (**UA-Q2**); the `conversation_id` lifetime. **v1 working assumption for tracing only (not a decision):** one web `conversation_id` per chat thread, independent of session lifetime. |
| **UA-D6** | Sessions | **Fixed timeouts: 30 min idle / 12 h absolute** (NIST 800-63B AAL2), same for every tier. | ✅ CONFIRMED | Simple and standards-aligned. **Consequence:** a HITL outcome can arrive after the session has expired, so delivery must not depend on a live session and the answer must be persisted to the conversation (ties to UA-D2's event log and UA-D5). What counts as "activity" is still open (**UA-Q3**). |
| **UA-D7** | Request/Response | **Typed event envelope:** `status`, `text_delta`, `citation`, `action_card`, `handoff`, `final`, `error`, each with `event_id` (monotonic per conversation), `turn_id`, `type`, `payload`. The web renderer maps each type to a component. Future adapters degrade by type. | ✅ CONFIRMED | Citations and action cards (e.g. approval status of the $12,400 credit memo) are first-class, not parsed out of markdown. Pairs naturally with UA-D2 (SSE `event:` field = `type`, `id:` = `event_id`). |
| **UA-D8** | Request/Response | Streaming vs. output-safety gate | 🔵 OPEN | **Deferred on purpose. Revisit after Comp. 7 (Safety) and Comp. 9 (Evaluation)**, which determine the gate's actual latency and whether an incremental guard exists. Option (d) needs a streaming-capable guard, and (a) is only acceptable if gate + generation fit the ~10 s attention limit. **What it blocks until then:** whether `text_delta` events are emitted before `final`. UA-D7 already carries both, so the contract survives either outcome. Candidate for a Step-7 experiment when revisited. |

**Follow-up answers (user, 2026-09-23):**
* **UA-Q1 → (ii)** Minimal read-only T0 token (`sub` = anonymous session). Folded into UA-D4.
* **UA-Q2 → (i)** Inbox only. A deferred outcome is persisted to the conversation and shown on the next visit/login. No outbound email notification in v1. Accepted trade-off: users who never return never see the outcome (see grid KU4). Works because HITL-worthy actions require step-up to T1/T2, so the inbox is always keyed to a returning, identifiable principal.
* **UA-Q3 → (ii)** Any authenticated request, including an open SSE stream, resets the 30-min idle timer. Consequence: an open tab keeps the session alive up to the 12 h absolute cap (see grid KK3, UK5, UU5).

**Follow-up questions as originally asked:**
* **UA-Q1** (D3 × D4): T0 anonymous has no user token. What can the agent do on its behalf? Options: (i) no delegated token, so public/read-only knowledge only and every tool call requires step-up · (ii) a T0 token with `sub` = anonymous session and a tiny read-only audience.
* **UA-Q2** (D1 × D2 × D6): the user is offline or the session has expired when a HITL outcome arrives. Web-only v1 means no Slack/email channel. Options: (i) inbox only: the event is persisted and shown on the next visit/login · (ii) inbox + an outbound email *notification* (a link, not a conversation channel).
* **UA-Q3** (D2 × D6): what resets the 30-min idle timer? Options: (i) only user-originated turns (SSE heartbeats and agent events do not) · (ii) any authenticated request including an open SSE stream.

### 5.7 EXPERIMENT CHECK — Step 7

No fork was marked for experiment. Per the loop, UA-D1/D2/D3/D6/D7 stand as the finalized approach, and UA-D4 stands subject to UA-Q1. UA-D5 and UA-D8 are OPEN (not experiments). UA-D8 is flagged as a likely experiment candidate when it is revisited.

### 5.8 FAILURE MODES — Step 8 (Known/Unknown grid, scoped to this component's sub-components)

Quadrant definitions follow [`failure_modes_matrix.md`](./failure_modes_matrix.md). Entries already in the whole-system matrix are **pointed to, not restated**: 15 MB paste vs. body limit, JWT clock-skew, Okta introspection latency, vague prompt, cheerful tone during an outage.

**Q1 — KNOWN KNOWNS (explicit contract breaches)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| KK1 | API | The client retries `POST /turns` after a network blip, so the same turn is processed twice (duplicate tool proposal). |
| KK2 | API / Req-Resp | A proxy/LB idle timeout (e.g. Nginx 60 s) cuts the SSE stream mid-answer, and events are missed. |
| KK3 | Identity | The access token expires while the long-lived SSE stream stays open. The token was only validated at connect, so events keep flowing to a no-longer-authenticated client. **Made more likely by UA-Q3(ii).** |
| KK4 | Sessions | BOLA: the user swaps `conversation_id` in the URL/stream path and reads another principal's or tenant's transcript. |
| KK5 | Identity | T1 one-time code brute-forced or replayed (no attempt limit/TTL), or sent to a stale email/phone on file. |
| KK6 | Identity | An on-behalf-of token outlives logout or session expiry, and in-flight work keeps acting with it. |

**Q2 — KNOWN UNKNOWNS (we know to ask; magnitude unknown)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| KU1 | Channels/UI | Corporate proxies/AV that buffer `text/event-stream` until close, so the answer arrives all at once or never. Unknown share of enterprise users are affected. |
| KU2 | Identity | Drop-off at step-up (T0→T1/T2): how many abandon at the one-time-code/SSO prompt (Erlang A abandonment)? |
| KU3 | Sessions / API | Capacity: concurrent open SSE connections (multi-tab, 12 h lifetime under UA-Q3(ii)) and replay-log retention size. |
| KU4 | Req-Resp | Inbox-only (UA-Q2(i)): what share of users never return to see a deferred HITL outcome and re-contact instead (FCR hit)? |
| KU5 | Req-Resp | Perceived latency while UA-D8 is OPEN. It depends on gate latency that Comp. 7/9 will set. |

**Q3 — UNKNOWN KNOWNS (tacit conventions no one wrote down)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| UK1 | Channels/UI | The widget never tells users they are talking to an AI (EU AI Act Art. 50). Everyone assumes it, but it's in no spec. |
| UK2 | Channels/UI | Token-by-token updates flood screen readers via an ARIA live region (WCAG 2.2). |
| UK3 | Identity | An anonymous user *claims* an identity ("my account is 4471…") and the agent treats claimed as verified. Human agents know to verify before disclosing. |
| UK4 | Req-Resp | A deferred inbox item ("Approved: $12,400") appears hours later with no restatement of what was asked. |
| UK5 | Sessions | Shared/kiosk device: an open tab keeps the session alive for 12 h (UA-Q3(ii)), and the next person sees the conversation. |

**Q4 — UNKNOWN UNKNOWNS (emergent from combining decisions)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| UU1 | Req-Resp × Sessions | SSE replay (UA-D2) re-sends an `action_card` (UA-D7). A re-rendered "Confirm" button is clicked a second time, causing a double confirmation. |
| UU2 | Identity | Over time the T0 read-only audience (UA-Q1(ii)) gains a "harmless" lookup (order status by number), and anonymous scrapers enumerate it. |
| UU3 | Identity × Sessions | Mid-conversation step-up T0→T2: earlier anonymous turns (possibly typed by someone else on a shared device) now sit in context with T2 authority. The reverse also happens: the session expires while in-flight work holds a T2 token. |
| UU4 | Channels/UI | The renderer is an exfiltration sink: model-produced citation URLs or markdown images leak data via URL parameters. |
| UU5 | API / Sessions | Deploy/restart drops every 12 h-alive stream at once, and all clients reconnect with `Last-Event-ID`, causing a replay storm on the event log. |

### 5.9 DESIGN AGAINST THE FAILURE MODES — Step 9

Legend: **FIXES** = the decision removes the failure · **MITIGATES** = reduces likelihood or impact, with residual risk · **WORSENS** = the decision increases exposure · **OWNED RISK** = no decision addresses it yet (logged, not solved).

| Grid ID | Addressed by | Effect | Residual / owner |
| :--- | :--- | :--- | :--- |
| KK1 | UA-D2 (`Idempotency-Key` = `turn_id`) | **FIXES** at ingress | Dedupe window must exceed the client retry window (parameter, not a fork). |
| KK2 | UA-D2 (resumable SSE, `Last-Event-ID` replay) | **FIXES** data loss · **MITIGATES** UX (reconnect gap still visible) | Needs heartbeat comments shorter than proxy idle timeout (implementation). |
| KK3 | UA-D9 (24 h token > 12 h session cap) · UA-Q3(ii) **WORSENS** | **FIXES** expiry variant · **OWNED RISK** revocation variant + 24 h theft window | Server must enforce session expiry and revocation on the open stream independently of the token. |
| KK4 | Trajectory step A4 ownership check (baseline design, not a fork) | **MITIGATES** | Final ID/ownership model depends on **UA-D5 (OPEN)**. Owned until D5 resolves. |
| KK5 | UA-D3 introduces T1 | **OWNED RISK** | Attempt limits / code TTL: rate limiting is Comp. 11; code policy is Comp. 7. |
| KK6 | UA-D4 (short-lived tokens) | **MITIGATES** (bounded by TTL) | Revocation on logout/expiry is not decided. **Owned.** |
| KU1 | UA-D2 (SSE over HTTP/2 is more proxy-friendly than WS) | **MITIGATES** | No fallback (long-poll) decided. **Owned.** Measure in Comp. 10. |
| KU2 | UA-D3 (FAQs need no login) | **MITIGATES** friction overall | Step-up abandonment unmeasured. **Owned**, metric for Comp. 9/10. |
| KU3 | — (UA-Q3(ii) **WORSENS**) | **OWNED RISK** | Capacity planning → Comp. 11. Replay-log retention → Comp. 8. |
| KU4 | UA-Q2(i) introduces it | **OWNED RISK (explicitly accepted trade-off)** | Revisit if re-contact rate on deferred outcomes is high. |
| KU5 | UA-D8 OPEN | **OWNED RISK** until D8 is revisited after Comp. 7/9 | UA-D7 keeps the contract stable either way. |
| UK1 | — | **OWNED RISK** | Cheap fix (disclosure banner); belongs in the web UI spec. |
| UK2 | UA-D7 (typed `text_delta` lets the renderer batch per sentence) | **MITIGATES** only if the renderer does it | **Owned** (implementation). |
| UK3 | UA-D4 + UA-Q1(ii) (T0 token is read-only, so a claimed identity unlocks no account tools) | **FIXES** for actions · **MITIGATES** for disclosure | Whether read-only T0 tools can *reveal* account data is set by the T0 audience contents (Comp. 5/7). |
| UK4 | UA-D7 (events carry `turn_id`) | **MITIGATES** (renderer can link back) | Restating the original question in the item is not decided. **Owned** (small). |
| UK5 | UA-D6 12 h cap bounds it · UA-Q3(ii) **WORSENS** it | **OWNED RISK (accepted trade-off)** | Explicit consequence of Q3(ii). |
| UU1 | — (UA-D2 × UA-D7 **create** it) | **OWNED RISK** | Real fix is action idempotency at Tools (report: HMAC idempotency key per action) → Comp. 5. The renderer should also mark replayed cards as already-actioned. |
| UU2 | UA-Q1(ii) creates the surface | **OWNED RISK** | Governance of the T0 audience → Comp. 7 Tool Permissions. |
| UU3 | — | **OWNED RISK** | Assurance-level tagging per turn. Revisit with UA-D5. |
| UU4 | UA-D7 (structured citation payloads, not raw HTML/markdown) | **MITIGATES** | Renderer must not auto-load remote images or unlisted URLs. Output safety → Comp. 7. |
| UU5 | — (UA-D2 × UA-Q3(ii) **create** it) | **OWNED RISK** | Reconnect jitter/backoff + replay caps → Comp. 11. |

**Summary:** 3 FIXES (KK1, KK2 data loss, UK3 actions) · 7 MITIGATES · 11 OWNED RISKS (2 explicitly accepted trade-offs: KU4, UK5). **KK3 is the only owned risk inside this component's own scope that has no downstream owner.** It is flagged as a candidate new fork.

**UA-F9 / UA-D9 — Token validity (user decision, 2026-09-23): user access tokens are valid for 24 h.** ✅ CONFIRMED
* Scope as understood: the *user's* access/session credential. The on-behalf-of tokens from UA-D4 stay short-lived (unchanged).
* **Effect on KK3:** the 24 h token outlives the 12 h absolute session cap (UA-D6), so a token can no longer expire in the middle of a live session. The **expiry variant of KK3 is FIXED**.
* **Residual (still OWNED):** the *revocation* variant. After logout in another tab or an account disable, an already-open stream keeps flowing unless the server re-checks.
* **New exposure (logged, not argued):** the token can outlive its session by up to 12 h, so the server must enforce session expiry on the stream on its own rather than infer it from the token. A stolen token is also usable for up to 24 h (a wider window than typical short-lived access tokens with refresh).
* Updated KK3 row in 5.9: **FIXES (expiry) · OWNED RISK (revocation, 24 h theft window).**

### 5.10 DEFINITION OF DONE — Step 10 check

| Criterion | Met? |
| :--- | :--- |
| Every sub-component has a Step-6 status | ✅ Channels/UI D1 · API D2 · Identity D3, D4 · Sessions D5 (OPEN), D6 · Request/Response D7, D8 (OPEN) |
| Trajectory traced start to finish | ✅ Flows A, B, C (§5.3). Flow B's Slack leg is out of v1 scope per D1 |
| Boundary explicit | ✅ §5.4 |
| Failure grid exists + Step 9 run | ✅ §5.8, §5.9 |
| Logged with reasoning | ✅ This section + decision table rows |

### 5.11 MATERIALIZE — Step 11

* **Page:** `architecture.tldr` → `LLD - [1] User & Application` (generator: [`generate_lld_user_app.py`](./generate_lld_user_app.py)). Contents: boundary, Flows A/B/C, 5 sub-component cards (mechanic + status), failure grid with step-9 effects, decision-log summary.
* **Shallower duplicate on page 1: trimmed to pointers (user choice (a), 2026-09-24).** Page-1 nodes `[1] User Channels`, `[2] API Gateway & Identity` and `[13] Response Delivery Engine` now carry a one-line v1 summary + "→ page: LLD - [1] User & Application". The same text is in [`generate_architecture_tldr.py`](./generate_architecture_tldr.py) so a re-run keeps it.
  * *Why:* two page-1 lines contradicted decisions: "Web · Mobile · Chat SDK · Slack · Teams · Email" vs UA-D1 web-only, and "SSE / WebSocket" vs UA-D2 SSE-only. The others were less detailed (no tiers, no inbox path).
  * *Left untouched:* `[14] User Client` node, the "Stream to User" / "Lifecycle Completed" arrows, the "State Sync" arrow to Data (Comp 8), and the page-1 failure box `[1-4] Ingress & Edge` (still accurate).
* **Noted, not changed (outside this component's page):** the Orchestration page (`generate_lld_page.py`, box "5. Persistence & Response Delivery") still says "SSE / WebSocket token streaming". It conflicts with UA-D2 and presumes UA-D8.

---

## 6. Component Loop [2/15] — Knowledge & Retrieval

> **Loop status:** 🟡 IN PROGRESS · Steps 1–10 complete (DoD met) · ✅ CLOSED (2026-09-24) · Steps 1–11 complete · Page-1 `[7]` trimmed to pointer  
> **Decision ID prefix:** `KR-` (forks `KR-F#`, decisions `KR-D#`)  
> **Architecture mapping:** thoughts.md Component 3 ≈ architecture node `[7] Knowledge & RAG Retrieval` · existing sketch in [`low_level_design.md`](./low_level_design.md) Component [7]

### 6.1 GROUND — Raw Material (not decisions)

**From the master report (rich for this component):**
* **Chunking:** recursive character (cheap; severs condition→action pairs and tables) · semantic (cosine-divergence boundaries) · **Late Chunking** (Günther et al., 2024): embed the whole document (≤8,192 tokens) with a long-context encoder, then mean-pool per chunk span so chunks keep global context (fixes co-reference such as "restart *it*").
* **Hybrid retrieval:** dense bi-encoders displace rare alphanumeric tokens (`ERR_SSL_PROTOCOL_ERROR`, `BUG-8192`). **BM25** gives exact match. **SPLADE** (Formal et al., 2021) gives learned sparse expansion. **RRF** `Σ 1/(k + r_m(d))`, k = 60, fuses lists without score calibration. Report figure: hybrid RRF 5–15 ms.
* **Reranking:** cross-encoders (Cohere Rerank, BGE-Reranker-v2-m3) over about the top 50, 30–60 ms. **ColBERT** late interaction (Khattab & Zaharia, 2020), MaxSim `Σ_i max_j E_Q,i · E_D,jᵀ`.
* **Query transformation:** **HyDE** (Gao et al., 2022) embeds a hypothetical answer. **Step-Back** (Zheng et al., 2023) abstracts to concepts. **LongLLMLingua** (Jiang et al., 2023) compresses up to 4× and addresses **Lost in the Middle** (Liu et al., 2023).
* **Adaptive/corrective:** **CRAG** (Yan et al., 2024) grades retrieval and takes a corrective action. **Self-RAG** (Asai et al., 2023) uses reflection tokens.
* **Human/organizational:** **SECI** (Nonaka & Takeuchi, 1995) · **Cognitive Load Theory** (Sweller, 1988) · **Information Foraging** (Pirolli & Card, 1999; information scent, patch leaving) · **KCS v6** ("reuse is review", Solve Loop / Evolve Loop).
* **Report synthesis:** Small-to-Big (single-concept units of 300–800 words) · **Dynamic scent gate** `D = {d ∈ Top-N | σ(CE(Q,d)) ≥ τ_scent}`, which halts generation if none pass · **closed-loop vector governance** (resolutions feed back into index weights).
* **Report roadmap:** Phase 2 (months 3–4) hybrid + cross-encoder + late chunking. Report claims +28% NDCG@10 and 4× token reduction.

**From general engineering practice:**
* **Baselines / benchmarks:** BEIR (Thakur et al., 2021): BM25 is a strong zero-shot baseline that many dense models fail to beat out of domain. MTEB (Muennighoff et al., 2023) for embedder choice. HNSW (Malkov & Yashunin, 2018). NDCG (Järvelin & Kekäläinen, 2002), MRR, Recall@k.
* **Contextual Retrieval** (Anthropic, 2024): prepend an LLM-generated document-context sentence to each chunk before both embedding and BM25. Reported −49% failed retrievals, −67% with reranking. It is an indexing-time cost, not a query-time one.
* **Parent-document / small-to-big retrieval** (match the small chunk, return the parent section).
* **Conversational query rewriting:** Rewrite-Retrieve-Read (Ma et al., 2023). Follow-ups ("what about the second one?") are not standalone queries.
* **RAG evaluation:** RAGAS context precision/recall (Es et al., 2024) · ARES (Saad-Falcon et al., 2023) · TruLens context relevance.
* **Security of the knowledge layer:** **OWASP Top 10 for LLM Apps 2025, LLM08 "Vector and Embedding Weaknesses"** (cross-tenant leakage, poisoning, access-control gaps) · **indirect prompt injection** through retrieved content (Greshake et al., 2023) · **PoisonedRAG** (Zou et al., 2024): a few crafted docs can steer answers · **embedding inversion** (Morris et al., 2023, vec2text): embeddings can leak close to the original text, so embeddings of PII are PII.
* **Compliance:** GDPR Art. 17 erasure applies to derived artifacts (chunks, embeddings, caches), not just the source doc.
* **Ingestion hygiene:** layout-aware parsing for PDFs/tables · near-duplicate removal (MinHash, Broder 1997) · change-data-capture (CDC) from source systems · document versioning (product version metadata such as `v4.2`).

**Pre-existing leanings in repo docs (NOT checkpointed decisions for this component):**
* `low_level_design.md` [7]: "Hybrid retrieval with RRF is mandatory". Candidates: Qdrant / Weaviate / pgvector, Cohere Rerank v3 / BGE, LlamaIndex, text-embedding-3-large / bge-large.
* `checkpoint.md` ADP-03: the RAG slot (35%) is labeled "ColBERT MaxSim + BM25 ranked passages". This is **inconsistent** with the LLD's "dense + BM25 + cross-encoder". Resolve under KR-F7/KR-F9.
* Canonical scenario (`request_response_lifecycle_example.md`): retrieves the v4.2 release-notes passage (0.94) and **Known Bug BUG-8192** (0.89).

### 6.2 DECOMPOSE

| Sub-component | Mechanic (what it does) |
| :--- | :--- |
| **Knowledge Sources** | Registry of what counts as knowledge: source systems, owner, trust tier, audience (customer-visible vs internal), tenant scope, product-version scope. |
| **Ingestion** | Connectors + CDC/batch sync. Parse (layout/table/code aware), clean, dedupe, attach metadata and ACLs. Propagate updates and **deletions**. |
| **Indexing** | Chunk, embed, and build sparse + dense indexes. Version the index. Enforce the tenant/ACL layout in index structure and payloads. |
| **Retrieval** | Takes a retrieval request from Orchestration: query transformation, first-stage candidate generation, ACL filtering. |
| **Ranking** | Fusion, reranking, admission cutoff (how many passages, or none), citation metadata packaging. |
| **Retrieval Evaluation** | In-pipeline retrieval quality signals (no-hit/abstain rates, per-query scores) and the retrieval-specific metrics contract. Offline golden-set harness is Comp 9 (see boundary). |

### 6.3 TRACE THE TRAJECTORY

**Flow A — Offline: ingestion & indexing (Known Bug BUG-8192 is filed and later corrected)**
1. **Knowledge Sources**: A Jira "Known Issues" project is registered as a source: owner = Support Eng, trust = operational, audience = ? (internal vs customer-visible, see **KR-F2**), scope = all tenants, product ∈ {v4.2}.
2. **Ingestion**: CDC webhook (or batch, see **KR-F3**) fires on create. Parse the issue (title, description, workaround, affected versions), strip boilerplate, dedupe against near-identical tickets, attach metadata `{source, doc_id, version, product_version, audience, tenant_scope, acl_groups, updated_at, url}`.
3. **Indexing**: Chunk (strategy per **KR-F4**) → embed (dense) + tokenize (sparse) → upsert into the index layout chosen in **KR-F5** → bump the index version.
4. **Update**: BUG-8192 is resolved in v4.2.1. CDC marks the doc superseded, and chunks are re-indexed with `status = fixed_in:v4.2.1`.
5. **Delete**: a source doc is removed or a GDPR erasure arrives. A tombstone propagates to chunks + embeddings (+ notifies Comp 12 to invalidate any semantic cache hits derived from them).

**Flow B — Online: query (Sarah @ acme-corp, T2, "DB failed over after v4.2 upgrade … $12,400 overage")**
1. **Retrieval (in)**: Orchestration (Comp 2) sends `RetrievalRequest {query_text, recent_turns, tenant_id, principal_tier, obo_token (UA-D4), filters {product_version: v4.2}, budget {max_passages, max_tokens}}`.
2. **Retrieval, query transform** (**KR-F8**): e.g. rewrite to a standalone query + extract exact identifiers (`v4.2`, `failover`, `Err 504`, `overage`).
3. **Retrieval, candidates** (**KR-F7**): sparse + dense (or other) first-stage → top-N per modality, **ACL-filtered** by tenant / tier / audience (**KR-F5**, **KR-F6**). A T0 anonymous request only sees the public KB (from UA-D3/D4).
4. **Ranking**: fuse (e.g. RRF) → rerank (**KR-F9**) → admission cutoff (**KR-F10**): release-notes passage 0.94 and BUG-8192 0.89 admitted. Or **zero admitted, returned as an explicit `no_evidence` result, not an empty list that orchestration mistakes for "nothing needed".**
5. **Ranking (out)**: `RankedEvidence {passages[{text, doc_id, chunk_id, version, url, audience, score, rank}], retrieval_trace_id, abstained: bool}`
   → **HANDOFF** to Orchestration Context Management (packs into the 35% RAG slot, ADP-03) and to Safety (Comp 7, quarantined parsing of untrusted retrieved text).
   → Citation metadata later surfaces as a UA-D7 `citation` event (via Orchestration, not directly).
6. **Retrieval Evaluation**: emits `retrieval_trace {query, rewritten, candidates, scores, admitted, latency}` → Observability (Comp 10) and Evaluation (Comp 9).

**Flow C — Quality loop (KCS Evolve Loop: gaps become knowledge)**
1. **Retrieval Evaluation**: aggregates `no_evidence` rates / low-scent queries by topic (e.g. many "overage after failover" queries before BUG-8192 existed).
2. → Content-gap report **handed off** to Continuous Improvement (Comp 16) and Human-in-the-Loop resolutions (Comp 13).
3. A specialist resolves the case, and an article is authored (KCS "externalization").
4. → The new article enters **Knowledge Sources** → Flow A. *(Whether resolved tickets are ingested directly is **KR-F1**.)*

### 6.4 BOUNDARY

| | |
| :--- | :--- |
| **Receives (upstream)** | **Offline:** raw documents + ACLs + change events from source systems (Confluence / Zendesk Guide / Jira / release notes; exact set per KR-F1). **Online:** `RetrievalRequest` from Orchestration (Comp 2), carrying tenant, tier and on-behalf-of token from Comp 1 (UA-D3/D4). |
| **Hands off (downstream)** | `RankedEvidence` (or explicit `no_evidence`) → Orchestration Context Management · retrieved text flagged *untrusted* → Safety (Comp 7) · `retrieval_trace` → Observability (10) + Evaluation (9) · content-gap reports → Continuous Improvement (16) · deletion tombstones → Cost/Cache (12). |
| **Does NOT own** | **Context packing / compression into the prompt** (Comp 2 ADP-03; LongLLMLingua, if used, lives there) · **Customer/account facts & past conversations** (Memory, Comp 4) · **Live account data** such as invoices and SLA tier (Tools, Comp 5: *knowledge = documents; live data = tool calls*) · **Semantic response cache** (Comp 12) · **Authorization *policy*** such as who may see what (Comp 7; this component *enforces* it at query time) · **Injection screening of retrieved text** (Comp 7) · **Answer-level groundedness / faithfulness** (Comp 9 / confidence gate) · **Offline golden sets, regression harness** (Comp 9) · **Raw document storage/backups** (Comp 8 Knowledge Data; this component owns derived chunks, embeddings and indexes) · **Embedding/reranker model hosting and cost** (Comp 12 Model Selection). |

### 6.5 SURFACE THE FORKS (undecided; awaiting user)

| Fork | Sub-component | Tension | Options | Status |
| :--- | :--- | :--- | :--- | :--- |
| **KR-F1** | Knowledge Sources | Coverage vs. authority (and PII) | (a) Curated KB only: published articles, policies, release notes · (b) (a) + operational sources: known-bug tracker, incident postmortems, runbooks · (c) (b) + raw resolved tickets / chat transcripts (long tail, but noisy, PII-laden, injection surface) | ✅ (a)+(b)+(c) conditional — see KR-D1 |
| **KR-F2** | Knowledge Sources | Internal-only content | (a) Never index internal-only docs · (b) Index; the agent may *use* them to reason but never quote/cite them to customers · (c) Per-chunk `audience` label; only customer-visible chunks can be cited, and internal ones go to HITL/specialist views only | ✅ (c) conditional — see KR-D2 |
| **KR-F3** | Ingestion | Freshness vs. cost/complexity | (a) Scheduled batch re-index (e.g. nightly) · (b) CDC/webhooks near-real-time for all sources · (c) CDC for volatile sources (bugs, status, release notes) + batch for stable ones. *Deletion propagation SLA is decided alongside.* | ✅ (a) conditional — see KR-D3 |
| **KR-F4** | Indexing | Chunk quality vs. indexing cost | (a) Recursive fixed-size · (b) Structure-aware (headings/tables/code) + parent-child small-to-big · (c) Late chunking (long-context embedder) · (d) Contextual Retrieval: LLM-generated context prefix per chunk, used by both dense and BM25 | ✅ (b) — see KR-D4 |
| **KR-F5** | Indexing | Tenant isolation strength vs. ops cost | (a) One shared index + mandatory metadata pre-filter (tenant, audience, ACL groups) · (b) Physical index/collection per tenant · (c) Shared *global/public* index + per-tenant *private* index, queried together | ✅ (c) — see KR-D5 |
| **KR-F6** | Retrieval | ACL freshness | (a) ACLs copied at ingestion (stale until next sync) · (b) Live permission check against the source system for the top-N at query time using the OBO token · (c) Copied ACLs + live check only for restricted/private docs | ✅ (c) — see KR-D6 |
| **KR-F7** | Retrieval | First-stage recall vs. complexity | (a) Dense only · (b) Hybrid BM25 + dense, RRF · (c) Hybrid SPLADE + dense, RRF · (d) ColBERT late-interaction (+ BM25). *Resolves the LLD-vs-ADP-03 inconsistency.* | ✅ (b) — see KR-D7 |
| **KR-F8** | Retrieval | Recall vs. latency and drift | (a) None: embed the raw last user turn · (b) Conversational rewrite to a standalone query + exact-identifier extraction · (c) (b) + multi-query / HyDE / step-back expansion | ✅ (b) — see KR-D8 |
| **KR-F9** | Ranking | Precision vs. latency vs. data residency | (a) No reranker (fused order) · (b) Hosted cross-encoder (e.g. Cohere Rerank; tenant text leaves your boundary) · (c) Self-hosted cross-encoder (e.g. BGE-reranker-v2-m3) · (d) LLM-as-reranker | ✅ (d) — see KR-D9 |
| **KR-F10** | Ranking | Abstain rate vs. hallucination | (a) Fixed top-k always passed · (b) Calibrated score threshold τ (scent gate); return `no_evidence` if none pass · (c) CRAG-style evaluator: grade → correct (re-query / broaden filters) → else `no_evidence` | ✅ (a) conditional — see KR-D10 |
| **KR-F11** | Retrieval Evaluation | Where retrieval quality is measured | (a) Offline golden set only (harness owned by Comp 9) · (b) (a) + online implicit signals (no-evidence rate, citation clicks, thumbs, re-asks) · (c) (b) + sampled LLM-judge scoring of production retrievals (context precision/recall) | ✅ (a)+(b)+(c) — see KR-D11 |

### 6.6 RESOLVE — Step 6 (user decisions, 2026-09-24)

Options in F1 and F11 are cumulative, so the user's "all three" = option (c).

| ID | Sub-component | Decision | Status | Reasoning / consequences captured |
| :--- | :--- | :--- | :--- | :--- |
| **KR-D1** | Knowledge Sources | **All tiers:** curated KB + operational (known bugs, postmortems, runbooks) + raw resolved tickets / chat transcripts. | ✅ CONFIRMED – conditional | Maximum coverage. The canonical scenario (BUG-8192) is retrievable. **Where raw tickets live and whether they are PII-scrubbed is still open (KR-Q1, KR-Q2).** Logged risk: raw tickets are the noisiest, most PII-laden, most injection-exposed source (PoisonedRAG / Greshake et al.). |
| **KR-D2** | Knowledge Sources | **`audience` label** (customer-visible / internal). Only customer-visible text may be cited to customers. Internal content is shown only in HITL/specialist views. | ✅ CONFIRMED – conditional | BUG-8192's internal notes can inform a specialist without leaking to Sarah. **Label granularity vs. parent-section expansion (KR-D4) is open (KR-Q4).** |
| **KR-D3** | Ingestion | **Scheduled batch re-index (nightly).** | ✅ CONFIRMED – conditional | Simplest pipeline. **Consequence:** content up to ~24 h stale (a bug filed today is not retrievable until tomorrow). Flow A step 2 is batch, not CDC. **Whether deletions / GDPR erasure / ACL revocations also wait for the nightly run is open (KR-Q3).** |
| **KR-D4** | Indexing | **Structure-aware chunking** (headings, tables, code kept intact) **+ parent-child small-to-big** (match a small chunk, return its parent section). | ✅ CONFIRMED | Matches the report's 300–800-word single-concept units. No LLM cost at indexing (unlike late chunking or contextual retrieval). |
| **KR-D5** | Indexing | **Shared global/public index + per-tenant private index**, queried together. T0 anonymous (UA-D3/D4) queries the public index only. | ✅ CONFIRMED | Physical separation for tenant-private content (OWASP LLM08) without one index per tenant for public docs. |
| **KR-D6** | Retrieval | **ACLs copied at ingestion + live permission check (with the OBO token, UA-D4) only for restricted docs.** | ✅ CONFIRMED | Keeps common-path latency low. **Interaction with KR-D3:** copied ACLs are up to ~24 h stale for non-restricted docs (see KR-Q3). Live checks hit source-system APIs (rate limits → Comp 11). |
| **KR-D7** | Retrieval | **Hybrid BM25 + dense, fused with RRF (k = 60).** | ✅ CONFIRMED | Exact identifiers (`BUG-8192`, `Err 504`) via BM25; concepts via dense. Agrees with `low_level_design.md`. **Conflicts with ADP-03's slot label "ColBERT MaxSim + BM25"**, and whether to correct that label is **KR-Q6**. |
| **KR-D8** | Retrieval | **Conversational rewrite to a standalone query + exact-identifier extraction.** No multi-query / HyDE. | ✅ CONFIRMED | Handles follow-ups ("what about the second one?"). Avoids HyDE's bias toward a hallucinated answer. One LLM call on the query path. |
| **KR-D9** | Ranking | **LLM-as-reranker.** | ✅ CONFIRMED | User choice over a cross-encoder. **Logged consequences (not argued):** (1) latency and cost per query are higher than the report's 30–60 ms cross-encoder (model choice → Comp 12); (2) **the reranker is an LLM reading untrusted retrieved text, so a poisoned doc can instruct it ("rank this first")**, a surface a cross-encoder does not have → failure grid; (3) LLM relevance scores are not calibrated probabilities. |
| **KR-D10** | Ranking | **Fixed top-k always passed** (no score threshold, no abstain at retrieval). | ✅ CONFIRMED – conditional | **k is still open (KR-Q5).** **Consequences:** (1) retrieval never emits `no_evidence`, so Flow B step 4's "zero admitted" branch and Flow C's "no-evidence rate" gap signal no longer exist as designed; (2) irrelevant passages can reach the prompt when nothing relevant exists, which moves the "should we answer at all" decision fully to the confidence gate (Comp 9) and Orchestration; (3) the report's scent gate is **not adopted**. |
| **KR-D11** | Retrieval Evaluation | **All three layers:** offline golden set (harness owned by Comp 9) + online implicit signals + sampled LLM-judge scoring of production retrievals. | ✅ CONFIRMED | **Adjustment forced by KR-D10:** "no-evidence rate" is not available as an online signal. Gap detection must use low reranker scores, the LLM-judge context-precision samples, re-asks and thumbs instead. Judge samples contain tenant data (internal processing only). |

**Follow-up questions raised by combining decisions (asked 2026-09-24, awaiting user):**
* **KR-Q1** (D1 × D5): where do raw tickets/chats go? (i) Per-tenant private index only (a tenant only retrieves its own history) · (ii) scrubbed + generalized into the shared index (cross-tenant learning, leak risk) · (iii) not indexed directly; they only seed KCS article drafts that a human publishes.
* **KR-Q2** (D1 × embedding inversion): PII-scrub tickets before chunking/embedding? (i) Yes, masked at ingestion (e.g. Presidio) · (ii) No, rely on per-tenant isolation.
* **KR-Q3** (D3 × D6 × GDPR Art. 17): do deletions / erasures / ACL revocations wait for the nightly batch? (i) Yes, everything nightly (≤ ~24 h exposure) · (ii) content nightly, plus an out-of-band immediate purge for deletions, erasures and ACL revocations.
* **KR-Q4** (D2 × D4): label granularity when a customer-visible child sits in a parent section with internal text. (i) Per chunk: parent expansion drops internal siblings · (ii) per document: a doc with any internal text is internal · (iii) per section.
* **KR-Q5** (D10): value of k. (i) 5 (report: top-50 → top-5) · (ii) budget-driven: as many parent sections as fit the 35% RAG slot (ADP-03) · (iii) other.
* **KR-Q6** (D7 × ADP-03): update ADP-03's RAG-slot label from "ColBERT MaxSim + BM25" to "Hybrid BM25 + dense (RRF), LLM-reranked"? (i) Yes · (ii) leave it.

**Follow-up answers (user, 2026-09-24):**
* **KR-Q1 → (i)** Raw tickets/chats go into **each tenant's private index only**. No cross-tenant learning from tickets. The shared public index holds curated + operational sources.
* **KR-Q2 → (i)** Tickets are **PII-masked at ingestion before chunking/embedding** (Presidio-class). The detection engine and policy are owned by Safety (Comp 7); Ingestion *invokes* it.
* **KR-Q3 → (ii)** Content refreshes nightly, but **deletions, GDPR erasures and ACL revocations are purged immediately** (out-of-band event path). Tombstones also go to Comp 12 (semantic cache).
* **KR-Q4 → (i)** The `audience` label is **per chunk**. Parent-section expansion drops internal sibling chunks before returning.
* **KR-Q5 → (iii) k = 10** parent sections. **Logged consequence:** at 300–800 words per parent, 10 sections ≈ 4k–11k tokens, which can exceed the 35% RAG slot on smaller context windows. Trimming then falls to Orchestration's assembler (ADP-03), outside this component, and 10 passages sit squarely in the "Lost in the Middle" range (Liu et al., 2023).
* **KR-Q6 → (i)** ADP-03's RAG-slot label corrected in this file (§2 ADP-03) and in [`component_5_orchestration_deep_dive.md`](./component_5_orchestration_deep_dive.md). The master report is literature and is left unchanged.

Resulting status changes: KR-D1, D2, D3, D10 move from *conditional* to **✅ CONFIRMED** (their conditions are now stated rules).

### 6.7 EXPERIMENT CHECK — Step 7

No fork was marked for experiment. All eleven stand as the finalized approach, including the follow-up rules above.

**Trajectory as decided (supersedes the placeholders in 6.3):**
* **Flow A:** step 2 is the **nightly batch** (not CDC) plus PII masking for tickets. Tickets are routed to the tenant's **private** index; curated + operational docs go to the **public** index. Step 5 (delete / erasure / ACL revocation) is an **immediate out-of-band purge**.
* **Flow B:** step 2 is a standalone rewrite + ID extraction. Step 3 is BM25 + dense over public (+ own-tenant private if T1/T2), with copied ACLs + a live check for restricted docs. Step 4 is RRF → **LLM rerank → always top-10 parent sections** (internal-audience chunks removed). **The `no_evidence` branch is removed.**
* **Flow C:** the gap signal comes from low reranker scores, judge-sample context precision, re-asks and thumbs, **not** a no-evidence rate.

### 6.8 FAILURE MODES — Step 8 (Known/Unknown grid, scoped to this component's sub-components)

Quadrant definitions follow [`failure_modes_matrix.md`](./failure_modes_matrix.md). Pointed to, not restated: "high cosine similarity but wrong doc" (Q2), "chunk boundary breaks table" (Q1), indirect injection via ingested logs (Q4), cross-tenant *semantic cache* poisoning (Q4, owned by Comp 12).

**Q1 — KNOWN KNOWNS (contract breaches)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| KK1 | Ingestion / Indexing | The nightly batch fails or half-completes. The index serves a mix of old and new versions, or goes stale for another day, with no alarm. |
| KK2 | Indexing / Retrieval | A query hits the **wrong tenant's private index** (tenant taken from a request field instead of the verified token). |
| KK3 | Ranking | The LLM reranker returns malformed output: IDs not in the candidate set, duplicates, or fewer than 10 → parse failure or empty context. |
| KK4 | Retrieval | A T0 anonymous request reaches a private index. |
| KK5 | Indexing | Embedding-model upgrade without a full re-index. Query and stored vectors come from different models, so similarity is silently meaningless. |
| KK6 | Ingestion | An immediate purge (Q3-ii) removes the index entries but **misses derived copies**: parent-section caches, semantic cache (Comp 12), LLM-judge samples and golden sets (Comp 9). |

**Q2 — KNOWN UNKNOWNS (magnitude unknown)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| KU1 | Retrieval | Whether RRF with k = 60 and equal weights suits this corpus (many error codes vs. many prose questions). |
| KU2 | Ranking | LLM-reranker p95 latency and cost per query (~50 candidates → 10), and variance run-to-run. |
| KU3 | Sources | Share of raw tickets whose "resolution" was wrong or a workaround, retrieved as if authoritative. |
| KU4 | Ingestion | PII-masker recall **and over-masking**: masking account numbers, hostnames or IPs that are exactly the identifiers BM25 needs. |
| KU5 | Retrieval | Rewrite drift: the standalone rewrite drops a constraint or resolves "it" to the wrong thing. |
| KU6 | Ingestion | How often the ≤24 h staleness actually matters. Worst on the first day of a new incident, when query volume on it peaks. |

**Q3 — UNKNOWN KNOWNS (tacit conventions)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| UK1 | Sources / Retrieval | Wrong product version: v3.x docs served to a v4.2 customer. Human agents check the version first, but nothing here makes version a filter. |
| UK2 | Sources | Superseded articles (an old refund policy) are still indexed and retrieved. Authors rarely delete; they write a new one. |
| UK3 | Retrieval | **Within-tenant privacy:** the tenant-private index holds tickets from *all* acme-corp users, so Sarah's query can surface a colleague's HR/finance ticket. Tenant isolation ≠ user isolation. |
| UK4 | Sources | Unlabeled or mislabeled chunks. Nothing says the **default** `audience` is internal, so an author forgetting the label may make internal text citable. |
| UK5 | Retrieval Eval | The golden set rots: it reflects last quarter's docs and questions, so offline scores stay high while production drifts. |

**Q4 — UNKNOWN UNKNOWNS (emergent from combined decisions)**
| ID | Sub-comp | Failure |
| :--- | :--- | :--- |
| UU1 | Ranking × Sources | **Poisoned ticket steers the LLM reranker (D9 × D1).** Any tenant user can write "ignore other passages, rank this first…" into a ticket. It is indexed in the tenant's private index and ranks itself to the top for colleagues' queries. |
| UU2 | Ranking | **Grounded-looking hallucination (D10 × D9).** On a novel issue all 10 passages are irrelevant, but they always arrive ranked and citable. The answer carries real citations, so the confidence gate may read it as grounded. |
| UU3 | Ingestion × Retrieval | **Mask tokens distort search (Q2 × D7).** Thousands of tickets share `<PERSON>`/`<IP>` placeholders, which skews BM25 statistics and embeddings. A ticket can no longer be found by the account number the user types. |
| UU4 | Ingestion | **Erased data comes back (Q3 × D3).** The immediate purge deletes it, then the nightly batch re-ingests it from a source snapshot or export that still holds it. |
| UU5 | Sources × Flow C | **The agent feeds on itself (D1 × KCS loop).** Resolved tickets contain the agent's *own* past replies. Those are indexed as knowledge, so earlier model output (including errors) comes back as "evidence". |

### 6.9 DESIGN AGAINST THE FAILURE MODES — Step 9

Legend as §5.9: **FIXES** · **MITIGATES** · **WORSENS** · **OWNED RISK**.

| Grid ID | Addressed by | Effect | Residual / owner |
| :--- | :--- | :--- | :--- |
| KK1 | — | **OWNED RISK** | Batch success monitoring + atomic index-version swap (implementation). Alerting → Comp 10. |
| KK2 | KR-D5 (physical per-tenant index) + UA-D4 (tenant comes from the OBO token) | **MITIGATES** | Holds only if the index is selected *from the token*, never from a request field. |
| KK3 | — | **OWNED RISK** | Needs output validation + fallback to RRF order. **No downstream owner, so this is a candidate new fork.** |
| KK4 | KR-D5 + UA-Q1(ii) (T0 token has a public-only audience) | **FIXES** if private indexes require a tenant-scoped token | — |
| KK5 | — | **OWNED RISK** | Model version stamped on the index; re-index on change. Model choice → Comp 12. |
| KK6 | KR-Q3(ii) immediate purge | **FIXES** for primary indexes · **OWNED** for derived copies | Comp 12 cache invalidation already in the tombstone path. Comp 9 judge samples and golden sets are **not**. |
| KU1 | KR-D7 + KR-D11 (golden set measures it) | **MITIGATES** | Tuning is a parameter, not a fork. |
| KU2 | KR-D9 **WORSENS** | **OWNED RISK** | Latency/cost budget → Comp 11/12. Measured via KR-D11. |
| KU3 | KR-Q1(i) limits the blast radius to one tenant · KR-D11(c) judge can catch it | **MITIGATES** | No quality filter on which tickets get ingested. **Owned.** |
| KU4 | KR-Q2(i) **creates** it | **OWNED RISK** | Measure masked vs. unmasked retrieval on the golden set (KR-D11a). |
| KU5 | KR-D8 creates it · KR-D11 measures it | **OWNED RISK** | — |
| KU6 | KR-D3 creates it | **OWNED RISK (accepted trade-off)** | Revisit if incident-day misses show up in Flow C gap signals. |
| UK1 | KR-D8 (extracts `v4.2`) | **MITIGATES** only if the extracted version is used as a filter | Not decided. **Owned.** |
| UK2 | KR-D3 re-index picks up new articles, but old ones stay | **OWNED RISK** | Needs a `status: superseded` field in source metadata. |
| UK3 | KR-Q1(i) **creates** it (tenant-wide, not user-wide) · KR-D6 live check helps only for docs marked restricted | **OWNED RISK** | Per-user/role ACLs on tickets → policy owner Comp 7. **Real exposure in v1.** |
| UK4 | KR-D2 + Q4(i) create the label, but there is no default | **OWNED RISK** | "Default = internal" is a one-line rule, not yet decided. **Candidate new fork.** |
| UK5 | KR-D11(b)+(c) online signals and judge counter golden-set rot | **MITIGATES** | Golden-set refresh cadence → Comp 9. |
| UU1 | KR-D9 **WORSENS** (instruction-following reranker) · Comp 7 quarantine applies *after* retrieval, not to the reranker | **OWNED RISK** | Reranker runs on untrusted text inside this component. **Candidate new fork.** |
| UU2 | KR-D10 × KR-D9 **create** it | **OWNED RISK** | Comp 9 gate must judge relevance, not citation presence. Hand-off note to Comp 9. |
| UU3 | KR-Q2 × KR-D7 **create** it | **OWNED RISK** | Measure via KR-D11(a). A masking approach that keeps identifiers searchable is a Comp 7 question. |
| UU4 | KR-Q3 × KR-D3 **create** it | **OWNED RISK** | Batch must check a tombstone list before re-ingesting. **Candidate new fork.** |
| UU5 | KR-D1 **creates** it | **OWNED RISK** | Agent-authored text in tickets must be excluded or labeled. **Candidate new fork.** |

**Summary (before §6.9a):** 2 FIXES (KK4, KK6 primary) · 5 MITIGATES · 15 OWNED RISKS (1 accepted: KU6). **Five owned risks sit inside this component with no downstream owner. They are flagged as candidate new forks:** KK3 reranker output validation · UK4 default label · UU1 reranker injection · UU4 re-ingestion of erased data · UU5 agent-authored text in tickets.

#### 6.9a Candidate forks resolved (user decisions, 2026-09-24)

| ID | Grid | Decision | Status | Effect on the grid / consequences |
| :--- | :--- | :--- | :--- | :--- |
| **KR-D12** | KK3 | **Reranker validation wrapper:** schema-check the reranker output (scores, IDs ∈ candidate set, no duplicates, order, count). On any failure, **automatically fall back to the original RRF ranking.** | ✅ CONFIRMED | KK3 **FIXED** (a malformed output can no longer empty or corrupt the context). Residual: fallback silently lowers quality, so the **fallback rate is emitted as a metric** → Comp 10. |
| **KR-D13** | UK4 | **`audience = internal` is the system-level default.** Customer-visible/public needs an **explicit override**. | ✅ CONFIRMED | UK4 **FIXED** for unlabeled content. Residual (owned): an explicit *wrong* override. **Consequence:** at onboarding, every existing source is internal until someone labels it, so citable coverage starts low. Public KB articles need a bulk "customer-visible" labeling step. |
| **KR-D14** | UU1 | **Screen/sanitize retrieved text *before* it reaches the LLM reranker.** All retrieved content is treated as untrusted input. | ✅ CONFIRMED | UU1 **MITIGATED** (injection detectors have imperfect recall; a screened passage can still carry a subtle instruction). **Boundary update:** the screening *engine/policy* stays with Safety (Comp 7), but it is now **invoked inside this component**, between candidate generation and rerank (Flow B step 3½). Cost: screening ~50 candidates per query adds latency (→ KU2, Comp 11). |
| **KR-D15** | UU4 | **Deletion-aware ingestion:** check the deletion list → filter → ingest → verify, plus a **post-ingestion deletion check** after every batch. | ✅ CONFIRMED | UU4 **FIXED** for re-ingestion from stale snapshots. Residual: the deletion list itself must be durable and complete (storage → Comp 8). |
| **KR-D16** | UU5 | **Provenance metadata on every ticket chunk:** `source = human \| agent`. | ✅ CONFIRMED – conditional | Makes agent-authored text identifiable. **Whether agent content is *excluded* from retrieval or *kept but labeled* is still open (KR-Q7).** Exclude → UU5 FIXED. Label only → UU5 MITIGATED (the LLM can still lean on labeled agent text). |

* **KR-Q7** (D16): agent-authored ticket content: (i) **exclude** from retrieval (index human text only) · (ii) **keep but label** (e.g. "agent reply, unverified"), so it can be retrieved but is visibly marked.
* **KR-Q7 → (ii) keep but label (user, 2026-09-24).** Agent-authored chunks stay retrievable and carry a visible "agent reply, unverified" marker. KR-D16 → ✅ CONFIRMED. **UU5 → MITIGATED, not fixed:** the model can still rely on labeled agent text, so the self-reinforcing loop is weakened but not broken. Residual (owned): whether Comp 9's confidence gate discounts agent-provenance evidence. Hand-off note to Comp 9.

**Summary (after §6.9a):** 5 FIXES (KK3, KK4, KK6 primary, UK4, UU4) · 7 MITIGATES (+UU1, UU5) · 10 OWNED RISKS. No candidate forks remain without an owner or a decision.

### 6.10 DEFINITION OF DONE — Step 10 check

| Criterion | Met? |
| :--- | :--- |
| Every sub-component has a Step-6 status | ✅ Sources D1, D2 · Ingestion D3 · Indexing D4, D5 · Retrieval D6, D7, D8 · Ranking D9, D10 · Retrieval Eval D11 (all CONFIRMED; none OPEN) |
| Trajectory traced start to finish | ✅ Flows A/B/C (§6.3), updated to the decisions in §6.7 |
| Boundary explicit | ✅ §6.4 |
| Failure grid exists + Step 9 run | ✅ §6.8, §6.9 |
| Logged with reasoning | ✅ §6.6–6.10 + decision table rows |

### 6.11 MATERIALIZE — Step 11

* **Page:** `architecture.tldr` → `LLD - [2] Knowledge & Retrieval` (generator: [`generate_lld_knowledge.py`](./generate_lld_knowledge.py)). Contents: boundary, Flow A (offline ingest + instant purge path), Flow B (online query, 2 rows ①–⑥), Flow C (quality loop), 6 sub-component cards (mechanic + status), failure grid with step-9 effects after §6.9a, decision-log summary + hand-offs to Comp 7/8/9/12.
* **Refactor:** layout helpers moved into [`lld_layout.py`](./lld_layout.py) (shared by both component pages). The component 1 page was regenerated and verified **identical** (51/51 records).
* **Shallower duplicates:**
  * Page 1 node `[7] Knowledge & RAG Retrieval` ("Hybrid Vector + BM25 Search / Document Reranking & Chunking") was consistent with the decisions, just shallower. **Trimmed to a pointer (user choice (a), 2026-09-24):** "BM25 + dense · LLM rerank · top-10 → see LLD - [2] Knowledge & Retrieval". The same text is in [`generate_architecture_tldr.py`](./generate_architecture_tldr.py).
  * *Not on the canvas, noted only:* [`low_level_design.md`](./low_level_design.md) Component [7] now **contradicts** KR-D3 (it says CDC near-real-time; decided: nightly batch), KR-D8 (it says multi-query expansion ×3; decided: no multi-query) and KR-D9 (it says Cohere / BGE cross-encoder; decided: LLM reranker). It also lists a context compressor, which is Orchestration's (boundary §6.4).

*(2026-09-24: a stale duplicate "§5.11 MATERIALIZE" block that had appeared here, showing the page-1 pointer choice as still pending, was deleted at the user's request. The authoritative §5.11 is in §5.)*

---

## 7. Component Loop [3/15] — Memory & State

> **Loop status:** 🟡 IN PROGRESS · Steps 1–4 complete · Step 5/6 awaiting user decisions  
> **Decision ID prefix:** `MS-` (forks `MS-F#`, decisions `MS-D#`)  
> **Architecture mapping:** thoughts.md Component 4 ≈ architecture node `[6] Memory & State Engine` · existing sketch in [`low_level_design.md`](./low_level_design.md) Component [6]

### 7.1 GROUND — Raw Material (not decisions)

**Already decided upstream (inputs to this component, not reopened):**
* **ADP-02:** Temporal outer saga + LangGraph inner loop, with "thread snapshot commits". Durable timers and HITL waits belong to Temporal.
* **ADP-03:** context slot budgets. Profile facts **15%**, dialogue buffer **25%**, scratchpad **10%**. Packing and compaction *execution* belong to Orchestration's assembler. ADP-03 explicitly rejects naive FIFO ("context cliff").
* **UA-D5 (OPEN, revisit here):** session vs. conversation vs. case. **UA-D6:** session 30 min idle / 12 h, and the conversation outlives the session. **UA-D3:** tiers T0 / T1 / T2.
* **KR boundary:** documents and raw tickets belong to Knowledge (tenant-private index, KR-Q1). *Customer facts and past conversations belong here.*

**From the master report:**
* Three tiers: **working** (context window), **short-term/episodic** (session event log in Redis/Postgres, summarization + eviction), **long-term/semantic** (vector + relational: preferences, past resolutions, cross-session entities).
* **Generative Agents** (Park et al., 2023): `Score(m) = α_rec·γ^(t−t_last) + α_imp·S_imp(m) + α_rel·cos(q, m)`, plus a **reflection** engine that turns episodes into abstract facts.
* **MemGPT / Letta** (Packer et al., 2023): OS-style virtual memory. The agent pages memory in and out through function calls.
* **Baddeley** (1974 / 2000): central executive + episodic buffer. **Ebbinghaus** (1885): `R(t) = exp(−t/S)` decay.
* **Transactive memory** (report's "meta-directory"): don't hold all knowledge in one store; know *where* to look.
* Memory table: Zep temporal graph (20–50 ms, "graph reconciliation, prevents hallucinated profile updates") · **CRM system of record** (10–30 ms, multi-year legal audit, deterministic GDPR/CCPA purge).
* Roadmap target: "65% reduction in history token costs; zero cross-session entity amnesia".

**From general engineering practice:**
* **Mem0** (Chhikara et al., 2025): extract candidate facts, then reconcile with existing memory via **ADD / UPDATE / DELETE / NOOP**.
* **Zep / Graphiti** (Rasmussen et al., 2025): **bi-temporal knowledge graph**. Every fact has valid-from/valid-to, so "Acme *was* on us-east-1" is kept, not overwritten.
* **Recursive summarization** for long dialogues (Wang et al., 2023).
* **Benchmarks:** LongMemEval (Wu et al., 2024) and LoCoMo (Maharana et al., 2024). Knowledge updates and temporal reasoning are where memory systems fail most.
* **LangGraph persistence:** *checkpointer* (per-thread state, resumable) vs. *Store* (cross-thread long-term memory).
* **Temporal:** workflow history has hard limits (on the order of 50k events / 50 MB), so long-running workflows need **Continue-As-New**. Workflow state must be deterministic on replay.
* **Event sourcing** (Fowler, 2005): state = fold over events, matching the ADP-01 reducer `S_{t+1} = Reducer(S_t, Δ_t)`.
* **Memory security:** **OWASP Agentic AI Threats & Mitigations (2025), T1 "Memory Poisoning"** · **MINJA** (Dong et al., 2025): injection into agent memory through ordinary interactions · the 2024 "SpAIware" demonstration (Rehberger) of persistent instructions planted in an assistant's long-term memory.
* **GDPR:** Art. 5(1)(c) minimisation, (d) accuracy, (e) storage limitation · Art. 15 access · Art. 16 rectification · Art. 17 erasure, with the **Art. 17(3)(e)** legal-claims exception, which matters because UA flagged transcripts as legal records (*Moffatt v. Air Canada*).
* **Contact-center practice:** "single customer view" in the CRM · Dixon et al. (2010): having to repeat information is a top effort driver.

**Pre-existing items elsewhere (pointed to, not restated):**
* `low_level_design.md` [6]: Redis Stack + Postgres/pgvector, "extraction / retrieval / lifecycle & garbage collection".
* `failure_modes_matrix.md`: "[6] key collision in Redis across concurrent sessions of the same user" (Q1) · "context compaction drops a constraint from 5 turns ago" (Q2) · "creepy over-personalization: referencing a 2-year-old closed ticket" (Q3).
* `user_evaluation_framework.md`: expectation `memory_retrieves_platinum_sla`.

### 7.2 DECOMPOSE

| Sub-component | Mechanic (what it does) |
| :--- | :--- |
| **Conversation Memory** | Ordered turn log per conversation (verbatim) + compaction (summaries / pinned items). Source of the 25% dialogue slot. |
| **Long-Term Memory** | Facts, preferences and episodes about a principal (and maybe their tenant) that persist across conversations. Extraction, reconciliation, retrieval. Source of the 15% profile slot. |
| **Working Memory** | What one agent run holds between steps: intermediate thoughts, tool outputs, retrieved-passage references. Source of the 10% scratchpad slot. *Packing is ADP-03's.* |
| **Agent State** | Durable execution state `S_t`: FSM node, collected parameters, pending approvals, step and trial counters (ADP-04), and checkpoints for crash recovery and resume after HITL waits. |
| **Memory Lifecycle** | Retention, decay, consolidation, rectification, erasure and audit of everything above. |

### 7.3 TRACE THE TRAJECTORY

**Flow A — Hot path: one turn (Sarah @ acme-corp, T2, conversation `conv_…`)**
1. **Agent State**: Orchestration receives the turn (UA envelope) and loads the latest checkpoint for this conversation (FSM node `TRIAGED`, collected params, counters).
2. **Conversation Memory**: returns recent turns verbatim + compacted older history (policy **MS-F2**).
3. **Long-Term Memory**: returns relevant facts for Sarah (scope **MS-F3**): e.g. *"prefers CLI steps"*, *"primary DB us-east-1, replica us-west-2"*, episode *"INV-9821 disputed 2026-09-20"*. Whether *"Acme = Platinum SLA"* comes from here or from the CRM tool is **MS-F6**.
   → **HANDOFF** of dialogue + profile content to Orchestration's assembler (ADP-03 packs 25% / 15%).
4. **Working Memory**: during the run, holds the plan, tool outputs (e.g. a 40 KB billing ledger) and passage refs (handling: **MS-F7**).
5. **Agent State**: checkpointed at each boundary (granularity **MS-F9**; truth location **MS-F8**).
6. **Conversation Memory**: at turn end, the user turn and the delivered response are appended.

**Flow B — Consolidation (async, after the turn or at resolution)**
1. **Long-Term Memory**: extract candidate facts from the new turns (write policy **MS-F5**).
2. Reconcile against existing memory: ADD / UPDATE / DELETE / NOOP, or close a temporal validity interval (representation **MS-F4**).
3. **Conversation Memory**: refresh the rolling summary if older turns fell out of the verbatim window.
4. → Memory-write event → Data (Comp 8, audit) and Observability (Comp 10).

**Flow C — Long wait & resume (HITL approval of the $12,400 credit memo)**
1. **Agent State**: the graph reaches `AWAITING_APPROVAL`. The checkpoint is committed, and Temporal holds the wait (ADP-02; not owned).
2. Sarah's session expires (UA-D6). Conversation and Agent State persist.
3. Approval arrives 3 h later. **Agent State** restores `S_t` exactly, and the graph continues from `AWAITING_APPROVAL` (without re-running earlier tools).
4. Next day Sarah returns in a new session. Whether she lands in the *same* conversation, and how a second conversation about the same invoice links to it, is **MS-F1** (reopened UA-D5).

**Flow D — Lifecycle: retention, correction, erasure**
1. **Memory Lifecycle**: routine expiry/decay per **MS-F10**.
2. Sarah says "we moved the replica to eu-west-1". Rectification: Long-Term Memory updates or invalidates the old fact (Flow B path).
3. GDPR erasure for Sarah: purge long-term facts, summaries, embeddings, working-memory artifacts and old checkpoints (control model **MS-F11**). **Transcripts are legal records:** whether they are kept under Art. 17(3)(e) is a *retention policy* owned by Comp 7/8. This component executes whatever that policy says.

### 7.4 BOUNDARY

| | |
| :--- | :--- |
| **Receives (upstream)** | Turn envelopes and state deltas from Orchestration (Comp 2), carrying `conversation_id`, principal, tenant and tier from Comp 1 · tool results (Comp 5) into Working Memory · delivered responses (Comp 1 Request/Response) for the turn log · erasure / rectification / retention instructions (policy from Comp 7/8). |
| **Hands off (downstream)** | Dialogue content (25% slot) + profile facts (15%) + scratchpad content (10%) → Orchestration assembler · restored `S_t` checkpoints → Orchestration / Temporal activities · memory-write and erasure events → Data (8) audit, Observability (10) · consolidation signals (resolved episodes) → Continuous Improvement (16). |
| **Does NOT own** | **Slot budgets & packing** (ADP-03, Comp 2) · **durable timers / waits / workflow lifecycle** (Temporal, ADP-02, Comp 2) · **session identity & expiry** (Comp 1) · **documents & raw tickets** (Knowledge, Comp 3) · **authoritative account data** such as SLA tier, plan and invoices (CRM via Tools, Comp 5) · **retention/erasure *policy* & PII rules** (Comp 7/8) · **storage engines, backups, transcript archive** (Comp 8) · **semantic answer cache** (Comp 12) · **HITL queues** (Comp 13). |

### 7.5 SURFACE THE FORKS (undecided; awaiting user)

| Fork | Sub-component | Tension | Options | Status |
| :--- | :--- | :--- | :--- | :--- |
| **MS-F1** *(reopens UA-D5)* | Conversation Memory | What is "one conversation"? | (a) One chat thread, independent of session; a new thread starts a new conversation · (b) One continuous conversation per principal (everything ever said) · (c) Three levels: session → conversation (thread) → **case** (the issue, e.g. INV-9821); several conversations can link to one case | ⏳ PENDING |
| **MS-F2** | Conversation Memory | Fidelity vs. tokens | (a) Last N turns verbatim only (FIFO; ADP-03 warns against it) · (b) Last N verbatim + rolling LLM summary of older turns · (c) (b) + **pinned items** (constraints, IDs, commitments) that are never evicted | ⏳ PENDING |
| **MS-F3** | Long-Term Memory | Personalization vs. privacy/exposure | (a) No long-term memory in v1 (conversation-only) · (b) Per-principal facts only · (c) Per-principal + per-tenant shared facts (e.g. "Acme runs us-east-1", visible to all Acme users) | ⏳ PENDING |
| **MS-F4** | Long-Term Memory | Simplicity vs. temporal correctness | (a) Flat fact list + vector search (Mem0-style reconcile) · (b) Temporal knowledge graph with validity intervals (Zep/Graphiti) · (c) Memory stream scored by recency × importance × relevance (Park et al.) | ⏳ PENDING |
| **MS-F5** | Long-Term Memory | Freshness vs. poisoning vs. cost | (a) Auto-extract after every turn · (b) Extract once when the conversation/case resolves · (c) Only explicit or verified facts ("remember that…", or confirmed by a tool result) | ⏳ PENDING |
| **MS-F6** | Long-Term Memory | Speed vs. authority | Account facts (SLA tier, plan, region): (a) stored in memory · (b) **never** stored; always fetched from the CRM tool, and memory holds only soft facts, preferences and episodes · (c) cached from the CRM with TTL + source stamp | ⏳ PENDING |
| **MS-F7** | Working Memory | Prompt size vs. access to detail | Large tool outputs: (a) inline, truncated to the 10% scratchpad · (b) stored **by reference** with an inline summary; the agent can page in detail (MemGPT-style) · (c) discarded after each step | ⏳ PENDING |
| **MS-F8** | Agent State | Single source of truth | (a) LangGraph checkpointer (Postgres) holds cognitive state; Temporal holds only workflow status + checkpoint ID · (b) Temporal history is the truth and LangGraph state is rebuilt from it (history limits, Continue-As-New) · (c) Both hold state, reconciled | ⏳ PENDING |
| **MS-F9** | Agent State | Resume precision vs. write cost | Checkpoint granularity: (a) per turn · (b) per graph node · (c) per node **plus** before and after every side-effecting tool call | ⏳ PENDING |
| **MS-F10** | Memory Lifecycle | Usefulness vs. storage limitation | (a) Keep until explicitly deleted · (b) Fixed TTL per memory type · (c) Decay (facts that are used get renewed, Ebbinghaus-style) + a hard maximum TTL | ⏳ PENDING |
| **MS-F11** | Memory Lifecycle | User control vs. build effort | (a) Erasure/correction only via a back-office request (GDPR SLA ≤ 1 month) · (b) User-visible "what the agent remembers" with self-service correct/delete (Art. 15/16/17) · (c) (a) in v1, (b) later | ⏳ PENDING |
