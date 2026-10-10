# Enterprise AI Customer Support Agent__

[![Architecture](https://img.shields.io/badge/Architecture-Enterprise%20Multi--Agent-blue.svg)](#system-architecture)
[![Statechart](https://img.shields.io/badge/Orchestrator-LangGraph%20%2B%20Temporal-orange.svg)](#key-architectural-decisions)
[![Decisions](https://img.shields.io/badge/Decision%20Model-Jev%20(TypeSafe)-purple.svg)](#key-architectural-decisions)
[![License](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)

An enterprise-grade, high-reliability architecture and low-level engineering specification for an autonomous **Enterprise AI Customer Support Agent**. Built to handle mission-critical customer workflows, multi-day ticket lifecycles, regulatory compliance, context budget optimization, and human-in-the-loop (HITL) escalations.

---

## 📑 Documentation Index

| Document | Description |
| :--- | :--- |
| 📘 [**Master Architectural Report**](<./docs/architecture/Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md>) | Comprehensive 16-pillar architectural blueprint and literature review |
| 🏛️ [**System Architecture (HLD)**](./docs/architecture/architecture.md) | Abstract 7-stage pipeline, end-to-end data flow, and foundational platform |
| ⚙️ [**Low-Level Design (LLD)**](./docs/lld/low_level_design.md) | Component contracts, Pydantic schemas, state reducers, and interfaces |
| 🧠 [**Component 5: Orchestration Deep Dive**](./docs/architecture/component_5_orchestration_deep_dive.md) | Cognitive control plane, hybrid statecharts, context budgets, and resilience |
| 🛡️ [**Architectural Decisions (ADP Checkpoint)**](./docs/decisions/checkpoint.md) | Confirmed ADRs (ADP-01 to ADP-04) and pre-execution Genesis blueprint |
| ⚠️ [**Failure Modes & Unknowns Matrix**](./docs/analysis/failure_modes_matrix.md) | 2×2 Epistemic Matrix (Known/Unknown) mapping failure traps and recovery protocols |
| 🔄 [**Request-Response Lifecycle Walkthrough**](./docs/lld/request_response_lifecycle_example.md) | Detailed trace of a real-world enterprise request through all 14 components |
| 📊 [**Behavioral Evaluation Framework**](./docs/analysis/user_evaluation_framework.md) | User-centric behavioral archetypes, evaluation metrics, and safety benchmarks |
| 🎨 [**Interactive Canvas (`architecture.tldr`)**](./diagrams/architecture.tldr) | Multi-page visual system schematic and LLD statechart canvas |

---

## 🏛️ System Architecture

```mermaid
flowchart LR
    %% Styles
    classDef clientStyle fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#01579b;
    classDef safetyStyle fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100;
    classDef brainStyle fill:#ede7f6,stroke:#512da8,stroke-width:2px,color:#311b92;
    classDef contextStyle fill:#e8eaf6,stroke:#3949ab,stroke-width:2px,color:#1a237e;
    classDef execStyle fill:#fffde7,stroke:#fbc02d,stroke-width:2px,color:#f57f17;
    classDef hitlStyle fill:#ffebee,stroke:#d32f2f,stroke-width:2px,color:#b71c1c;
    classDef deliveryStyle fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#1b5e20;

    subgraph S1["1. Channels & Ingress"]
        User["User & Ingress Channels\n(Web, Mobile, Slack, Teams)"]:::clientStyle
    end

    subgraph S2["2. Gateway & Security"]
        Gateway["API Gateway & Identity\n(Auth, Session, Rate Limiting)"]:::safetyStyle
        InputSafety["Input Safety Shield\n(PII Masking, Injection Defense)"]:::safetyStyle
    end

    subgraph S3["3. Orchestration Core"]
        Orchestrator["Agent Orchestration Core\n(LangGraph Statechart & FSM)"]:::brainStyle
    end

    subgraph S4["4. Context & Knowledge"]
        Memory["Memory & State Engine\n(Working, Short-Term, Long-Term)"]:::contextStyle
        RAG["Knowledge & Retrieval (RAG)\n(Enterprise Docs, Hybrid Search)"]:::contextStyle
    end

    subgraph S5["5. Execution & Reasoning"]
        LLM["Model Runtime\n(Routing & Token Optimizer)"]:::execStyle
        Tools["Tools & External APIs\n(CRM, ERP, Billing, Ticketing)"]:::execStyle
        MultiAgent["Specialized Sub-Agents\n(Billing, Tech Support, Ops)"]:::execStyle
    end

    subgraph S6["6. Governance & HITL"]
        HITL{"Confidence &\nHITL Gate"}:::hitlStyle
        HumanSupport["Human Specialist\n(Review / Escalation)"]:::hitlStyle
        OutputSafety["Output Safety Guardrail\n(Policy & Hallucination Check)"]:::safetyStyle
    end

    subgraph S7["7. Delivery"]
        Delivery["Response Delivery\n(Streaming & Session Commit)"]:::deliveryStyle
    end

    User --> Gateway --> InputSafety --> Orchestrator
    Orchestrator <--> Memory
    Orchestrator <--> RAG
    Orchestrator --> LLM
    LLM --> Tools & MultiAgent
    Tools & MultiAgent --> Orchestrator
    Orchestrator --> HITL
    HITL -->|Escalate| HumanSupport
    HITL -->|Approved| OutputSafety
    OutputSafety --> Delivery --> User
```

---

## 🔑 Key Architectural Decisions (ADPs)

| ID | Component | Title | Selected Architecture |
| :--- | :--- | :--- | :--- |
| **ADP-01** | Agent Runtime | Planning Paradigm | **Hybrid Dual-Process Statechart** (Deterministic LangGraph FSM for compliance/SOPs + scoped ReAct in edge nodes) |
| **ADP-02** | Agent Runtime | Workflow Durability | **Two-Tier Hybrid** (Temporal.io outer saga for multi-day durability & HITL + LangGraph inner cognitive loop) |
| **ADP-03** | Agent Runtime | Context Engineering | **Tripartite Structured Slot Allocator** (System 15%, Profile 15%, RAG 35%, Chat 25%, Scratchpad 10%) |
| **ADP-04** | Agent Runtime | Error Recovery | **Dual-Process Circuit Breaker** (Max 2 Reflexion trials, immediate HITL tripwire on persistence) |
| **ADP-05** | Agent Runtime | Decision Model | **Jev (TypeSafe) at decision points** (triage, FSM transition guards, tool selection; LangGraph stays the runner, code owns control flow) |

All 79 ADPs (Orchestration + 15 components): [`docs/decisions/adp_index.md`](./docs/decisions/adp_index.md).

---

## 💸 v1 Deployment: Learning Profile

This is a learning project. The ADPs describe the **target production design** (AWS in US + EU, Kubernetes, multi-AZ, GPUs), which would cost roughly USD 6,500–9,500/month. **v1 runs on a learning deployment profile instead, capped at USD 10/month:**

* **One Oracle Cloud Always Free ARM VM** running Docker Compose: Temporal dev server + workers, LangGraph orchestrator, API, Jaeger, OpenBao, Ollama (and optionally Postgres / Qdrant).
* **Free managed services:** Vercel (frontend), Neon or Supabase (Postgres), Qdrant Cloud (vectors), Cloudflare R2 (objects / backups), Upstash Redis (counters), Langfuse or Grafana Cloud (traces), Hugging Face Spaces (Streamlit specialist console), GitHub Actions (CI).
* **Models:** Groq / Gemini free tiers (or Ollama) for most turns, the judge and screening; Claude only for the hardest turns, under a USD 10 console spend limit.

The full mapping from each production component to its v1 form is in [`SPEC.md`](./SPEC.md) (*Constraints → Learning deployment profile*).

---

## 🎨 Interactive Whiteboard Canvas

The repository includes an interactive [tldraw](https://www.tldraw.com) canvas [`architecture.tldr`](./diagrams/architecture.tldr):
* **Page 1: System Architecture & Failure Modes:** High-level end-to-end data flow and 2×2 Knowns/Unknowns failure mode cards.
* **Page 2: LLD - Agent Orchestration Core:** Low-level LangGraph statechart nodes, transitions, slot allocation diagrams, and circuit breaker tripwires.

### How to View the Canvas
1. **In VS Code:** Install the [tldraw extension](https://marketplace.visualstudio.com/items?itemName=tldraw-org.tldraw-vscode) and open [`architecture.tldr`](./diagrams/architecture.tldr) directly.
2. **In Browser:** Open [tldraw.com](https://www.tldraw.com), click `Menu (☰)` → `File` → `Open` and select `diagrams/architecture.tldr`.
3. **Regenerate / Customize:** Run the canvas generation scripts:
   ```bash
   python scripts/generate_architecture_tldr.py
   python scripts/generate_lld_page.py
   ```

---

## 🗂️ Repository Structure

```text
Enterprise Support Agent/
├── scripts/                                       # All Python diagram & canvas generator scripts
│   ├── lld_layout.py                              # Shared layout engine & bounding box math
│   ├── generate_architecture_tldr.py             # System architecture canvas generator (Page 1)
│   ├── generate_lld_page.py                       # Component 5 Orchestration canvas generator (Page 2)
│   └── generate_lld_*.py                          # Component 1-15 canvas generator scripts
│
├── docs/                                          # Design, architectural specifications, and ADRs
│   ├── architecture/                              # High-level architecture & master reports
│   │   ├── architecture.md                        # System Architecture (HLD)
│   │   ├── component_5_orchestration_deep_dive.md # Orchestration cognitive control plane deep dive
│   │   └── Enterprise AI Customer Support Agent Architecture - Formatted Master Report.md
│   │
│   ├── lld/                                       # Low-level design & execution traces
│   │   ├── low_level_design.md                    # Component contracts, schemas, and reducers
│   │   └── request_response_lifecycle_example.md  # End-to-end request walkthrough across all components
│   │
│   ├── analysis/                                  # Safety, failure analysis, and evaluation benchmarks
│   │   ├── failure_modes_matrix.md                # 2x2 Epistemic Known/Unknown failure traps matrix
│   │   └── user_evaluation_framework.md           # Behavioral archetypes & evaluation benchmarks
│   │
│   └── decisions/                                 # Architectural Decision Records (ADR) & logs
│       ├── checkpoint.md                          # ADRs (ADP-01 to ADP-05) & 15 closed component loops
│       └── thoughts.md                            # Component taxonomy & capability mapping scratchpad
│
├── diagrams/                                      # Interactive canvas & visual assets
│   └── architecture.tldr                          # Multi-page interactive tldraw canvas
│
├── .gitignore                                     # Repository Git configuration
└── README.md                                      # Repository root index and documentation portal
```

---

## 📂 Pre-Execution Genesis Codebase Blueprint

When initialized for automated code generation, the system maps to the following directory structure:

```
src/
├── core/
│   ├── orchestrator/
│   │   ├── statechart.py        # LangGraph StateGraph, Node reducers, FSM edges
│   │   ├── triage.py            # Acuity & Intent routing (Jev Choice/Score/Noul)
│   │   ├── decisions.py         # Jev question sets: triage, transition guards, tool selection
│   │   └── planner.py           # Layer 2 Deliberative ReAct engine
│   ├── context/
│   │   ├── assembler.py         # Tripartite slot budget allocator & token packing
│   │   └── models.py            # ContextEnvelope and typed Slot models
│   └── recovery/
│       ├── circuit_breaker.py   # Anti-loop detector & StepCeilingGuard
│       └── reflexion.py         # Verbal self-critique generator
├── workflows/
│   ├── ticket_saga.py           # Temporal workflow for durable ticket lifecycle
│   └── activities.py            # Activities invoking LangGraph cognitive turns
└── schemas/
    ├── state.py                 # Thread state schema S_t and Delta_t reducers
    └── diagnostic.py            # IncidentDiagnosticPacket for HITL escalation
```

---

## 👥 Authors & Acknowledgments

- **Omkar Gujja** ([@omkarg01](https://github.com/omkarg01))
