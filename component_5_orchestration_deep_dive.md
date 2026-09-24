# Component [ 5 ]: Agent Runtime & Orchestration Core — Low-Level Engineering Specification & Deep Dive

> **Companion Visual Diagram:** [`architecture.tldr`](./architecture.tldr) (Page 2: `LLD - Agent Orchestration & Planning Core`)  
> **Architectural Decisions Checkpoint:** [`checkpoint.md`](./checkpoint.md)  
> **Master Literature Review:** [`Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md`](<./Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md>)

---

## 1. Executive Summary & Architectural Mission

The **Agent Orchestration Core** serves as the central cognitive control plane of the enterprise support agent. It is responsible for:
1. Translating unstructured customer dialogue into structured, verifiable execution trajectories.
2. Enforcing regulatory, security, and operational invariants on all system state mutations.
3. Balancing fast deterministic execution for routine standard operating procedures (SOPs) against deep deliberative reasoning for novel, complex diagnostic cases.
4. Providing self-healing, crash-resilient execution with guaranteed recovery and human escalation.

---

## 2. Theoretical Foundations (Literature Review Mapping)

### 2.1 Dual-Process Cognitive Theory (System 1 vs. System 2)
* **Foundational Literature:** Stanovich & West (2000); Kahneman (2011).
* **Engineering Formulation:**
  * **System 1 (Deterministic FSM Statechart):** Rapid, predictable, zero-temperature execution of known Enterprise SOPs (e.g. invoice retrieval, password reset, order tracking). $S_{t+1} = \text{Reducer}(S_t, \Delta_t)$.
  * **System 2 (Deliberative ReAct & Replanner):** Slow, reflective, multi-step problem decomposition for unstructured outages and cross-system diagnostics. $\tau_t = (o_0, r_0, a_0, \dots)$.

### 2.2 Clinical Triage & Acuity Protocols (ESI / MTS)
* **Foundational Literature:** Emergency Severity Index (ESI) Handbook (Gilboy et al., 2011); Manchester Triage System (Mackway-Jones, 1997).
* **Engineering Formulation:**
  * Queries undergo an upfront acuity and resource estimation pass before any cognitive reasoning or tool invocation occurs. The pass is one **Jev** request (ADP-05): `Choice` intent, `Score` acuity, `Noul` missing details. Code picks the level from the answers **and** their confidence.
  * **Level 1/2 (High Acuity / Standard SOP):** Direct routing to System 1 FSM.
  * **Level 3 (Ambiguous Intent):** Execution is frozen, emitting a targeted clarification prompt to prevent hallucinated assumptions.
  * **Level 4/5 (Unstructured Diagnostic):** Routed to System 2 deliberative replanning.

### 2.3 Baddeley Multicomponent Working Memory Model
* **Foundational Literature:** Baddeley (2000); Liu et al. (2023) *"Lost in the Middle"*.
* **Engineering Formulation:**
  * Context windows are partitioned into strict token budget allocations:
    * **15%**: Immutable System Persona, Role Boundaries & Safety Invariants.
    * **15%**: Customer Identity & Chronic Account Fact Graph (Mem0 / Zep).
    * **35%**: Slotted RAG Document Passages (Hybrid BM25 + dense RRF, LLM-reranked, top-10; see checkpoint.md KR-D7/D9/D10).
    * **25%**: Chronological Dialogue Buffer (sliding window turns).
    * **10%**: Ephemeral Scratchpad Buffer for intermediate plan thoughts.
  * **LongLLMLingua** perplexity filtering drops 40–60% of low-information tokens.

### 2.4 High-Reliability Organizing (HRO) & Error Recovery
* **Foundational Literature:** Weick & Sutcliffe (2007) *Managing the Unexpected*; Shinn et al. (2023) *Reflexion*.
* **Engineering Formulation:**
  * **Reflexion Self-Critique:** $c_t = \text{LLM\_reflect}(\tau_t, S_t, \Omega)$ allows up to 2 verbal self-correction trials for transient tool/API errors.
  * **Hard Step Ceiling Guard:** Anti-loop tripwire halts execution if step count $N > 4$.
  * **Metacognitive Uncertainty Gating:** Semantic entropy calculation determines whether confidence $\ge 0.90$. If $< 0.90$ or if the circuit breaker trips, execution immediately escalates to a Human Specialist (HITL).

---

## 3. Detailed Component Architecture

### Stage 1: Ingress & Context Engineering
* **Inputs:** Raw user query string, tenant identifier, channel metadata (Slack/Teams/Web), OAuth2/JWT claims.
* **Processing:**
  1. Validates user authentication and RBAC permissions.
  2. Queries vector/relational stores to retrieve tenant profile and chronic issue facts.
  3. Formulates hybrid search query for RAG passages.
  4. Compiles Baddeley slot envelope and compresses low-information tokens via LongLLMLingua.
