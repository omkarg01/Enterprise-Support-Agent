# Enterprise AI Support Agent: Low-Level System Design (LLD)

> **Companion High-Level Architecture:** [`architecture.md`](./architecture.md)  
> **Visual Whiteboard Canvas:** [`architecture.tldr`](./architecture.tldr)  
> **Capabilities Taxonomy:** [`thoughts.md`](./thoughts.md)  
> **Evaluation & Failure Modes:** [`user_evaluation_framework.md`](./user_evaluation_framework.md) · [`failure_modes_matrix.md`](./failure_modes_matrix.md)

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
* **Workflow & State Graph:** **LangGraph** (Native Python state-graph with conditional branching and checkpointing) OR **Temporal.io** (Industrial durable execution platform for multi-day human-in-the-loop workflows with zero data loss).
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

#### A. Architectural Responsibility
Maintains the continuum of knowledge across turns (short-term working memory), across sessions (episodic history), and across customer accounts (semantic user profile).

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MEMORY & STATE ENGINE                               │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Working Memory       │ 2. Episodic Session     │ 3. Long-Term Profile    │
│  • Active scratchpad    │  • Turn-by-turn history │  • Tenant metadata      │
│  • Intermediate tool res│  • Sliding window / sum  │  • Explicit preferences │
│  • Task state registers │  • Context compaction   │  • Historical issues    │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Memory Extraction    │ 5. Memory Retrieval     │ 6. Lifecycle & Garbage  │
│  • Async fact extractor │  • Hybrid entity lookup │  • GDPR right-to-forget │
│  • Contradiction resolver  • Semantic search     │  • TTL & invalidation   │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Working Memory (Scratchpad):**
   * *Capabilities:* Fast ephemeral key-value store maintaining current variables, active tool outputs, and unresolved plan steps for the active turn.
2. **Episodic Session Memory:**
   * *Capabilities:* Bounded multi-turn dialogue buffer; sliding-window token management; hierarchical summarization (summarizing older turns while preserving recent turns veridically).
3. **Long-Term User & Tenant Profile Store:**
   * *Capabilities:* Persistent storage of extracted facts (e.g., *"Sarah is Lead Architect at Acme Corp"*, *"Tenant uses AWS us-east-1"*); cross-session recall of customer preferences and chronic technical quirks.
4. **Memory Extraction & Reflection Pipeline:**
   * *Capabilities:* Background worker that asynchronously scans completed sessions, extracts atomic facts, resolves contradictions with prior facts, and commits verified memories.
5. **Memory Lifecycle & Privacy Manager:**
   * *Capabilities:* TTL expiration of stale context; tenant isolation guarantees; GDPR/CCPA compliance engine (hard-deletion of personal data on request).

#### C. Candidate Frameworks & Tooling Stack
* **Memory Management Engines:** **Mem0** (State-of-the-art personalized long-term memory engine with auto-extraction and graph relations) / **Zep** (Fast temporal context memory store for AI agents) / **SuperMemory**.
* **Storage Layer:** **Redis Stack** (Sub-millisecond working memory and sliding session cache) + **PostgreSQL with pgvector** (Relational long-term memory and entity embeddings).

#### D. Architectural Tradeoffs & Decisions
* **Raw Chat History vs. Extracted Fact Graph:**
  * Appending raw conversation history blows up token budgets and causes the *"Lost in the Middle"* attention degradation.
  * Extracting atomic structured facts (via **Mem0**) reduces token consumption by 70% and enables precise semantic lookup, but requires an async LLM extraction call.

---

### Component [ 7 ]: KNOWLEDGE & RAG RETRIEVAL ENGINE

#### A. Architectural Responsibility
Provides grounded enterprise documentation, product runbooks, API manuals, and past incident postmortems to ensure factually accurate responses.

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      KNOWLEDGE & RAG RETRIEVAL                              │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Ingestion Pipeline   │ 2. Chunking & Indexing  │ 3. Hybrid Retrieval     │
│  • Multi-source ETL     │  • Semantic / Markdown  │  • Dense Vector (HNSW)  │
│  • Metadata enrichment  │  • Code / Schema aware  │  • Sparse Keyword (BM25)│
│  • Change-data-capture  │  • Parent-child chunking│  • Multi-query expand   │
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Reranking Engine     │ 5. Context Compression  │ 6. Retrieval Evaluation │
│  • Cross-encoder rank   │  • Extractive summary   │  • Faithfulness scoring │
│  • Reciprocal Rank Fus. │  • Context trimming     │  • Context recall / hit │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Multi-Source Ingestion & Sync Pipeline:**
   * *Capabilities:* Connectors to Notion, Confluence, GitHub, Jira, and Zendesk; automated Change-Data-Capture (CDC) to re-index modified documents in near real-time.
