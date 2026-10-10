# Enterprise AI Support Agent: Low-Level System Design (LLD)

> **Companion High-Level Architecture:** [`architecture.md`](../architecture/architecture.md)  
> **Visual Whiteboard Canvas:** [`architecture.tldr`](../../diagrams/architecture.tldr)  
> **Capabilities Taxonomy:** [`thoughts.md`](../decisions/thoughts.md)  
> **Evaluation & Failure Modes:** [`user_evaluation_framework.md`](../analysis/user_evaluation_framework.md) · [`failure_modes_matrix.md`](../analysis/failure_modes_matrix.md)

---

## 1. Low-Level Design Methodology & Decomposition Framework

To translate the abstract architecture into concrete, buildable engineering specifications, every component is decomposed across five standardized dimensions:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COMPONENT LOW-LEVEL DESIGN FRAMEWORK                     │
├───────────────────────────┬─────────────────────────────────────────────────┤
│ 1. Subcomponents          │ Modular internal engines that comprise the node│
├───────────────────────────┼─────────────────────────────────────────────────┤
│ 2. Sub-Capabilities      │ Functional mechanics & operations executed       │
├───────────────────────────┼─────────────────────────────────────────────────┤
│ 3. Design Patterns        │ State Machine, DAG, Event-Sourcing, Saga, Actor │
├───────────────────────────┼─────────────────────────────────────────────────┤
│ 4. Tool & Tech Ecosystem  │ Industry frameworks, open-source libraries, SaaS│
├───────────────────────────┼─────────────────────────────────────────────────┤
│ 5. Architectural Tradeoffs│ Latency vs. Accuracy, Durability vs. Velocity   │
└───────────────────────────┴─────────────────────────────────────────────────┘
```

---

## 2. Tier 2: Cognitive Reasoning & Execution Core

---

### Component [ 5 ]: AGENT ORCHESTRATION CORE (The Brain)

#### A. Architectural Responsibility
The central runtime engine responsible for parsing incoming queries, determining intents, formulating multi-step execution plans, managing contextual state transitions, compiling prompts, and controlling execution flow.

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       AGENT ORCHESTRATION CORE                              │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Intent Engine        │ 2. Planning Engine      │ 3. Workflow State Mach. │
│  • Multi-label classify │  • ReAct / Plan-Solve   │  • Graph state machine  │
│  • Entity extraction    │  • Subgoal breakdown    │  • Branching & joins    │
│  • Ambiguity detector   │  • Dynamic replanning   │  • Checkpointing & save │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Context Assembler    │ 5. Prompt Manager       │ 6. Agent Harness        │
│  • Token budget pack    │  • Dynamic few-shot     │  • Execution loop       │
│  • Working memory merge │  • Versioned templates  │  • Exception recovery   │
│  • RAG passage slotting │  • Guardrail injection  │  • Timeout control      │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Intent Understanding & Decomposition Engine:**
   * *Capabilities:* Multi-label classification (e.g., detecting both a technical issue and a billing issue simultaneously); semantic slot-filling for entities (`cluster_id`, `invoice_id`); confidence scoring on intent; ambiguity detection triggering clarifying questions instead of guessing.
2. **Planning & Decision-Making Engine:**
   * *Capabilities:* Goal decomposition into Directed Acyclic Graphs (DAGs); ReAct (Reason + Act) loop execution; Plan-and-Solve patterns; dynamic re-planning when an intermediate tool fails or returns unexpected data.
3. **Workflow State Machine & Execution Controller:**
   * *Capabilities:* Deterministic Finite State Machine (FSM) enforcing transition rules (e.g., `INGESTED` $\to$ `TRIAGED` $\to$ `EXECUTING` $\to$ `AWAITING_APPROVAL` $\to$ `RESOLVED`); state checkpointing; thread suspension for async human approval.
4. **Context Engineering & Assembly Engine:**
   * *Capabilities:* Dynamic context window allocation; token budgeting; interleaving system prompts, long-term user profile facts, short-term conversational turns, and retrieved RAG chunks into a coherent prompt payload; deduplication of repetitive context.
5. **Prompt Engineering & Versioning Engine:**
   * *Capabilities:* Template interpolation; version-controlled prompt registries; dynamic few-shot example selection based on semantic similarity to current query; system persona adherence enforcement.
6. **Agent Runtime Harness:**
   * *Capabilities:* Sandbox execution loop; step recursion limits (preventing infinite tool loops); latency tracking per hop; catastrophic exception catch-and-recover handlers.

#### C. Candidate Frameworks & Tooling Stack
* **Workflow & State Graph (selected, ADP-02):** **Temporal.io** outer saga (multi-day waits, timers, approvals, tool activities) + **LangGraph** inner cognitive loop (FSM, ReAct, specialist subgraphs; its Postgres checkpointer is the single source of agent state, MS-D8).
* **Decision Points (intent, urgency, transition guards, tool selection):** **Jev** (TypeSafe System One model; `typesafe-sdk`). Typed `Choice` / `Score` / `Noul` answers with confidence; no text generation or free-text parsing. *Selected: checkpoint.md ADP-05.*
* **Open-Value Extraction:** **Instructor** (Pydantic-enforced structured LLM extraction) / **Outlines** (Constrained regex/grammar-guided generation), for entity values Jev can't pick from a closed set.
* **Prompt Management:** **Langfuse Prompt Management** / **Agenta** / **Promptfoo** (Versioned prompt templates with Git-like tags and automated evaluation).
* **Context Assembly:** **LlamaIndex Context Optimizer** / **Semantic Kernel Context Pipeline**.

#### D. Architectural Tradeoffs & Decisions
* **LangGraph vs. Temporal:**
  * *LangGraph:* Faster developer velocity, native Python LLM primitive integration, lightweight state checkpointing in PostgreSQL. Best for fast (<60s) synchronous reasoning sessions.
  * *Temporal:* Industrial-grade replay durability, handles process crashes mid-workflow, guarantees execution across days/weeks for human-in-the-loop approvals.
  * *Hybrid Recommendation:* Use **LangGraph** for Tier-2 cognitive reasoning turns, wrapped inside a **Temporal Workflow** for durable long-running ticket lifecycles requiring human escalations.
* **Decision model: Jev vs. LLM classifier (Instructor):**
  * *Jev:* Calibrated probabilities and a confidence value per answer, many questions in one parallel call, no output parsing. Can't generate text, weak at numbers and dates, susceptible to injection.
  * *LLM classifier:* Flexible, but its scores are uncalibrated and it can return malformed output.
  * *Decision (ADP-05):* **Jev** at the triage gate, FSM transition guards and tool selection, with LangGraph edges still in code. Amounts, dates and SLA checks stay in code.

---

### Component [ 6 ]: MEMORY & STATE ENGINE

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §7 (MS-D1 – MS-D19) · diagram page `LLD - [3] Memory & State`.

#### A. Architectural Responsibility
Keeps what the agent needs across steps (working memory), across turns (conversation memory), and across conversations (per-user long-term facts), plus durable agent state for crash recovery and resume. Account facts (SLA tier, plan, region, invoices) are **not** memory: they are always read from the CRM tool (MS-D6).

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MEMORY & STATE ENGINE                               │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Conversation Memory  │ 2. Long-Term Memory     │ 3. Working Memory       │
│  • Session→conv→case    │  • Per-user facts only  │  • Plan, tool outputs   │
│  • Verbatim + summary   │  • valid_from / valid_to│  • Large outputs by ref │
│  • Pinned items         │  • Jev reconcile        │  • Inline summary       │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Agent State          │ 5. Memory Lifecycle     │                         │
│  • LangGraph checkpoints│  • Fixed TTL per type   │                         │
│  • Around side effects  │  • Erasure inventory    │                         │
│  • Versioned + migrated │  • Back-office requests │                         │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Conversation Memory:**
   * *Capabilities:* Three levels: session → conversation → case (MS-D1); a new conversation is linked to an open case by a Jev `Choice`, confirmed by the user below high confidence (MS-Q1). Last N turns verbatim + rolling LLM summary + pinned items (constraints, IDs, commitments) that are never evicted; pins set by rules + a Jev `Noul`, with a Jev unpin check (MS-D2, MS-Q2).
2. **Long-Term Memory:**
   * *Capabilities:* Per-principal facts only (MS-D3), stored as flat records with `valid_from` / `valid_to` so updates close old facts (MS-D4). Extracted at case resolution (or conversation close for cases without one, or > 14 days open) from user turns and allow-listed structured tool fields only; plans are skipped (MS-D5, D16, D17). Only facts about the user themself (Jev `Noul`, MS-D15). A Jev `Choice` decides ADD / UPDATE / DELETE / NOOP; low confidence → NOOP (MS-D12). Stored unmasked and encrypted; both sides masked the same way before the Jev call (MS-D19).
3. **Working Memory:**
   * *Capabilities:* Plan, tool outputs and passage references for one run; large tool outputs stored by reference with an inline summary, paged in on demand (MS-D7).
4. **Agent State:**
   * *Capabilities:* LangGraph checkpointer (Postgres) is the single source of state; Temporal holds only status + checkpoint ID (MS-D8). Checkpoint after every node and before/after every side-effecting tool call (MS-D9). Versioned checkpoints with migrations; unknown version → HITL (MS-D13). After waits > 1 h, re-fetch and re-check before side effects (MS-D18).
5. **Memory Lifecycle:**
   * *Capabilities:* Fixed TTL per type: facts 12 months since last confirmed, episodes 24 months, blobs and checkpoints case closed + 30 days (MS-D10, MS-Q3). Back-office erasure/correction in v1 (MS-D11). Erasure inventory covering every store; facts keyed by `(principal, tenant)` (MS-D14).

#### C. Frameworks & Tooling Stack
* **Selected:** **PostgreSQL + pgvector** (facts, conversation log, LangGraph checkpointer) · **LangGraph checkpointer** (agent state) · **Jev** (reconcile, case linking, pinning, subject check) · an LLM for summaries and fact extraction.
* **Not used:** Mem0 / Zep as engines (their ideas are adopted: Mem0-style reconcile, Zep-style validity dates) · Redis Stack for session memory (state lives in Postgres).

#### D. Architectural Tradeoffs & Decisions
* **Raw Chat History vs. Extracted Facts:** raw history blows up token budgets ("Lost in the Middle"); extracted facts need an async LLM call and can be poisoned. Decision: both, bounded: verbatim window + summary + pins for the conversation (MS-D2), and a small, per-user, validated fact store (MS-D3 – D5, D12, D15 – D17).
* **Graph memory vs. flat facts:** a temporal knowledge graph handles updates well but is heavy; decision: flat facts with validity dates (MS-D4).

---

### Component [ 7 ]: KNOWLEDGE & RAG RETRIEVAL ENGINE

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §6 (KR-D1 – KR-D16) · diagram page `LLD - [2] Knowledge & Retrieval`.

#### A. Architectural Responsibility
Provides grounded enterprise documentation, product runbooks, known bugs, postmortems and resolved tickets, and returns the top-10 relevant parent sections with citation metadata to Orchestration. Packing passages into the prompt (and any compression) belongs to Orchestration (ADP-03).

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      KNOWLEDGE & RAG RETRIEVAL                              │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Knowledge Sources    │ 2. Ingestion            │ 3. Indexing             │
│  • Curated + operational│  • Nightly batch        │  • Structure-aware      │
│  • Raw tickets / chats  │  • Instant purge path   │  • Parent-child chunks  │
│  • audience + provenance│  • PII-mask tickets     │  • Public + per-tenant  │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Retrieval            │ 5. Ranking              │ 6. Retrieval Evaluation │
│  • Standalone rewrite   │  • RRF fusion (k = 60)  │  • Offline golden set   │
│  • BM25 + dense         │  • Screen, then LLM     │  • Online signals       │
│  • ACL copy + live check│    rerank → top-10      │  • Sampled LLM judge    │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Knowledge Sources:**
   * *Capabilities:* Curated KB, operational content (known bugs, postmortems, runbooks) and raw resolved tickets/chats (KR-D1). Per-chunk `audience` label, internal by default; only customer-visible text is cited to customers (KR-D2, D13, Q4). Provenance `source = human | agent`; agent text labelled "agent reply, unverified" (KR-D16).
2. **Ingestion:**
   * *Capabilities:* Nightly batch re-index (KR-D3), deletion-aware (check → filter → ingest → verify, KR-D15). Deletions, GDPR erasures and ACL revocations purged immediately out-of-band, with tombstones to the semantic cache (KR-Q3). Tickets PII-masked before chunking/embedding (KR-Q2).
3. **Indexing:**
   * *Capabilities:* Structure-aware chunking (headings, tables, code intact) with parent-child small-to-big retrieval (KR-D4). Shared public index + per-tenant private index; raw tickets go to the tenant's private index only; T0 anonymous users query public only (KR-D5, Q1).
4. **Retrieval:**
   * *Capabilities:* Standalone query rewrite + exact-identifier extraction; no multi-query / HyDE (KR-D8). Hybrid BM25 + dense (KR-D7). ACLs copied at ingestion + live on-behalf-of check for restricted docs (KR-D6).
5. **Ranking:**
   * *Capabilities:* Reciprocal Rank Fusion (k = 60) → screen retrieved text as untrusted (Comp 7 engine, KR-D14) → LLM reranker (KR-D9) with a validation wrapper that falls back to RRF order on malformed output (KR-D12) → always top-10 parent sections, internal chunks dropped (KR-D10, Q5).
6. **Retrieval Evaluation:**
   * *Capabilities:* Offline golden set (harness in Comp 9) + online implicit signals + sampled LLM-judge context precision (KR-D11). No "no evidence" rate, since retrieval never abstains (KR-D10).

#### C. Frameworks & Tooling Stack
* **Search engine (decided, DP-Q1):** **Qdrant**, hybrid dense + sparse, with a shared public collection and per-tenant private collections.
* **Reranker (decided, KR-D9):** an **LLM reranker** behind a validation wrapper. Cross-encoders (Cohere Rerank v3, BGE) were considered and not chosen. Jev as reranker is an open candidate (ADP-05-Q4).
* **PII masking at ingestion:** Presidio-class engine owned by Safety (Comp 7).
* **Embedding models:** `text-embedding-3-large` / `bge-large-en-v1.5` (not yet decided; model version stamped on the index).

#### D. Architectural Tradeoffs & Decisions
* **Dense-only vs. hybrid:** dense search fails on exact error codes (`ERR_504_TIMEOUT`); decision: hybrid BM25 + dense with RRF (KR-D7).
* **Freshness vs. simplicity:** CDC was considered; decision: nightly batch for content, instant purge for deletions (KR-D3, Q3). Content can be up to ~24 h stale.
* **LLM reranker vs. cross-encoder:** the LLM reranker reads untrusted text and can be steered, so retrieved text is screened first (KR-D14) and output is validated (KR-D12).

---

### Component [ Cap 12 ]: COST & RESOURCE ROUTER

> **Decided design (Cost & Resource Management):** [`checkpoint.md`](../decisions/checkpoint.md) §15 (CR-D1 – CR-D14) · diagram page `LLD - [11] Cost & Resource Management`.

#### A. Architectural Responsibility
Picks the model tier for each turn, keeps token use within budgets, tracks exact cost per tenant, and caches answers that are safe to reuse.

#### B. Subcomponents & Sub-Capabilities
1. **Model Selection:** three tiers (self-hosted open-weights, mid-tier API, frontier API); a **Jev `Score` of difficulty** picks the tier per turn; a step that fails checks or has low confidence is rerun one tier up (CR-D1, D2, CR-Q1). The self-hosted model also serves easy low-risk turns (CR-D9).
2. **Token Management:** ADP-03 slots + provider prompt caching for the stable prefix + compression of retrieved passages (never dialogue, pins or delimiters) (CR-D6). Token budget per turn by route and per conversation per day; when reached, wrap up or hand off (CR-D5).
3. **Answer Cache:** per tenant, 7 days, only answers built from customer-visible KB content with no account data; eligibility by a Jev `Noul`; key = question + tenant + product version + KB index version; version-specific questions not cached; invalidated by KB updates and tombstones (CR-D3, D14).
4. **Usage Budgets:** monthly budget per tenant, alert at 80 %, soft cap at 100 % → knowledge-base-only answers (CR-D4). Release gate: cost per resolved conversation ≤ +10 % per release and ≤ 1.5 × the first month's cost per route (CR-D12).
5. **Cost Tracking:** exact usage counter (RP-D14) × price table, reconciled monthly with invoices and GPU bills; per-tenant reports for tenant admins (CR-D7, D11).
6. **Cost Optimization:** cheaper tiers for reranker / screening / judge where EV shows no loss (CR-D8); envelope encryption for per-user keys (CR-D10).

#### C. Frameworks & Tooling Stack
* **Selected:** Jev (tier routing, cache eligibility) · provider prompt caching · LongLLMLingua-class passage compression · vLLM-class serving for the self-hosted tier.
* **Not used:** cross-tenant semantic cache; GPTCache-style similarity-only keys.

---

### Component [ 8 ]: MODEL RUNTIME & LLM ENGINE

#### A. Architectural Responsibility
Manages physical connections, inference sessions, structured output decoding, and streaming responses from LLM providers or local weights.

#### B. Subcomponents & Sub-Capabilities
1. **Model Provider Abstraction Layer:**
   * *Capabilities:* Uniform interface supporting Anthropic, OpenAI, Google Vertex AI, and local self-hosted vLLM endpoints.
2. **Structured Decoding & Grammar Engine:**
   * *Capabilities:* Guarantees valid JSON output matching target schemas via JSON Schema / Context-Free Grammars; eliminates syntax errors in tool invocations.
3. **Resilience & Fallback Engine:**
   * *Capabilities:* Automatic retry with exponential backoff on HTTP 429/503; automatic fallback to backup model provider if primary provider suffers an outage.

#### C. Candidate Frameworks & Tooling Stack
* **Inference Gateway:** **LiteLLM** / **LangChain ChatModel abstraction**.
* **Local Self-Hosted Runtime (if required):** **vLLM** / **Ollama**.

---

### Component [ 9 ]: TOOLS & ENTERPRISE APIS

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §8 (TA-D1 – TA-D18) · diagram page `LLD - [4] Tools & Actions`.

#### A. Architectural Responsibility
Gives the agent authenticated, validated, auditable ways to read and change enterprise systems (CRM, ERP/billing, ticketing, cloud monitoring). Choosing the tool is Orchestration's (ADP-05); approval queues and UI are HITL's; policy content and the screening engine are Safety's.

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        TOOLS & ENTERPRISE APIS                              │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Tool Registry        │ 2. Tool Schemas         │ 3. Tool Invocation      │
│  • Python functions     │  • Pydantic in / out    │  • Temporal activities  │
│  • Jev shortlist / turn │  • Argument provenance  │  • Pool per system      │
│  • Reviewed risk class  │  • Allow-listed sources │  • Sagas + safe undo    │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. External Systems     │ 5. Action Validation    │ 6. Error Handling+Audit │
│  • Adapters, auth, limit│  • Tiered approval      │  • Safe retries, breaker│
│  • Outbound allow-list  │  • Jev gate (tighten)   │  • Normalized errors    │
│  • Dry run if supported │  • Dry-run preview      │  • Audit + crypto-shred │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Tool Registry:**
   * *Capabilities:* Plain Python functions with typed signatures; no MCP in v1 (TA-D2). Per turn, code filters to tools the user's tier and role may use, then Jev shortlists and picks (TA-D1, ADP-05); low shortlist confidence → human (TA-Q3). Risk class proposed by the author and reviewed by a second person; external-facing or irreversible tools need approval until reviewed (TA-D16).
2. **Tool Schemas:**
   * *Capabilities:* Pydantic input/output models with closed sets for Jev. Identifying and sensitive arguments (IDs, amounts, environment, recipients, URLs) must trace to the user's words or an allow-listed structured field of an earlier tool result (TA-D3, D13).
3. **Tool Invocation:**
   * *Capabilities:* Runs as Temporal activities on one task queue per external system, each with its own worker pool and outbound allow-list (TA-D11, Q2). On-behalf-of token where supported, else a scoped service account + agent-side user check (TA-D4). Idempotency key = `checkpoint_id + tool_call_id` (MS-D9). Multi-system writes run as Temporal sagas; compensations are idempotent tools and a failed compensation goes to a human; steps with no real undo run last and need approval (TA-D8, D17, D18). Output text screened by Safety before the LLM reads it, size-capped, stored by reference (TA-D9).
4. **External Systems:**
   * *Capabilities:* Adapters handling auth, rate limits, API versions, error formats and dry-run support where it exists.
5. **Action Validation:**
   * *Capabilities:* Tiered approval: reads automatic; low-risk writes below the threshold automatic; financial writes at/above the tenant threshold (default $1,000, counted per call), destructive or irreversible actions → human approval (TA-D5, D14, Q1). A Jev gate (allow / ask / deny + "serves the request?") can only tighten the static tier; "ask" goes to the user for low-risk calls and to a specialist above that (TA-D6, Q4). Dry-run preview on the approval card where the API supports it (TA-D7).
6. **Error Handling & Audit:**
   * *Capabilities:* Retry reads and idempotent writes only, with backoff + jitter and a circuit breaker per system; auth and permanent errors are normalized for the agent (TA-D10). Append-only audit record per call + before/after state for financial writes (TA-D12); personal fields encrypted per user and erased by deleting the key (TA-D15).

#### C. Frameworks & Tooling Stack
* **Selected:** **Pydantic v2** (schemas) · **Temporal** activities, task queues, sagas (execution) · **Jev** (shortlist, pick, gate).
* **Not used in v1:** **Model Context Protocol (MCP)** and **LangChain Toolkits** (TA-D2) · microVM sandboxes (no code-execution tools; TA-D11).
* **Resilience:** Temporal activity retry policies (Tenacity / PyBreaker only if needed outside Temporal).

---

### Component [ 10 ]: MULTI-AGENT SPECIALIST SWARM

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §9 (MA-D1 – MA-D17) · diagram page `LLD - [5] Multi-Agent & Communication`.

#### A. Architectural Responsibility
A coordinator delegates to specialist sub-agents with their own instructions, tools and identity, merges their typed results, and writes the only reply to the user. Specialists never talk to each other or to the user (MA-D1, D12).

#### B. Subcomponents & Sub-Capabilities
1. **Specialized Agents:**
   * *v1 set (MA-D2):* **Generalist**, **Billing** (invoices, disputes, credits; Stripe / NetSuite / SAP reads), **Technical** (logs, monitoring, runbooks; CloudWatch / Datadog / GitHub reads), **Account & Ops** (SSO, permissions, provisioning; Okta / Auth0 / CRM reads). The coordinator is separate and only routes and merges (MA-Q1).
   * *Identity and permissions (MA-D9):* each specialist has its own agent identity (`act` claim, audit) and a tool allow-list intersected with the user's scope. **Reads and low-risk writes directly** (tickets, notes, reset links: tools reviewed as low-risk, TA-D16); anything above that (financial at/above the threshold, destructive, irreversible) is proposed to the coordinator, which sends it through Tools for approval. Every call still goes through Tools (Jev gate, idempotency, audit). *(Revised 2026-09-29 from read-only.)*
2. **Delegation:**
   * *Capabilities:* Jev `Choice` for the lead specialist (including "generalist handles it") + one `Noul` per specialist for multi-domain turns; code dispatches; low confidence → HITL (MA-D3).
3. **Agent Communication:**
   * *Capabilities:* Typed task and result contracts (Pydantic), always through the coordinator; results carry `status` (done / blocked / needs-user), findings, evidence references and proposed actions (MA-D4). Task brief + on-demand read access to the conversation (MA-D5). Proposed actions are reported as proposed; low-risk writes a specialist made are reported only after Tools reads them back (MA-D14).
4. **Coordination:**
   * *Capabilities:* Parallel for independent subtasks, sequential otherwise (MA-D6); parallel branches are read-only, and a specialist writes only when it runs alone in the case (MA-D18). Conflicts: Jev `Score` per claim vs. evidence, tie → HITL (MA-D7); any numeric disagreement → HITL (MA-D16). Limits: depth 1, ≤ 5 specialists, 2 steps per specialist, 6 per turn, shared token budget (MA-D8). One merged reply after the join and conflict check, in one voice, with a Jev check for "go contact another team" (MA-D13, D15, D17). Success is announced only after Tools verifies it (MA-D14).
5. **Agent Discovery:**
   * *Capabilities:* Static registry in code/config (MA-D10). Specialists run as LangGraph subgraphs in the same graph and checkpoint (MA-D11, from ADP-02 / MS-D8).

#### C. Frameworks & Tooling Stack
* **Selected:** **LangGraph** subgraphs (coordinator + specialists) · **Pydantic** contracts · **Jev** (delegation, claim scoring, reply check).
* **Not used:** CrewAI, AutoGen, A2A messaging between services (single graph, static registry).

---

## 3. Tier 1: Ingress & Edge Security Tier

---

> **Decided design for [ 1 ] and [ 2 ]:** [`checkpoint.md`](../decisions/checkpoint.md) §5 (UA-D1 – UA-D9) · diagram page `LLD - [1] User & Application`.

### Component [ 1 ]: USER CHANNELS & INGRESS
* **Subcomponents:**
  * Web chat widget only in v1 (UA-D1). Slack, Teams, mobile and email are later adapters on the same channel-agnostic envelope.
  * API transport: decoupled `POST` (returns 202, idempotent submit) + resumable SSE stream, one path for live, reconnect and deferred (HITL) answers (UA-D2). Deferred answers land in the conversation inbox; no email notification in v1 (UA-Q2).
  * Response contract: typed event envelope (`status`, `text_delta`, `citation`, `action_card`, `handoff`, `final`, `error`) (UA-D7). Streaming vs. the output safety gate is still open (UA-D8).
* **Tooling:** **FastAPI** (POST + SSE). Slack Bolt / Bot Framework SDKs only when those channels are added.

### Component [ 2 ]: API GATEWAY & IDENTITY
* **Subcomponents:**
  * Tiered identity: T0 anonymous → T1 email OTP → T2 SSO with step-up for account actions (UA-D3).
  * Downstream identity: RFC 8693 token exchange → short-lived on-behalf-of tokens with `sub` = user, `act` = agent; T0 gets a minimal read-only token (UA-D4, UA-Q1). User access token valid 24 h (UA-D9).
  * Sessions: 30 min idle / 12 h absolute (UA-D6); session → conversation → case (MS-D1).
  * Tenant context comes from the verified token only, never from request fields. SLA tier and entitlements are read from the CRM tool when needed (MS-D6).
* **Tooling:** **Kong Gateway** / **Traefik** / **FastAPI Security** / **Auth0 / Okta** (IdP choice not yet decided).

### Component [ 3 ]: RELIABILITY & RESILIENCE

> **Decided design (Reliability / Performance / Scale):** [`checkpoint.md`](../decisions/checkpoint.md) §14 (RP-D1 – RP-D15) · diagram page `LLD - [10] Reliability / Performance / Scale`.

* **Rate limiting:** per tenant, user and conversation at the gateway, plus tokens per minute per tenant from an exact usage counter (RP-D1, D14). Over the limit → a knowledge-base-only answer (generated, or cited snippets with no LLM); a hard ceiling returns `429` + `Retry-After` (RP-D2).
* **Duplicates:** one active turn per conversation (including turns waiting on approval); new messages are rejected with "still working on your last message" (RP-D3).
* **Fallbacks:** LLM provider down → self-hosted open-weights model in the region → knowledge-base-only (RP-D4). Jev down → fallback LLM classifier for triage / delegation; guards and tool gating stay fail-safe (RP-D5). Tool breaker open → request held up to 24 h, answer delivered to the inbox, then a human (RP-D7). No cached account facts (MS-D6).
* **Bursts:** request coalescing for identical reads; incident mode where a Jev `Noul` routes outage questions to a prepared status answer (RP-D6).
* **Retries:** one retry layer per dependency, a per-turn retry budget and a global retry budget per dependency (RP-D13). Tool retries / breakers: TA-D10.
* **Latency:** first `status` ≤ 1 s; final-reply p95 FAQ 5 s · diagnostics 20 s · multi-specialist 45 s (RP-D8).
* **Scale + DR:** autoscaling with a pre-warmed minimum (RP-D9); multi-AZ inside each region, no cross-region failover (RP-D10); point-in-time recovery + monthly drills into a same-region warm standby (RP-D11, D15); load + chaos tests every release (RP-D12).
* **Tooling:** **Envoy / Kong** rate limiting · Temporal retry policies · a per-region counter store (e.g. Redis) · vLLM-class serving for the fallback model.

> **Decided design for [ 4 ], [ 12 ] and authorization / governance:** [`checkpoint.md`](../decisions/checkpoint.md) §10 (SG-D1 – SG-D18) · diagram page `LLD - [6] Safety, Security & Governance`.

### Component [ 4 ]: INPUT SAFETY GUARDRAILS
* **Subcomponents:**
  * Normalization: Unicode NFKC + confusables detection (homoglyphs, zero-width characters); payload size guard.
  * PII tokenization (SG-D4, D5): custom recognizers + an allow-list of technical identifiers never masked (hostnames, service names, invoice / cluster / ticket IDs); **reversible, global, format-preserving deterministic tokens** stored in a vault. Models (LLM and Jev) see tokens only. Real values are restored only into tool fields declared `needs_pii` (SG-D16). Vault down → tokens stay in the reply and PII-needing tools are blocked (SG-D18).
  * Screening of everything entering a prompt: user messages, retrieved passages (KR-D14), tool outputs (TA-D9), conversation reads (SG-D1). **Llama Guard 3** for hazards and abuse + a **Prompt Guard-class injection classifier**, self-hosted; not Jev (SG-D2). Three bands per source type: pass / review (read-only turn or human) / block (SG-D3).
  * Spotlighting: untrusted text is delimited and marked as data in every prompt (SG-D1). No quarantined LLM.
  * Hostility: persona guidance only (SG-D7); "talk to a human" always available (SG-D14).
* **Tooling:** **Llama Guard 3**, Prompt Guard-class classifier, **Microsoft Presidio** (custom recognizers) + token vault. *Not used:* NeMo Guardrails, Guardrails AI, Jev for screening.

### Authorization, Privacy & Governance (no separate node; SG-D8 – D14, D17)
* **Policy engine:** **AWS Cedar**, called by Tools, Knowledge and Memory; versioned, tested policies (SG-D8). Holds agent-side checks for service-account tools (TA-D4), T0 audience rules, ticket visibility (author + tenant support / admin roles, SG-D9) and tenant blackout windows (destructive / production actions need approval, SG-D10). Engine down → writes fail closed, reads use a short cache (SG-D17).
* **Providers:** standard API terms; providers see tokens, not PII (SG-D11).
* **Retention:** transcripts in an access-restricted legal archive for **2 years**, removed from agent stores on erasure (SG-D12).
* **Governance:** every automated decision recorded with policy / prompt / model versions; a "why" view (users: plain-language reasons and checks run; specialists: full record) (SG-D13). AI disclosure, "talk to a human" always available, human review for account closure / suspension, refund or dispute rejection, contract or plan changes (SG-D14).

---

## 4. Tier 3: Governance, HITL & Response Delivery Tier

---

### Component [ 11 ]: CONFIDENCE GATE & EVALUATION BOUNDARY

> **Decided design (Human-in-the-Loop, Confidence Boundaries):** [`checkpoint.md`](../decisions/checkpoint.md) §16 (HL-D1 – HL-D14) · diagram page `LLD - [12] Human-in-the-Loop`.

* **Gate:** a Jev request on every draft: `Noul` "is every claim supported by the cited passages or tool results?" + `Score` relevance; agent-written evidence counts less (HL-D1).
* **Bands (starting values, recalibrated per route by EV-D9):** send ≥ 0.90 · co-pilot 0.50–0.90 (a human edits the draft; only on routes with a staffed queue) · hand off < 0.50 (HL-D2, HL-Q1).
* **Numbers:** any draft stating money or SLA figures goes to the co-pilot band (HL-D13).
* **Approvals** for actions come from Tools (TA-D5); the gate reads that flag.
* **Not used:** semantic entropy (2–4 s of extra sampling), logprob scoring.

### Component [ HITL ]: HUMAN SUPPORT SPECIALIST CONSOLE

> **Decided design (Human-in-the-Loop):** [`checkpoint.md`](../decisions/checkpoint.md) §16.

* **Escalation:** one typed packet for every source (reason code, case, summary, evidence, proposed action, diagnostic, deadline) (HL-D12) → skills-based queues (billing, technical, account; tier) with priority from Jev triage acuity and tenant tier; SLA timers: handoffs 15 min, approvals 1 h, reviews 1 business day, breaches move to a senior queue (HL-D3). Aging alerts; undecided approvals cancelled after 3 days and the user told (HL-D9).
* **Approval:** rights by role and amount (senior at/above a tenant setting, default $5,000); the approver may not be the handler; enforced in Cedar (HL-D4). The card shows evidence + dry-run preview, and the approver confirms amount, account and target one by one (HL-D5).
* **Handoff:** "talk to a human" (button or a Jev `Noul`) hands off immediately; the agent answers low-risk questions until a human picks up, then stops (cold handoff) (HL-D6, D7). Users see status, a wait estimate and can cancel a pending action (HL-D8).
* **Human replies** run the same output checks as agent replies, as overridable warnings (HL-D14).
* **Feedback:** approve / reject + reason codes (HL-D10).
* **Tooling:** **self-hosted Retool in each region** (HL-D11).

### Component [ 12 ]: OUTPUT SAFETY GUARDRAILS
* **Subcomponents:**
  * Leakage filter: PII, secrets (API keys, internal IPs / hostnames) and system-prompt leakage (SG-D6).
  * URL and markdown sanitization: no auto-loading remote images or unlisted URLs (SG-D6, UA UU4).
  * Rule-based promise check: commitment phrases ("we will refund", "guarantee", prices, legal terms) hold the reply for one rewrite unless a verified action backs them (SG-D15, *Moffatt*). No tone check (SG-D6, accepted risk).
  * Redirect check ("go contact another team"): Jev `Noul`, owned by Multi-Agent (MA-D13).
  * *Factual grounding is not here:* it belongs to Confidence Boundaries (Comp 13), measured by Evaluation (Comp 9).
* **Tooling:** rules + the Comp [4] classifiers. *Not used:* NeMo Guardrails, Guardrails AI. (Ragas faithfulness moves to evaluation.)

### Component [ 13 ]: RESPONSE DELIVERY ENGINE
* **Subcomponents:**
  * Real-Time Streaming Manager (SSE / WebSocket stream chunks to client with low latency).
  * State Commit Coordinator (Two-phase commit persisting conversation state and audit records).
* **Tooling:** **FastAPI StreamingResponse** / **Redis Pub/Sub** / **Kafka**.

### Component [ 14 ]: USER CLIENT (SERVED)
* **Subcomponents:**
  * Markdown / Rich Card Renderer (Renders formatted code, tables, and action buttons in UI).
  * Micro-Feedback Loop (Captures user Thumbs Up / Down ratings and feedback text).
* **Tooling:** Modern Web Chat SDK / React Chat Components.

---

## 5. Tier 4: Enterprise Foundation Platform

---

### Foundation: DATA & PERSISTENCE (Capability 8)

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §11 (DP-D1 – DP-D15) · diagram page `LLD - [7] Data & Persistence`.

* **Regions:** regional deployments, **US + EU** at launch; each tenant pinned to one region; every store below runs per region (DP-D10).
* **PostgreSQL (per region), pool model with forced row-level security** (DP-D2): turns + idempotency, sessions / conversations / cases, LangGraph checkpoints, conversation memory, long-term facts (pgvector), tool audit + decision records (append-only, DP-D3), transcript legal archive (restricted role, 2 years, DP-D8), outbound event log (7-day replay window, DP-D7), tenant settings (DP-D12).
* **Token vault:** a separate, isolated Postgres database per region; one token key shared across regions so tokens stay global (DP-D5, DP-Q2).
* **Knowledge search:** **Qdrant** (hybrid dense + sparse; public + per-tenant collections) (DP-D1, DP-Q1).
* **Object storage:** working-memory blobs (MS-D7) and versioned raw knowledge snapshots per nightly batch (DP-D11).
* **Encryption:** envelope encryption: a data key per user, wrapped by a per-tenant KMS key, stored in a separate key store with 1-day backup retention; erasure deletes the user's data key (DP-D4 revised by CR-D10, CR-D13, TA-D15).
* **Change events:** no event bus; components write directly to consumers (DP-D6). **Erasure / deletion fan-out runs as a Temporal workflow** with retries and a completion check, including removal from raw snapshots (DP-D13, D15).
* **Backups:** kept 35 days; erased data may remain in backups until expiry; a restore can bring it back (accepted, documented) (DP-D9, D14).
* **Not used:** Redis for working memory or sessions (MS-D8); Kafka / outbox (DP-D6).

### Foundation: OBSERVABILITY & TRACING (Capability 10)

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §13 (OB-D1 – OB-D14) · diagram page `LLD - [9] Observability & Monitoring`.

* **Instrumentation:** OpenTelemetry in every service; an LLM-observability SDK (Langfuse / LangSmith) used only to create agent-step spans, exported as OpenTelemetry (OB-D1, OB-Q1). Every Jev request, answer and confidence is on the trace (ADP-05).
* **Pipeline:** the OpenTelemetry collector runs the PII recognizers on every span and log line (OB-D14), then tail-samples: all errors, escalations, risk-tier and slow traces kept, the rest sampled (OB-D4). Kept traces carry full tokenized content; others carry metadata only (OB-D3).
* **Stores (per region, US / EU):** **Jaeger + OpenSearch** for traces (OB-D2, OB-Q3); structured JSON logs in a separate log store with the trace ID in every line (OB-D12). Retention **7 days**; erasure deletes by user / conversation ID inside the DP-D13 workflow (OB-D5, D6).
* **Metrics + alerts:** dashboards, no tenant-facing targets; internal SLOs (availability, time to first `status`, time to final reply, escalation rate) drive burn-rate alerts, plus specific alerts: OAuth token expiry, nightly batch failure, breaker open, reranker fallback rate, repeated sub-threshold writes (OB-D7, D8, OB-Q2).
* **Tokens + cost:** per tenant, conversation and component, **estimated** from kept traces scaled by the sampling rate (OB-D9, D13).
* **Execution:** Temporal's UI + traces (OB-D10).
* **Failures:** rules, then a Jev `Choice` over a failure taxonomy (OB-D11).
* **Not used:** Prometheus labels per conversation (cardinality); Grafana Tempo (no per-trace deletion); vendor APM auto-instrumentation.

### Foundation: EVALUATION & LLMOPS (Capabilities 9, 14, 15, 16)

> **Decided design (Evaluation & Experimentation):** [`checkpoint.md`](../decisions/checkpoint.md) §12 (EV-D1 – EV-D15) · diagram page `LLD - [8] Evaluation & Experimentation`. Testing, Deployment and Continuous Improvement have their own sections below.

* **Datasets:** synthetic Interaction Episodes from [`user_evaluation_framework.md`](../analysis/user_evaluation_framework.md) + hand-written seeds + curated production conversations, PII-tokenized, regional, erasable, only from tenants who opt in (EV-D1, D10, D15).
* **Suites:** per-component suites (triage, retrieval, memory, tools, delegation, screening, reply checks) + an end-to-end suite for the **risk tier only** (EV-D3, EV-Q1).
* **Scoring:** deterministic assertions + an **LLM judge from a different model family**, calibrated against human labels (EV-D2, EV-Q3). Jev is not a scorer; Jev's own decision points are measured and calibrated here (EV-D9).
* **Environment:** staging copies of external systems; where none exists: stateful fakes for write / multi-step integrations, recorded responses for read-only ones, skip low-priority ones (EV-D4, EV-Q2). Staging uses test accounts, an outbound allow-list and no notifications (EV-D14).
* **Cadence + gate:** smoke per commit, full suites nightly and before release (EV-D6). Release gate: component thresholds, zero safety violations and pass^k on the risk tier, cost / latency budgets, live CSAT / FCR / CES for the next version (EV-D5).
* **Live:** implicit signals + sampled judge scoring (EV-D8); shadow mode (reads live, writes never executed) and canary / A/B on read-only routes; live evidence counts only for the routes it covered (EV-D7, D12, D13).
* **Thresholds:** calibrated per route on labelled sets each release (EV-D9).
* **Streaming:** replies are buffered until checks pass, with `status` events meanwhile (EV-D11, resolves UA-D8).
* **Tooling:** **Ragas** / **DeepEval** / **Promptfoo** / **Langfuse datasets** (tool choice not yet decided).

---

### Foundation: TESTING & QUALITY (Capability 14)

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §17 (TQ-D1 – TQ-D14) · diagram page `LLD - [13] Testing & Quality`. Quality *scoring* is Evaluation's (§12); this is pass / fail correctness.

* **Unit:** model and Jev calls always mocked (TQ-D1); Cedar policy unit tests (TQ-D4).
* **Agent behaviour:** deterministic tests of FSM transitions, guards, step caps, approval tiers, one active turn, budgets and fallbacks with scripted (incl. malformed / hostile) model outputs, plus property-based tests of the safety invariants (TQ-D2).
* **Integration:** contract tests for shared formats (escalation packet, typed results, events, tool schemas, Jev question sets) (TQ-D5); one-trace-end-to-end tests across Temporal, Jev and SSE (TQ-D11).
* **CI checks:** every tenant table has forced RLS; every tool declares risk class, idempotency, `needs_pii`, integration type; every policy has tests (TQ-D4).
* **E2E:** one test per framework scenario (8) on staging, with synthetic persona data + tokenized opted-in production samples (TQ-D6, D12).
* **Adversarial:** a fixed injection / jailbreak set in CI (TQ-D3).
* **Regression:** merge gate = unit + integration + contracts + policy checks + EV smoke subset (TQ-D9); retries allowed except for invariant / property / policy / security tests (TQ-D8, D13); SQL migrations by review, checkpoint migrations tested against stored old versions (TQ-D7, D14); load + chaos on staging before each release (TQ-D10).
* **Tooling:** pytest + Hypothesis (property tests) · Cedar test tooling · Pact-style contract tests.

### Foundation: DEPLOYMENT & LLMOPS (Capability 15)

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §18 (DL-D1 – DL-D12) · diagram page `LLD - [14] Deployment & LLMOps`.
> **v1 runtime (learning project):** the design below is the production target. v1 runs on the *learning deployment profile* — one Oracle Cloud Always Free ARM VM with Docker Compose + free managed services, ≤ USD 10/month — mapped component by component in [`SPEC.md`](../../SPEC.md) (*Constraints*).

* **Environments:** dev + one staging (US) + production per region (US, EU); only US tenants' samples in staging (DL-D1, D12).
* **Two release tracks:** code deploys on merge, all at once per region; merges touching prompts, Jev questions, thresholds, policies or model config are held by a CI path rule for a scheduled release that passes the full EV-D5 gate and rolls out shadow → canary on read-only routes → all (DL-D2, D4, D10).
* **Models:** "latest" aliases for the three LLM tiers; the embedding model and Jev's version are pinned (DL-D3). Embedding changes re-index in place in a maintenance window (DL-D11).
* **In-flight workflows:** Continue-As-New at release boundaries, after draining pending signals; LangGraph checkpoints migrated (MS-D13) (DL-D6).
* **Rollback:** manual redeploy of the previous version (DL-D5).
* **Secrets:** a cloud secrets manager per region with scheduled rotation; the shared token key stored in each region's secrets manager, not rotated (DL-D7, D8).
* **Infrastructure:** Kubernetes per region (Helm) with infrastructure as code, incl. GPU serving for the self-hosted tier (DL-D9).

### Foundation: CONTINUOUS IMPROVEMENT (Capability 16)

> **Decided design:** [`checkpoint.md`](../decisions/checkpoint.md) §19 (CI-D1 – CI-D12) · diagram page `LLD - [15] Continuous Improvement`.

* **Signals:** thumbs, specialist reason codes, implicit signals (re-asks, escalations, abandonment, reopened cases), a one-question survey after resolution (CI-D1); free text classified and ranked by Jev (`Choice` category, `Score` severity) (CI-D11).
* **Analysis:** a hierarchical failure taxonomy mapped to components, versioned in code, reviewed monthly for new categories (CI-D2); weekly reviews by volume × severity and blameless postmortems for safety or financial incidents (CI-D3).
* **Levers:** prompts, Jev questions, thresholds, tool descriptions, KB articles (agent drafts, human publishes), curated few-shot examples excluded from tests, and per-region fine-tuning of the self-hosted model on opted-in, tokenized, positively-signalled conversations (CI-D4, D5, D6, CI-Q1, Q2).
* **Validation:** every reviewed failure becomes a test before its fix ships (clustered); release gate + before / after on the cluster + read-only A/B (CI-D7, D8). Changes approved by normal code review (CI-D10).
* **Evolution:** monthly user-distribution check; quarterly review of all owned risks; quarterly retraining without erased users' data (CI-D9, D12).

## 6. Comprehensive Component-to-Tooling Mapping Matrix

| Architecture Component | Primary Subcomponents | Recommended Frameworks & Tools | Key Trade-off / Decision |
| :--- | :--- | :--- | :--- |
| **[ 1 ] Channels** | Web widget, POST 202 + SSE, Typed Events | FastAPI (Slack / Teams SDKs later) | Web-only v1 on a channel-agnostic envelope (UA-D1, D2, D7) |
| **[ 2 ] Gateway** | Tiered Identity, On-Behalf-Of Tokens, Sessions | Kong / Traefik, FastAPI Security, IdP (TBD) | T0 → T1 → T2 tiers; RFC 8693 tokens (UA-D3, D4) |
| **[ 3 ] Reliability** | Rate Limits, Fallbacks, Coalescing, Latency, DR | Envoy / Kong, Temporal retries, counter store, self-hosted fallback LLM | Degrade instead of drop; no cross-region failover (RP-D2, D10) |
| **[ 4 ] Input Safety** | Normalization, PII tokens, Screening, Spotlighting | Presidio (custom) + vault, Llama Guard 3, injection classifier, Cedar | Pass / review / block bands; models see tokens only (SG-D2 – D5) |
| **[ 5 ] Orchestrator** | Intent, Planning, State Machine, Context | LangGraph, Temporal.io, Jev (TypeSafe), Langfuse | Python-native speed (LangGraph) vs industrial replay durability (Temporal) |
| **[ 6 ] Memory** | Conversation, Long-Term, Working, Agent State, Lifecycle | PostgreSQL + pgvector, LangGraph checkpointer, Jev | Verbatim + summary + pins; per-user facts with validity dates (MS-D2, D3, D4) |
| **[ 7 ] RAG Retrieval** | Sources, Ingestion, Indexing, Retrieval, Ranking, Eval | Qdrant (hybrid), LLM reranker | Hybrid BM25 + dense (RRF), screened LLM rerank, top-10 (KR-D7, D9, D10, D14) |
| **[Cap 12] Cost Router** | Tier Routing, Cascade, Answer Cache, Budgets, Tracking | Jev, prompt caching, self-hosted tier, exact usage counter | Jev difficulty routing + cascade; per-tenant cache only (CR-D1, D2, D3) |
| **[ 8 ] Model Runtime** | Inference Provider, Structured Decoding | LiteLLM, vLLM, OpenAI / Anthropic APIs | Hosted commercial frontier models vs self-hosted open-weights on vLLM |
| **[ 9 ] Tools & APIs** | Registry, Schemas, Invocation, Validation, Errors + Audit | Python + Pydantic v2, Temporal activities + sagas, Jev | Tiered approval ($1,000 default) with a Jev gate that only tightens (TA-D5, D6) |
| **[ 10 ] Multi-Agent** | Specialists, Delegation, Communication, Coordination | LangGraph subgraphs, Pydantic contracts, Jev | Coordinator + specialists (low-risk writes direct, rest via coordinator), one voice (MA-D1, D9, D12) |
| **[ 11 ] Confidence** | Jev Draft Gate, Bands, Numbers Rule | Jev | Send ≥ 0.90 · co-pilot 0.50–0.90 · hand off; money / SLA drafts reviewed (HL-D1, D2, D13) |
| **[ HITL ] Specialist** | Packet, Queues + SLAs, Approval Cards, Handoff, Feedback | Retool (self-hosted per region), Temporal signals, Cedar | Field-by-field approval; cold handoff (HL-D5, D6) |
| **[ 12 ] Output Safety** | Leakage Filter, URL Sanitizer, Promise Check | Rules + Comp [4] classifiers | Fast checks, no tone check; grounding lives in Comp 13 (SG-D6, D15) |
| **[ 13 ] Delivery** | SSE Streamer, State Sync, Two-Phase Commit | FastAPI StreamingResponse, Redis Pub/Sub, Kafka | Chunk-by-chunk streaming UX vs whole-response atomic guardrail check |
| **[ 14 ] User Client** | UI Render, Markdown, Micro-Feedback | React SDK, Web Components | Interactive action widgets vs static text responses |
| **Platform: Obs** | Traces, Logs, Metrics, Tokens, Failures | OpenTelemetry, Jaeger + OpenSearch, Jev | Tail sampling + 7-day retention; costs estimated from kept traces (OB-D4, D5, D13) |
| **Platform: Eval** | Datasets, Component + Risk-Tier Suites, Judge, Shadow / Canary | Ragas, DeepEval, Promptfoo, Langfuse | Assertions + cross-family LLM judge; zero risk-tier violations gate releases (EV-D2, D5) |
| **Platform: Testing** | Unit, Agent Behaviour, Contracts, E2E, Adversarial, Regression | pytest + Hypothesis, Cedar tests, contract tests | Mocked models; property tests of invariants; no retries for safety tests (TQ-D1, D2, D13) |
| **Platform: Deployment** | Environments, Release Tracks, Versioning, Secrets, Rollback | Kubernetes + Helm, IaC, secrets manager, Temporal Continue-As-New | Code continuous, behaviour gated + canaried; LLMs on latest, embedding + Jev pinned (DL-D3, D10) |
| **Platform: Improvement** | Feedback, Taxonomy, Levers, Validation, Evolution | Jev (feedback triage), EV suites, per-region fine-tuning | Every failure → test; fine-tune per region, retrain quarterly (CI-D4, D7, D12) |