* **Output:** Normalized, typed Pydantic `ContextEnvelope` payload.

### Stage 2: Acuity & Intent Triage Gate
* **Inputs:** `ContextEnvelope` payload.
* **Processing:**
  1. One **Jev** request (ADP-05) with a narrow `state` (latest message, short dialogue summary, identity tier): `Choice` intent, `Score` urgency/acuity, `Noul` "required details missing?". Entity values outside a closed set are still extracted by the LLM or regex.
  2. Evaluates ESI decision logic in code, on the answer **and** its confidence:
     * High Acuity / Standard SOP, high confidence $\to$ Dispatch to System 1 FSM.
     * Complex Outage / Diagnostic $\to$ Dispatch to System 2 ReAct.
     * Missing parameter slots, or medium intent confidence $\to$ Dispatch Clarification Return.
     * Low confidence, or Jev error/timeout $\to$ Clarification or human queue (never fail open).
  3. Thresholds per route (ADP-05-Q2): > ~0.9 act · 0.5–0.9 clarify/flag · < 0.5 human; stricter for routes that change data; calibrated on golden sets.
  4. `state` is built from the PII-masked envelope only (ADP-05-Q3).
* **Guarantees:** Triage latency target $< 150\text{ms}$ (to be measured against the Jev API); deterministic boundary enforcement.

### Stage 3: Dual-Process Cognitive Engine
* **System 1 (LangGraph FSM):**
  * Loads thread checkpoint from PostgreSQL.
  * Verifies parameter invariants with strict regex/schema guards. Amounts, dates and SLA intervals are checked in code, never by Jev.
  * Conditional edges ask **Jev** `Noul` guards (e.g. "has the customer confirmed the fix?" before `RESOLVED`); on error the guard counts as "no".
  * Dispatches verified deterministic tools (<300ms, zero temperature).
  * On schema validation exception or unknown runbook branch: gracefully escalates to System 2.
* **System 2 (Deliberative ReAct & Replanner):**
  * Decomposes goal into a Plan-and-Solve Directed Acyclic Graph (DAG): $P'_{k+1:K} = \text{Replanner}(P_{k:K}, o_k)$.
  * Interleaves reasoning steps ($r_t$) and action payloads ($a_t$).
  * Tool selection: **Jev** `Choice` over the tool list and closed-set arguments; low confidence $\to$ HITL. Plan generation and free-text arguments stay with the generative LLM.
  * Dispatches diagnostic inspection tools and aggregates log/telemetry findings.
* **Circuit Breaker & Reflexion:**
  * If a tool invocation fails, generates verbal critique $c_t$ and prepends to context.
  * After each attempt, a **Jev** `Noul` "did this step succeed?" decides: continue, next trial, or trip to HITL. Low confidence counts as failure (ADP-05-Q1).
  * Maximum 2 Reflexion retry trials.
  * Hard StepCeilingGuard trips if total iterations exceed 4.
  * Emits `IncidentDiagnosticPacket` and trips immediately to Human Specialist (HITL).
* **Ambiguity Return:**
  * Generates clarifying question back to customer UI; suspends workflow thread awaiting user response.

### Stage 4: Execution Sandbox & Verification Gate
* **Sandboxed Tool Execution:**
  * Model Context Protocol (MCP) tool caller with rate-limiting and circuit breakers.
  * Two-Phase mutation checks (dry-run staging before writing side-effects).
  * Distributed Saga coordinator with compensating transactions for multi-system actions.
* **Confidence & Policy Gate:**
  * Computes semantic entropy and checks citation density against retrieved RAG chunks.
  * Enforces statutory compliance (GDPR/HIPAA/PCI-DSS) and financial risk limits (> $1,000 mandates human review).
  * **Score $\ge 0.90$:** Verified & Approved $\to$ Advance to Stage 5.
  * **Score $< 0.90$ OR Breaker Tripped:** Immediate escalation to Human Specialist.

### Stage 5: Durable Checkpointing & Delivery
* **Temporal.io & PostgreSQL Checkpointer:**
  * Atomic state snapshot committed to PostgreSQL.
  * Event-sourced replayability guarantees zero state loss across pod restarts.
  * Coordinates multi-day SLA timers and human approval signals.
* **Async Fact Extraction:**
  * Background worker scans completed turn, extracts verified customer facts, and updates Mem0 / Zep knowledge graph.
* **Response Delivery:**
  * Low-latency SSE/WebSocket token streaming to customer UI with citations and action buttons.
  * Emits distributed OpenTelemetry traces (W3C traceparent) and token usage logs.

---

## 4. Knowing Your Unknowns Failure Matrix (Reference)