2. **Context-Aware Semantic Chunking:**
   * *Capabilities:* Document-type-aware parsing (preserves code blocks, table schemas, and markdown headers intact); hierarchical parent-document chunking (retrieves small chunks for precision, expands to parent section for context).
3. **Hybrid Dense + Sparse Search Engine:**
   * *Capabilities:* Dense embedding similarity (semantic concepts) blended with Sparse BM25 / Splade (exact error codes, model names, ticket IDs); multi-query expansion generating 3 query variations.
4. **Cross-Encoder Reranking Engine:**
   * *Capabilities:* Deep cross-encoder scoring of top-50 candidates down to top-5 most relevant passages, filtering out semantic noise.
5. **Context Extractor & Compressor:**
   * *Capabilities:* Strips redundant boilerplate, legal headers, and navigation text before passing passages to the LLM context assembler.

#### C. Candidate Frameworks & Tooling Stack
* **Vector & Hybrid Search Engines:** **Qdrant** (Rust-based, high-throughput hybrid dense/sparse vector search with payload filtering) / **Weaviate** / **PostgreSQL pgvector**.
* **RAG Orchestration:** **LlamaIndex** (Deep document indexing and advanced retrieval strategies).
* **Reranker:** **Cohere Rerank v3** / **BGE-Reranker-Large** (Local open-source).
* **Embedding Models:** `text-embedding-3-large` (OpenAI) / `bge-large-en-v1.5`.

#### D. Architectural Tradeoffs & Decisions
* **Dense-Only Vector Search vs. Hybrid (BM25 + Dense):**
  * Dense vector search fails catastrophically on exact error codes (e.g. `ERR_504_TIMEOUT`) because embeddings cluster them with general network concepts.
  * Hybrid retrieval with **Reciprocal Rank Fusion (RRF)** is mandatory for enterprise technical support.

---

### Component [ Cap 12 ]: COST & RESOURCE ROUTER

#### A. Architectural Responsibility
Dynamically routes prompts to the optimal model tier, manages token consumption budgets, and operates semantic response caches to prevent redundant LLM invocations.

#### B. Subcomponents & Sub-Capabilities
1. **Semantic Cache Engine:**
   * *Capabilities:* Computes embedding of incoming query; matches against cached Q&A pairs with high cosine threshold (>0.96) within the same tenant context; serves sub-10ms answers for recurring FAQ tickets.
2. **Dynamic Model Tier Router:**
   * *Capabilities:* Evaluates query complexity score; routes simple FAQs to Fast Small Models (e.g. Claude 3.5 Haiku / Llama 3.1 8B / GPT-4o-mini) and complex multi-agent reasoning to Frontier Models (Claude 3.5 Sonnet / GPT-4o).
3. **Token Usage Meter & Quota Controller:**
   * *Capabilities:* Real-time token consumption tracking per tenant; alerts when monthly spend threshold reaches 80%; enforces hard circuit-breaking if quota is breached.

#### C. Candidate Frameworks & Tooling Stack
* **Model Gateway & Router:** **LiteLLM Proxy** (Unified proxy for 100+ LLMs with fallbacks, load balancing, and cost tracking) / **Portkey.ai**.
* **Semantic Caching:** **GPTCache** / **Redis Semantic Cache**.

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

#### A. Architectural Responsibility
Provides the agent with safe, sandboxed, authenticated mechanisms to inspect and mutate enterprise business systems (CRM, ERP, Cloud Monitoring, Ticketing).