| Quadrant | Failure Mode | Technical Risk | Architectural Mitigation |
|:---|:---|:---|:---|
| **Q1: Known Knowns** | Token Overflow | Context exceeds 16k window; KV-cache clips mid-sentence | Hard slot budget ceilings in Baddeley Context Assembler |
| **Q1: Known Knowns** | Illegal State Jump | Statechart attempts invalid transition (e.g. refund before auth) | LangGraph strictly typed Pydantic StateGraph edges; Jev only picks among allowed edges |
| **Q1: Known Knowns** | Tool Schema Rejection | LLM emits malformed JSON keys; downstream API returns 400 | Instructor grammar enforcement & regex parameter masks |
| **Q1: Known Knowns** | Recursion Ceiling | ReAct tool loop fails to converge; infinite loop | Anti-loop circuit breaker: StepCeilingGuard ($N \le 4$) |
| **Q2: Known Unknowns** | Intent Ambiguity Tie | 50/50 split between Tech & Billing; model guesses incorrectly | Jev triage confidence below threshold forces clarifying question |
| **Q2: Known Unknowns** | ReAct Reasoning Drift | Temperature variance generates erratic diagnostic paths | Plan-and-Solve upfront DAG constrains search space |
| **Q2: Known Unknowns** | Compaction Loss | Token pruner drops critical negation (e.g. *"Do NOT reboot"*) | Preserved regex slots for negative constraints |
| **Q2: Known Unknowns** | Latency Jitter | Reflexion replanning takes 9s vs normal 1.2s | Streaming token chunks + async Temporal heartbeats |
| **Q3: Unknown Knowns** | Sycophantic Compliance | User asks *"Should I run `rm -rf /`?"*; agent agrees | Hard-coded safety policies forbidding destructive shell ops |
| **Q3: Unknown Knowns** | Tone Dissonance | Cheerful greeting with emojis during catastrophic outage | Acuity sentiment classifier enforces sober clinical tone |
| **Q3: Unknown Knowns** | Over-Apologizing Loop | Agent repeats *"I apologize for the confusion"* across 4 turns | System prompt invariant forbidding repetitive apologies |
| **Q3: Unknown Knowns** | Premature Ticket Close | Marking ticket RESOLVED before client verifies cluster fix | Jev `Noul` guard on client confirmation before `RESOLVED` |
| **Q4: Unknown Unknowns**| Cyclic Tool Deadlock | Plan assumes tool succeeded; tool rolled back by saga | Saga compensations explicitly mutate shared thread state |
| **Q4: Unknown Unknowns**| Indirect Injection | Hidden prompt in customer logs hijacks RAG context | Sandboxed tool execution + isolated prompt envelopes |
| **Q4: Unknown Unknowns**| Sub-Plan Explosion | Replanner spawns 3 sub-plans per failed step; OOM crash | Global DAG depth ceiling $\le 2$ |
| **Q4: Unknown Unknowns**| Automation Seduction | Human specialist blindly approves bad plan due to past 99% AI accuracy | Forced active friction UI requiring parameter review |
| **Q2: Known Unknowns** | Jev Miscalibration | High confidence on a wrong intent for a new product or phrasing | Golden-set calibration per route (ADP-05-Q2); trace every Jev answer |
| **Q3: Unknown Knowns** | Jev Numeric Judgment | A guard quietly depends on an amount or date comparison Jev gets wrong | Numbers, dates and SLA checks stay in code (ADP-05) |
| **Q4: Unknown Unknowns**| Injected Decision | Customer text steers the triage `Choice` toward a privileged route | Jev never authorizes high-risk edges alone; hard-coded pre-conditions still gate them |

---

## 5. Architectural Decision Log

* **ADP-01: Planning Paradigm** $\to$ **Option C: Hybrid Dual-Process Statechart** (LangGraph deterministic FSM for SOPs + scoped ReAct for diagnostics).
* **ADP-02: Workflow Durability** $\to$ **Option C: Two-Tier Hybrid** (Temporal.io outer saga for multi-day SLA + LangGraph inner cognitive loop).
* **ADP-03: Context Compaction** $\to$ **Option C: Tripartite Structured Slot Allocator** (15% System, 15% Profile, 35% RAG, 25% Dialogue, 10% Scratchpad).
* **ADP-04: Error Recovery** $\to$ **Option C: Dual-Process Circuit Breaker** (Reflexion verbal critique max 2 trials, then immediate HITL tripwire).
* **ADP-05: Decision Model** $\to$ **Jev (TypeSafe) at decision points** (triage, FSM transition guards, System 2 tool selection); LangGraph stays the runner. Q1: LLM critique kept, Jev step check trips HITL · Q2: TypeSafe confidence bands, stricter for data-changing routes · Q3: only PII-masked state sent to Jev · Q4 (scope beyond Comp 5) open.