#### B. Subcomponents & Sub-Capabilities

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        TOOLS & ENTERPRISE APIS                              │
├─────────────────────────┬─────────────────────────┬─────────────────────────┤
│ 1. Tool Registry        │ 2. Schema Validator     │ 3. Tool Execution Engine│
│  • Catalog of APIs      │  • Pydantic validation  │  • Sandboxed runner     │
│  • RBAC permission maps │  • Param sanitization   │  • Connection pools     │
│  • Dynamic tool discov. │  • Type coercion        │  • Timeout & circuit br.│
├─────────────────────────┼─────────────────────────┼─────────────────────────┤
│ 4. Read/Write Sandbox   │ 5. Two-Phase Mutation   │ 6. Error Transformer    │
│  • Dry-run simulation   │  • Transaction rollback │  • LLM-friendly errors  │
│  • Read-only enforcement│  • Audit event logger   │  • Retry advice hints   │
└─────────────────────────┴─────────────────────────┴─────────────────────────┘
```

1. **Tool Registry & Capability Catalog:**
   * *Capabilities:* Dynamic registration of tool definitions (Salesforce, Jira, Stripe, CloudWatch); tenant-aware and role-based tool visibility (e.g. tier-1 users cannot see refund tools).
2. **Schema Validation & Parameter Sanitizer:**
   * *Capabilities:* Strict Pydantic validation of arguments generated by LLMs before dispatch; strips dangerous shell characters or SQL injection attempts.
3. **Execution Sandbox & Connection Pool:**
   * *Capabilities:* Isolated execution worker; connection pooling with rate-limit compliance per external API; timeout enforcement (max 10s per tool call).
4. **Dry-Run & Two-Phase Mutation Controller:**
   * *Capabilities:* Simulates destructive changes (dry-run mode); staging environment routing; audit trail generation before committing external API mutations.
5. **Error Normalizer & Reflection Hints:**
   * *Capabilities:* Translates cryptic HTTP 500 or SOAP error XML into clean, semantically meaningful error messages with diagnostic hints for the LLM to recover.

#### C. Candidate Frameworks & Tooling Stack
* **Tool Registry & Standards:** **Model Context Protocol (MCP)** (Anthropic's standardized protocol for tools and resources) / **LangChain Toolkits**.
* **Validation:** **Pydantic v2**.
* **Resilience:** **Tenacity** (Python retry library) / **PyBreaker** (Circuit breaker).

---

### Component [ 10 ]: MULTI-AGENT SPECIALIST SWARM

#### A. Architectural Responsibility
Coordinates specialized autonomous sub-agents with dedicated system prompts, distinct tool access, and bounded operational domains.

#### B. Subcomponents & Sub-Capabilities
1. **Specialist Sub-Agents:**
   * *Billing Specialist Agent:* Access to Stripe, NetSuite, SAP; specializes in invoice disputes, refunds, and contract renewals.
   * *Technical Support Specialist Agent:* Access to CloudWatch, Datadog, GitHub; specializes in log analysis, infrastructure diagnostics, and code runbooks.
   * *Account & Operations Agent:* Access to Okta, Auth0, CRM; specializes in SSO, permissions, and organization provisioning.
2. **Inter-Agent Communication & Delegation Protocol:**
   * *Capabilities:* Structured inter-agent message passing (A2A messaging); subtask parameter contract negotiation; supervisor-worker coordination topology.
3. **Sub-Agent State Aggregator & Conflict Resolver:**
   * *Capabilities:* Reconciles disparate findings from multiple specialists into a unified diagnostic synthesis; detects and breaks infinite delegation loops.

#### C. Candidate Frameworks & Tooling Stack
* **Multi-Agent Orchestration:** **LangGraph Multi-Agent** (Hierarchical supervisor-worker graphs) / **CrewAI** / **AutoGen**.

---

## 3. Tier 1: Ingress & Edge Security Tier

---

### Component [ 1 ]: USER CHANNELS & INGRESS
* **Subcomponents:**
  * Multi-Channel Ingress Gateway (Web Widget, Mobile SDK, Slack Bot, MS Teams Bot, Email Ingress).
  * Protocol Normalizer (converts disparate Slack/Teams events into uniform internal `MessagePayload` objects).
* **Tooling:** **FastAPI** / **WebSockets** / **Slack Bolt SDK** / **Bot Framework SDK**.

### Component [ 2 ]: API GATEWAY & IDENTITY
* **Subcomponents:**
  * JWT/OAuth2 Introspection Engine (Validates user identity, tenant ID, and permissions).
  * Tenant Context Injector (Decorates request context with verified customer entitlements and SLA tier).
* **Tooling:** **Kong Gateway** / **Traefik** / **FastAPI Security** / **Auth0 / Okta JWT SDK**.

### Component [ 3 ]: RELIABILITY & RESILIENCE
* **Subcomponents:**
  * Token Bucket Rate Limiter (Per-user and per-tenant burst protection).
  * Circuit Breakers & Fallback Router (Trips when downstream services degrade; serves graceful cached fallbacks).
  * Idempotency & Debounce Controller (Deduplicates rapid repeated message submissions).
* **Tooling:** **Redis Token Bucket** / **Envoy Proxy Rate Limiting** / **Hystrix-pattern / PyBreaker**.

### Component [ 4 ]: INPUT SAFETY GUARDRAILS
* **Subcomponents:**
  * Prompt Injection & Jailbreak Shield (Deep classifier checking for override commands).
  * PII Detection & Tokenization Engine (Masks SSNs, credit cards, and private passwords before cognitive core).
  * DoS / Payload Size Guard (Rejects abnormal payloads).
* **Tooling:** **Guardrails AI** / **NeMo Guardrails** (NVIDIA) / **Microsoft Presidio** (PII masking) / **Llama Guard 3**.

---

## 4. Tier 3: Governance, HITL & Response Delivery Tier

---

### Component [ 11 ]: CONFIDENCE GATE & EVALUATION BOUNDARY
* **Subcomponents:**
  * Multi-Factor Confidence Scorer (Aggregates RAG citation density, LLM logprobs, and semantic entropy).
  * Financial & Mutation Risk Evaluator (Enforces hard dollar and permission thresholds, e.g., >$1,000 refund requires human).
  * Sentiment & Frustration Detector (Detects escalating user anger and routes to senior specialists).
* **Tooling:** Custom Confidence Rule Engine / **TruLens Feedback Functions**.

### Component [ HITL ]: HUMAN SUPPORT SPECIALIST CONSOLE
* **Subcomponents:**
  * Escalation Queue Router (Routes priority tickets to available qualified specialists).
  * Evidence & Context Presenter (Displays conversation transcript, tool logs, RAG citations, and one-click action buttons).
  * Warm Handoff / Specialist Override Engine (Allows specialist to approve, edit, or take over conversation).
* **Tooling:** **Retool** / **Streamlit** / **Zendesk Agent Workspace Custom App**.

### Component [ 12 ]: OUTPUT SAFETY GUARDRAILS
* **Subcomponents:**
  * Hallucination & Factuality Shield (Verifies that response claims are grounded in retrieved RAG docs or tool results).
  * Brand Tone & Policy Compliance Checker (Enforces professional, empathetic tone; checks competitor mentions).
  * Secret Exfiltration Filter (Ensures internal IP addresses, API keys, and system prompts are never leaked).
* **Tooling:** **NeMo Guardrails** / **Guardrails AI** / **Ragas Faithfulness Scorer**.

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
* **Components:**
  * Relational Database (PostgreSQL): Session records, tenant profiles, audit logs, credit memos.
  * Vector Database (Qdrant): Enterprise document embeddings, semantic cache.
  * Ephemeral Cache (Redis): Working memory, rate limits, session locks.
  * Object Storage (AWS S3): Large attachments, raw interaction dumps.

### Foundation: OBSERVABILITY & TRACING (Capability 10)
* **Components:**
  * Distributed OpenTelemetry Tracing: End-to-end spans tracing every hop (Gateway $\to$ Safety $\to$ Orchestrator $\to$ Tools $\to$ Delivery).
  * Metrics & Cost Dashboard: Token usage tracking, cost per conversation, P95 latency monitoring.
* **Tooling:** **OpenTelemetry SDK** / **Langfuse** / **LangSmith** / **Prometheus & Grafana**.

### Foundation: EVALUATION & LLMOPS (Capabilities 9, 14, 15, 16)
* **Components:**
  * Offline Benchmark Suite: Evaluates regression against golden datasets on every Git commit.
  * LLM-as-a-Judge Pipeline: Evaluates groundedness, answer relevance, and safety.
  * Continuous Improvement Pipeline: Curates negative user feedback into automated few-shot test cases.
* **Tooling:** **Ragas** / **DeepEval** / **Promptfoo** / **TrueFoundry / MLflow**.

---

## 6. Comprehensive Component-to-Tooling Mapping Matrix

| Architecture Component | Primary Subcomponents | Recommended Frameworks & Tools | Key Trade-off / Decision |
| :--- | :--- | :--- | :--- |
| **[ 1 ] Channels** | Ingress, Webhooks, Protocol Normalizer | FastAPI, WebSockets, Slack Bolt, Teams SDK | Standardized internal message schema vs channel-specific custom cards |
| **[ 2 ] Gateway** | Auth, JWT, Tenant Permissions | Kong Gateway, Auth0/Okta SDK, Traefik | Edge gateway validation vs application-level middleware auth |
| **[ 3 ] Reliability** | Rate Limiting, Circuit Breakers, Debounce | Redis Token Bucket, Envoy, PyBreaker | Strict 429 drops vs graceful queued degraded responses |
| **[ 4 ] Input Safety** | Injection Defense, PII Masking | Microsoft Presidio, NeMo Guardrails, Llama Guard | Regex speed vs deep classifier latency and false-positive rates |
| **[ 5 ] Orchestrator** | Intent, Planning, State Machine, Context | LangGraph, Temporal.io, Jev (TypeSafe), Langfuse | Python-native speed (LangGraph) vs industrial replay durability (Temporal) |
| **[ 6 ] Memory** | Working Scratchpad, Session, Long-Term Profile | Mem0, Zep, Redis Stack, PostgreSQL | Full conversation dump vs extracted atomic semantic fact graph |
| **[ 7 ] RAG Retrieval** | Chunking, Ingestion, Hybrid Search, Reranking | Qdrant, LlamaIndex, Cohere Rerank, BGE | Dense-only search vs hybrid dense+BM25 with cross-encoder rerank |
| **[Cap 12] Cost Router** | Semantic Cache, Model Tier Routing, Budgets | LiteLLM Proxy, GPTCache, Portkey.ai | Aggressive caching vs fresh dynamic reasoning; SLM routing risks |
| **[ 8 ] Model Runtime** | Inference Provider, Structured Decoding | LiteLLM, vLLM, OpenAI / Anthropic APIs | Hosted commercial frontier models vs self-hosted open-weights on vLLM |
| **[ 9 ] Tools & APIs** | Registry, Sandbox, Schema Validator, Rollback | Model Context Protocol (MCP), Pydantic v2, Tenacity | Direct API execution vs dry-run staging simulation with rollback |
| **[ 10 ] Multi-Agent** | Domain Specialists, Delegation, Coordination | LangGraph Multi-Agent, CrewAI, AutoGen | Autonomous peer swarm vs strict hierarchical supervisor control |
| **[ 11 ] Confidence** | Metric Scorer, Risk Boundary, Escalation Gate | Custom Rule Engine, TruLens Feedback Functions | High automation rate vs conservative human escalation overhead |
| **[ HITL ] Specialist** | Escalation Queue, Evidence UI, Override Console | Retool, Streamlit, Zendesk Custom Apps | Specialist review latency vs immediate unverified AI autonomy |
| **[ 12 ] Output Safety** | Hallucination Shield, Tone Check, Secret Filter | Guardrails AI, NeMo Guardrails, Ragas | Output validation latency (extra LLM pass) vs compliance assurance |
| **[ 13 ] Delivery** | SSE Streamer, State Sync, Two-Phase Commit | FastAPI StreamingResponse, Redis Pub/Sub, Kafka | Chunk-by-chunk streaming UX vs whole-response atomic guardrail check |
| **[ 14 ] User Client** | UI Render, Markdown, Micro-Feedback | React SDK, Web Components | Interactive action widgets vs static text responses |
| **Platform: Obs** | Traces, Metrics, Token & Cost Tracking | OpenTelemetry, Langfuse, Prometheus, Datadog | Tracing overhead and sampled logging vs complete observability audit |
| **Platform: Eval** | Golden Sets, LLM-as-a-Judge, Regression | Ragas, DeepEval, Promptfoo, TrueFoundry | Expensive LLM-as-a-judge eval vs deterministic heuristic assertions |
