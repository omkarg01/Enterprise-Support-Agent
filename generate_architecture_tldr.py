"""
Enterprise AI Support Agent — Architecture Diagram Generator  v4.2
=================================================================
Clean Typography & Collision-Free Topology:
- Widen column gap to 140px so all connector arrow labels fit cleanly
  without overlapping any component box borders.
- Integrate numbered step badges directly into the component titles
  (e.g., ① USER CHANNELS, ② API GATEWAY, etc.) so numbers and text
  never overlap connector labels.
- Remove separate floating circle badges that were colliding with arrow text.
- Column-aligned parallel telemetry drops into Foundation Platform.
"""

import json

SCHEMA = {
    "schemaVersion": 2,
    "sequences": {
        "com.tldraw.store": 5,
        "com.tldraw.asset": 1,
        "com.tldraw.camera": 1,
        "com.tldraw.document": 2,
        "com.tldraw.instance": 26,
        "com.tldraw.instance_page_state": 5,
        "com.tldraw.instance_presence": 6,
        "com.tldraw.page": 1,
        "com.tldraw.pointer": 1,
        "com.tldraw.shape": 4,
        "com.tldraw.asset.bookmark": 2,
        "com.tldraw.asset.image": 5,
        "com.tldraw.asset.video": 5,
        "com.tldraw.shape.arrow": 8,
        "com.tldraw.shape.bookmark": 2,
        "com.tldraw.shape.draw": 4,
        "com.tldraw.shape.embed": 4,
        "com.tldraw.shape.frame": 1,
        "com.tldraw.shape.geo": 11,
        "com.tldraw.shape.group": 0,
        "com.tldraw.shape.highlight": 3,
        "com.tldraw.shape.image": 5,
        "com.tldraw.shape.line": 5,
        "com.tldraw.shape.note": 10,
        "com.tldraw.shape.text": 4,
        "com.tldraw.shape.video": 4,
        "com.tldraw.binding": 0,
        "com.tldraw.binding.arrow": 1,
    },
}

class _Idx:
    _C = "123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    def __init__(self):
        self._n = 0
    def __call__(self):
        self._n += 1
        n = self._n
        b = len(self._C)
        return "a" + self._C[(n // b) % b] + self._C[n % b]

def rt(text: str) -> dict:
    parts = []
    for line in str(text).split("\n"):
        if line.strip():
            parts.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        else:
            parts.append({"type": "paragraph"})
    return {"type": "doc", "content": parts}

def _base(sid, kind, x, y, idx):
    return {
        "typeName": "shape", "id": sid, "type": kind,
        "x": x, "y": y, "rotation": 0, "isLocked": False,
        "opacity": 1, "parentId": "page:page",
        "index": idx(), "meta": {},
    }

def box(sid, label, x, y, w, h, color, fill, idx, size="s", font="sans", align="middle"):
    s = _base(sid, "geo", x, y, idx)
    s["props"] = {
        "geo": "rectangle", "dash": "draw", "url": "",
        "w": w, "h": h, "growY": 0, "scale": 1,
        "labelColor": "black", "color": color, "fill": fill,
        "size": size, "font": font,
        "align": align, "verticalAlign": "middle",
        "richText": rt(label),
    }
    return s

def frame(sid, label, x, y, w, h, color, idx):
    s = _base(sid, "geo", x, y, idx)
    s["props"] = {
        "geo": "rectangle", "dash": "dashed", "url": "",
        "w": w, "h": h, "growY": 0, "scale": 1,
        "labelColor": color, "color": color, "fill": "none",
        "size": "s", "font": "sans",
        "align": "start", "verticalAlign": "start",
        "richText": rt(label),
    }
    return s

def txt(sid, label, x, y, w, color, size, idx):
    s = _base(sid, "text", x, y, idx)
    s["props"] = {
        "richText": rt(label), "size": size, "font": "sans",
        "textAlign": "start", "color": color,
        "w": w, "scale": 1, "autoSize": True,
    }
    return s

def arrow(sid, x1, y1, x2, y2, label, idx,
          bend=0, color="black", size="s"):
    s = _base(sid, "arrow", x1, y1, idx)
    s["props"] = {
        "kind": "arc", "labelColor": "black",
        "color": color, "fill": "none", "dash": "draw",
        "size": size, "arrowheadStart": "none", "arrowheadEnd": "arrow",
        "font": "sans",
        "start": {"x": 0, "y": 0},
        "end":   {"x": x2 - x1, "y": y2 - y1},
        "bend": bend,
        "richText": rt(label),
        "labelPosition": 0.5, "scale": 1, "elbowMidPoint": 0.5,
    }
    return s


def build():
    idx = _Idx()
    rec = [
        {"typeName": "document", "id": "document:document",
         "gridSize": 10, "name": "", "meta": {}},
        {"typeName": "page", "id": "page:page",
         "name": "Enterprise Support Agent Architecture", "index": "a1", "meta": {}},
        {"typeName": "camera", "id": "camera:page:page",
         "x": -20, "y": -20, "z": 0.42, "meta": {}},
    ]
    R = rec

    # ─────────────────────────────────────────────────────────────────
    # SPATIAL GRID SYSTEM (Pixels)
    # Generous 140px gap between columns guarantees connector labels
    # have ample breathing room and never collide with box borders.
    # ─────────────────────────────────────────────────────────────────
    NW = 370        # Node Width
    NH = 115        # Node Height
    GAP_X = 140     # Gap between columns (plenty of room for arrow labels)
    
    COL1 = 80                           # Col 1: Channels, Client, QA/LLMOps
    COL2 = COL1 + NW + GAP_X            # 590: Gateway, Context, Delivery, DB
    COL3 = COL2 + NW + GAP_X            # 1100: Reliability, Orchestrator, Safety, Eval
    COL4 = COL3 + NW + GAP_X            # 1610: Input Safety, LLM/Multi, Confidence, Obs
    COL5 = COL4 + NW + GAP_X            # 2120: Tools, HITL, Continuous Improve
    
    TOTAL_CANVAS_W = COL5 + NW + 80     # 2570 px wide canvas

    # Row Y Positions
    Y_TITLE = 20
    
    # TIER 1: Ingress (y ≈ 90 to 275)
    T1_FRAME_Y = 90
    T1_NODE_Y  = 145
    T1_FRAME_H = 185

    # TIER 2: Reasoning Core (y ≈ 335 to 730)
    T2_FRAME_Y = 335
    T2_ROW1_Y  = 395
    T2_ROW2_Y  = 595
    T2_FRAME_H = 375

    # TIER 3: Governance & Delivery (y ≈ 770 to 1010)
    T3_FRAME_Y = 770
    T3_NODE_Y  = 825
    T3_FRAME_H = 185

    # TIER 4: Foundation Platform (y ≈ 1015 to 1255)
    T4_FRAME_Y = 1015
    T4_NODE_Y  = 1070
    T4_FRAME_H = 185

    # Registry for precise anchor points
    _b: dict[str, tuple[float, float, float, float]] = {}
    def reg(nid, x, y, w=NW, h=NH):
        _b[nid] = (x, y, w, h)

    def R_(nid): bx, by, bw, bh = _b[nid]; return bx + bw, by + bh / 2
    def L_(nid): bx, by, bw, bh = _b[nid]; return bx,      by + bh / 2
    def T_(nid): bx, by, bw, bh = _b[nid]; return bx + bw / 2, by
    def B_(nid): bx, by, bw, bh = _b[nid]; return bx + bw / 2, by + bh

    # Helper for adjacent connector arrows
    def connect(aid, from_nid, to_nid, label="",
                from_anchor="R", to_anchor="L",
                color="black", size="s", bend=0):
        anchors = {"R": R_, "L": L_, "T": T_, "B": B_}
        x1, y1 = anchors[from_anchor](from_nid)
        x2, y2 = anchors[to_anchor](to_nid)
        R.append(arrow(f"shape:a_{aid}", x1, y1, x2, y2,
                       label, idx, bend=bend, color=color, size=size))

    # ─── HEADER TITLES ───────────────────────────────────────────────
    R.append(txt("shape:t1",
                 "ENTERPRISE AI SUPPORT AGENT  —  SYSTEM ARCHITECTURE & LIFECYCLE",
                 COL1, Y_TITLE, 2400, "black", "xl", idx))
    R.append(txt("shape:t2",
                 "Complete End-to-End Flow  ·  All 16 Capabilities  ·  Clean Orthogonal Connectors with Integrated Numbering",
                 COL1, Y_TITLE + 38, 2400, "grey", "s", idx))

    # ═════════════════════════════════════════════════════════════════
    # TIER 1 — INGRESS & EDGE SECURITY (Caps 1, 7, 11)
    # Flow direction: LEFT ──► RIGHT
    # ═════════════════════════════════════════════════════════════════
    t1_frame_w = (COL4 + NW) - COL1 + 40
    R.append(frame("shape:f_t1",
                   "①  INGRESS & EDGE SECURITY TIER  (Capabilities 1, 7, 11)  ──  User Query Entry & Edge Validation",
                   COL1 - 20, T1_FRAME_Y, t1_frame_w, T1_FRAME_H, "blue", idx))

    # 1A: User Channels (Col 1)
    R.append(box("shape:n_channels",
                 "[ 1 ] User Channels & Ingress\nWeb widget only (v1) · more later\n→ page: LLD - [1] User & Application",
                 COL1, T1_NODE_Y, NW, NH, "blue", "semi", idx))
    reg("n_channels", COL1, T1_NODE_Y)

    # 1B: API Gateway & Identity (Col 2)
    R.append(box("shape:n_gateway",
                 "[ 2 ] API Gateway & Identity\nPOST 202 · Tiers T0/T1/T2 · Sessions\n→ page: LLD - [1] User & Application",
                 COL2, T1_NODE_Y, NW, NH, "blue", "semi", idx))
    reg("n_gateway", COL2, T1_NODE_Y)

    # 1C: Reliability & Traffic Control (Col 3)
    R.append(box("shape:n_reliability",
                 "[ 3 ] Reliability & Resilience\nRate Limiting · Token Bucket\nCircuit Breakers & Fallback",
                 COL3, T1_NODE_Y, NW, NH, "orange", "semi", idx))
    reg("n_reliability", COL3, T1_NODE_Y)

    # 1D: Input Safety Guardrails (Col 4)
    R.append(box("shape:n_input_safe",
                 "[ 4 ] Input Safety Guardrails\nPrompt Injection Shield\nPII Masking & Sanitization",
                 COL4, T1_NODE_Y, NW, NH, "orange", "semi", idx))
    reg("n_input_safe", COL4, T1_NODE_Y)

    # Tier 1 Horizontal Direct Connections (All between immediate neighbors, length = 140px)
    connect("t1_1", "n_channels", "n_gateway", "Query Payload",
            from_anchor="R", to_anchor="L", color="blue")
    connect("t1_2", "n_gateway", "n_reliability", "Auth Verified",
            from_anchor="R", to_anchor="L", color="blue")
    connect("t1_3", "n_reliability", "n_input_safe", "Rate Checked",
            from_anchor="R", to_anchor="L", color="orange")

    # ═════════════════════════════════════════════════════════════════
    # TIER 2 — COGNITIVE REASONING CORE (Caps 2, 3, 4, 5, 6, 12)
    # ═════════════════════════════════════════════════════════════════
    t2_frame_w = (COL5 + NW) - (COL2 - 20) + 20
    R.append(frame("shape:f_t2",
                   "②  COGNITIVE REASONING & EXECUTION CORE  (Capabilities 2, 3, 4, 5, 6, 12)",
                   COL2 - 20, T2_FRAME_Y, t2_frame_w, T2_FRAME_H, "violet", idx))

    # ─── Context & Knowledge (Col 2) ──────────────────────────────
    R.append(box("shape:n_memory",
                 "[ 6 ] Memory & State Engine\nWorking Memory · Session State\nLong-Term User Profile Store",
                 COL2, T2_ROW1_Y, NW, NH, "light-blue", "semi", idx))
    reg("n_memory", COL2, T2_ROW1_Y)

    R.append(box("shape:n_rag",
                 "[ 7 ] Knowledge & RAG Retrieval\nBM25 + dense · LLM rerank · top-10\n→ see LLD - [2] Knowledge & Retrieval",
                 COL2, T2_ROW2_Y, NW, NH, "light-blue", "semi", idx))
    reg("n_rag", COL2, T2_ROW2_Y)

    # ─── Agent Orchestration Core (Col 3) ─────────────────────────
    ORCH_H = 135
    R.append(box("shape:n_orch",
                 "[ 5 ] AGENT ORCHESTRATION CORE\nIntent Classification · Planning\nState Machine & Context Assembly",
                 COL3, T2_ROW1_Y - 10, NW, ORCH_H, "violet", "semi", idx, size="m"))
    reg("n_orch", COL3, T2_ROW1_Y - 10, NW, ORCH_H)

    R.append(box("shape:n_cost",
                 "[ Cap 12 ] Cost & Resource Router\nModel Tier Routing · Budgets\nSemantic Caching & Token Meter",
                 COL3, T2_ROW2_Y, NW, NH, "violet", "semi", idx))
    reg("n_cost", COL3, T2_ROW2_Y)

    # ─── Model Runtime (Col 4) ───────────────────────────────────
    R.append(box("shape:n_llm",
                 "[ 8 ] Model Runtime & LLM\nFrontier / Fast Model Inference\nReasoning & Token Optimization",
                 COL4, T2_ROW1_Y, NW, NH, "yellow", "semi", idx))
    reg("n_llm", COL4, T2_ROW1_Y)

    R.append(box("shape:n_multi",
                 "[ 10 ] Multi-Agent Sub-Agents\nBilling · Tech · Ops Specialists\nDelegation Protocol & State",
                 COL4, T2_ROW2_Y, NW, NH, "yellow", "semi", idx))
    reg("n_multi", COL4, T2_ROW2_Y)

    # ─── Tools & Actions (Col 5) ─────────────────────────────────
    R.append(box("shape:n_tools",
                 "[ 9 ] Tools & Enterprise APIs\nCRM · ERP · Ticketing APIs\nSchema Validation & Sandbox",
                 COL5, T2_ROW1_Y, NW, NH, "yellow", "semi", idx))
    reg("n_tools", COL5, T2_ROW1_Y)

    # ─── Connections for Tier 2 (All strictly adjacent!) ──────────
    # Step 4 to 5: Ingress to Orchestrator (travels across the open gap between T1 and T2)
    connect("in_to_orch", "n_input_safe", "n_orch", "Sanitized Query",
            from_anchor="B", to_anchor="T", color="violet", size="m", bend=0)

    # Step 5 to 6: Orchestrator ◄──► Memory (Col 3 to Col 2)
    connect("orch_mem_req", "n_orch", "n_memory", "Fetch History",
            from_anchor="L", to_anchor="R", color="light-blue", bend=12)
    connect("orch_mem_res", "n_memory", "n_orch", "Session Loaded",
            from_anchor="R", to_anchor="L", color="violet", bend=12)

    # Step 5 to 7: Cost Router / Orchestrator ◄──► RAG (Col 3 to Col 2)
    connect("orch_rag_req", "n_cost", "n_rag", "Retrieve Docs",
            from_anchor="L", to_anchor="R", color="light-blue", bend=12)
    connect("orch_rag_res", "n_rag", "n_cost", "Top-k Passages",
            from_anchor="R", to_anchor="L", color="violet", bend=12)

    # Orchestrator down to Cost Router (Col 3 Row 1 to Row 2)
    connect("orch_to_cost", "n_orch", "n_cost", "Token Budget",
            from_anchor="B", to_anchor="T", color="violet", bend=0)

    # Step 5 to 8: Orchestrator ──► Model Runtime (Col 3 to Col 4)
    connect("orch_to_llm", "n_orch", "n_llm", "Assembled Prompt",
            from_anchor="R", to_anchor="L", color="yellow", size="m", bend=0)

    # Step 8 to 9: Model Runtime ◄──► Tools (Col 4 to Col 5)
    connect("llm_to_tools", "n_llm", "n_tools", "Tool Call",
            from_anchor="R", to_anchor="L", color="yellow", bend=12)
    connect("tools_to_llm", "n_tools", "n_llm", "Action Result",
            from_anchor="L", to_anchor="R", color="yellow", bend=12)

    # Step 8 to 10: Model Runtime ◄──► Multi-Agent Swarm (Col 4 Row 1 to Row 2)
    connect("llm_to_multi", "n_llm", "n_multi", "Delegate",
            from_anchor="B", to_anchor="T", color="yellow", bend=12)
    connect("multi_to_llm", "n_multi", "n_llm", "Subtask Answer",
            from_anchor="T", to_anchor="B", color="yellow", bend=12)

    # ═════════════════════════════════════════════════════════════════
    # TIER 3 — GOVERNANCE, HUMAN-IN-THE-LOOP & RESPONSE DELIVERY (Caps 7, 13, 1)
    # Flow direction: RIGHT ──► LEFT
    # ═════════════════════════════════════════════════════════════════
    t3_frame_w = TOTAL_CANVAS_W - COL1 - 60
    R.append(frame("shape:f_t3",
                   "③  GOVERNANCE, HUMAN-IN-THE-LOOP & RESPONSE DELIVERY TIER  (Caps 7, 13, 1)  ──  Right-to-Left Return Flow",
                   COL1 - 20, T3_FRAME_Y, t3_frame_w, T3_FRAME_H, "red", idx))

    # 3D: Human Specialist (Col 5)
    R.append(box("shape:n_hitl",
                 "[ HITL ] Human Support Specialist\nEscalation Review · Warm Handoff\nSpecialist Override Console",
                 COL5, T3_NODE_Y, NW, NH, "red", "semi", idx))
    reg("n_hitl", COL5, T3_NODE_Y)

    # 3C: Confidence Gate (Col 4)
    R.append(box("shape:n_confidence",
                 "[ 11 ] Confidence Gate & Eval\nScore Threshold (Auto / HITL)\nRisk & Sentiment Boundary",
                 COL4, T3_NODE_Y, NW, NH, "red", "semi", idx))
    reg("n_confidence", COL4, T3_NODE_Y)

    # 3B: Output Safety Guardrails (Col 3)
    R.append(box("shape:n_out_safety",
                 "[ 12 ] Output Safety Guardrails\nHallucination & Factuality Shield\nCorporate Policy & Tone Check",
                 COL3, T3_NODE_Y, NW, NH, "orange", "semi", idx))
    reg("n_out_safety", COL3, T3_NODE_Y)

    # 3A: Response Delivery Engine (Col 2)
    R.append(box("shape:n_delivery",
                 "[ 13 ] Response Delivery Engine\nTyped events · resumable SSE · inbox\n→ page: LLD - [1] User & Application",
                 COL2, T3_NODE_Y, NW, NH, "green", "semi", idx))
    reg("n_delivery", COL2, T3_NODE_Y)

    # 3-Terminal: Served User Client (Col 1)
    R.append(box("shape:n_served",
                 "[ 14 ] User Client (Served)\nResponse Rendered in UI\nAudit Logged · Feedback Loop",
                 COL1, T3_NODE_Y, NW, NH, "green", "semi", idx))
    reg("n_served", COL1, T3_NODE_Y)

    # ─── Tier 2 down into Tier 3 ─────────────────────────────────
    # From Multi-Agent / Execution (Col 4, Tier 2) straight down into Confidence Gate (Col 4, Tier 3)
    connect("exec_to_conf", "n_multi", "n_confidence", "Candidate Draft",
            from_anchor="B", to_anchor="T", color="red", size="m", bend=0)

    # ─── Tier 3 Right-to-Left Connections (All Adjacent!) ────────
    # Confidence Gate ◄──► HITL (Col 4 to Col 5)
    connect("conf_to_hitl", "n_confidence", "n_hitl", "Low Conf (<90%)\nEscalate",
            from_anchor="R", to_anchor="L", color="red", bend=12)
    connect("hitl_to_conf", "n_hitl", "n_confidence", "Specialist\nApproved",
            from_anchor="L", to_anchor="R", color="green", bend=12)

    # Confidence Gate ──► Output Safety (Col 4 to Col 3)
    connect("conf_to_safety", "n_confidence", "n_out_safety", "High Conf Pass (>=90%)",
            from_anchor="L", to_anchor="R", color="orange", bend=0)

    # Output Safety ──► Response Delivery Engine (Col 3 to Col 2)
    connect("safety_to_deliv", "n_out_safety", "n_delivery", "Verified Safe",
            from_anchor="L", to_anchor="R", color="green", size="m", bend=0)

    # Response Delivery ──► User Client (Col 2 to Col 1)
    connect("deliv_to_served", "n_delivery", "n_served", "Stream to User",
            from_anchor="L", to_anchor="R", color="green", size="m", bend=0)

    # Clean return loop up Column 1:
    # Column 1 has NO components in Tier 2 (Context is in Col 2).
    # A straight vertical line from `n_served` (Tier 3) up to `n_channels` (Tier 1)
    # connects the end back to the beginning through completely unobstructed space!
    connect("loop_complete", "n_served", "n_channels", "Lifecycle Completed (Session Open)",
            from_anchor="T", to_anchor="B", color="green", size="s", bend=0)

    # ═════════════════════════════════════════════════════════════════
    # TIER 4 — ENTERPRISE FOUNDATION PLATFORM (Caps 8-10, 14-16)
    # Strictly aligned across the same 5 columns (COL1 - COL5)
    # ═════════════════════════════════════════════════════════════════
    t4_frame_w = TOTAL_CANVAS_W - COL1 - 60
    R.append(frame("shape:f_t4",
                   "④  ENTERPRISE FOUNDATION PLATFORM  (Capabilities 8, 9, 10, 14, 15, 16)  ──  Shared Telemetry, Persistence & LLMOps",
                   COL1 - 20, T4_FRAME_Y, t4_frame_w, T4_FRAME_H, "grey", idx))

    # 5 Cards aligned exactly under the 5 columns:
    found_cards = [
        ("n_qa_ops",  "Testing & LLMOps (Caps 14, 15)\nUnit & E2E Suites · CI/CD\nModel Registry · Blue/Green", COL1),
        ("n_data",    "Data & Persistence (Cap 8)\nPostgreSQL · Qdrant Vector\nS3 Objects · Audit Event DB",    COL2),
        ("n_eval",    "Evaluation & Benchmarks (Cap 10)\nRagas / Trulens · Golden Eval\nLLM-as-a-Judge · Regression", COL3),
        ("n_obs",     "Observability & Tracing (Cap 9)\nOpenTelemetry · LangSmith\nPrometheus · Cost Dashboards",COL4),
        ("n_ci",      "Continuous Improve (Cap 16)\nUser Feedback · Failure Triage\nPrompt & Few-Shot Evolution",COL5),
    ]

    for nid, label, col_x in found_cards:
        R.append(box(f"shape:{nid}", label, col_x, T4_NODE_Y, NW, NH, "grey", "semi", idx, size="s"))
        reg(nid, col_x, T4_NODE_Y, NW, NH)

    # Telemetry feeds from Tier 3 directly down into Foundation Tier 4:
    # Each source node and target card are at the exact same column X!
    # Therefore, each arrow is a 100% pure vertical drop with dx = 0!
    connect("deliv_to_data", "n_delivery", "n_data", "State Sync",
            from_anchor="B", to_anchor="T", color="grey", size="s")
    connect("safety_to_eval", "n_out_safety", "n_eval", "Eval Logs",
            from_anchor="B", to_anchor="T", color="grey", size="s")
    connect("conf_to_obs", "n_confidence", "n_obs", "Traces",
            from_anchor="B", to_anchor="T", color="grey", size="s")
    connect("hitl_to_ci", "n_hitl", "n_ci", "HITL Feedback",
            from_anchor="B", to_anchor="T", color="grey", size="s")

    # ═════════════════════════════════════════════════════════════════
    # SECTION 2 — 2x2 UNKNOWNS & FAILURE MODES MATRIX (COMPONENTS & TRAJECTORY)
    # ═════════════════════════════════════════════════════════════════
    M_TITLE_Y = 1320
    R.append(txt("shape:m_title1",
                 "ENTERPRISE AI SUPPORT AGENT  —  COMPONENT & TRAJECTORY FAILURE MODES (2x2 UNKNOWNS)",
                 COL1, M_TITLE_Y, 2400, "black", "xl", idx))
    R.append(txt("shape:m_title2",
                 "Knowns vs. Unknowns Taxonomy  ·  Component-Level Vulnerabilities & Trajectory Breakdown Points  ·  All 14 Architecture Nodes",
                 COL1, M_TITLE_Y + 38, 2400, "grey", "s", idx))

    Q_W = 1175
    Q_H = 650
    Q_ROW1_Y = 1420
    Q_ROW2_Y = 2110
    Q_COL1_X = COL1
    Q_COL2_X = COL1 + Q_W + 60
    CW = 572
    CH = 250
    CGAP_X = 31
    CGAP_Y = 15

    # ─────────────────────────────────────────────────────────────────
    # QUADRANT 1: KNOWN KNOWNS (Top-Left, Blue)
    # "What's in your prompt" — Explicit Contracts, Schemas & Deterministic Bounds
    # ─────────────────────────────────────────────────────────────────
    R.append(frame("shape:f_q1",
                   "Q1  ·  KNOWN KNOWNS  ──  \"What's in your prompt\"  (Explicit Contracts, Hard Limits & Deterministic Rules)",
                   Q_COL1_X, Q_ROW1_Y, Q_W, Q_H, "blue", idx))
    
    R.append(box("shape:h_q1",
                 "KNOWN KNOWNS — EXPLICIT CONTRACT & SPECIFICATION BREACHES\nViolations of defined interfaces, hardcoded limits, known API schemas, and deterministic constraints.\nPredictable, verifiable with unit/integration tests, and fully rule-governed.",
                 Q_COL1_X + 15, Q_ROW1_Y + 35, Q_W - 30, 65, "blue", "semi", idx, size="s", align="start"))

    R.append(box("shape:q1_c1",
                 "[ 1 - 4 ] Ingress & Edge Rule Failures\n• [2] Expired or signature-tampered JWT not rejected by Gateway\n• [3] Token bucket exhausted; dropped without 429 Retry-After\n• [4] Regex PII mask misses spaced/dashed SSN / Card patterns\n• [1] Payload size exceeds max allowed HTTP body limit",
                 Q_COL1_X + 15, Q_ROW1_Y + 115, CW, CH, "light-blue", "semi", idx, size="s", align="start"))

    R.append(box("shape:q1_c2",
                 "[ 5 - 7 ] State Machine & Context Breaches\n• [5] State machine invalid transition (e.g. ticket close before triage)\n• [6] Key collision in Redis across concurrent sessions of same user\n• [7] Document chunk boundary splits critical error code mid-string\n• [Cap 12] Hard token budget ceiling reached; response abruptly clipped",
                 Q_COL1_X + 15 + CW + CGAP_X, Q_ROW1_Y + 115, CW, CH, "light-blue", "semi", idx, size="s", align="start"))

    R.append(box("shape:q1_c3",
                 "[ 8 - 10 ] Tool & Agent Interface Failures\n• [8] LLM output violates required JSON schema for tool invocation\n• [9] Unhandled HTTP 500 / 504 gateway timeout from Salesforce CRM\n• [9] Expired OAuth refresh token for SAP ERP integration\n• [10] Sub-agent missing required RPC payload arguments",
                 Q_COL1_X + 15, Q_ROW1_Y + 115 + CH + CGAP_Y, CW, CH, "light-blue", "semi", idx, size="s", align="start"))

    R.append(box("shape:q1_c4",
                 "[ 11 - 14 ] Governance & Delivery Breaks\n• [11] Currency conversion error bypasses $1,000 refund threshold\n• [HITL] Escalation ticket dispatched to empty off-hours staff queue\n• [12] Static profanity/competitor blacklist bypassed via homoglyph\n• [13] SSE connection drops on intermediate Nginx 60s timeout",
                 Q_COL1_X + 15 + CW + CGAP_X, Q_ROW1_Y + 115 + CH + CGAP_Y, CW, CH, "light-blue", "semi", idx, size="s", align="start"))

    # ─────────────────────────────────────────────────────────────────
    # QUADRANT 2: KNOWN UNKNOWNS (Top-Right, Orange)
    # "Questions you know to ask" — Stochastic Variance & Anticipated Uncertainties
    # ─────────────────────────────────────────────────────────────────
    R.append(frame("shape:f_q2",
                   "Q2  ·  KNOWN UNKNOWNS  ──  \"Questions you know to ask\"  (Stochastic Model Variance, Latency & Ambiguity)",
                   Q_COL2_X, Q_ROW1_Y, Q_W, Q_H, "orange", idx))

    R.append(box("shape:h_q2",
                 "KNOWN UNKNOWNS — STOCHASTIC VARIANCE & PROBABILISTIC UNCERTAINTIES\nAnticipated variability in model reasoning, semantic ambiguity, downstream network latency, and user fuzziness.\nWe know these variables exist, but their exact values and trajectories fluctuate dynamically.",
                 Q_COL2_X + 15, Q_ROW1_Y + 35, Q_W - 30, 65, "orange", "semi", idx, size="s", align="start"))

    R.append(box("shape:q2_c1",
                 "[ 1 - 4 ] Input Ambiguity & Network Jitter\n• [1] Highly vague user prompt ('System broken, please fix it')\n• [2] Downstream IdP token validation jitter (50ms vs 3000ms)\n• [3] Transient cold-start latency spike tripping upstream gateways\n• [4] Ambiguous injection classifier score (0.49 vs 0.50 threshold)",
                 Q_COL2_X + 15, Q_ROW1_Y + 115, CW, CH, "orange", "semi", idx, size="s", align="start"))

    R.append(box("shape:q2_c2",
                 "[ 5 - 7 ] Intent Ambiguity & RAG Mismatch\n• [5] 51/49% intent tie (Tech vs Billing); agent guesses instead of asking\n• [6] Context compaction drops critical user constraint from 5 turns ago\n• [7] High cosine similarity matches outdated runbook version\n• [Cap 12] Down-routes to cheap SLM to save cost; model fails reasoning",
                 Q_COL2_X + 15 + CW + CGAP_X, Q_ROW1_Y + 115, CW, CH, "orange", "semi", idx, size="s", align="start"))

    R.append(box("shape:q2_c3",
                 "[ 8 - 10 ] Non-Deterministic Tool & Swarm Paths\n• [8] Temperature fluctuation produces inconsistent tool argument plans\n• [9] CRM API returns 500 records when schema anticipated 1\n• [10] Tech & Billing Sub-Agents return conflicting conclusions\n• [9] Cloud Telemetry API latency drags to 12s under peak load",
                 Q_COL2_X + 15, Q_ROW1_Y + 115 + CH + CGAP_Y, CW, CH, "orange", "semi", idx, size="s", align="start"))

    R.append(box("shape:q2_c4",
                 "[ 11 - 14 ] Confidence Drift & User Abandonment\n• [11] Model is 95% confident in an entirely hallucinated root cause\n• [HITL] Specialist queue backlog delays review; user abandons chat\n• [12] Subtle factual hallucination in SLA percentage passes check\n• [13] High Time-To-First-Token (TTFT) creates perceived freeze",
                 Q_COL2_X + 15 + CW + CGAP_X, Q_ROW1_Y + 115 + CH + CGAP_Y, CW, CH, "orange", "semi", idx, size="s", align="start"))

    # ─────────────────────────────────────────────────────────────────
    # QUADRANT 3: UNKNOWN KNOWNS (Bottom-Left, Violet)
    # "I'll know it when I see it" — Implicit Assumptions, Common Sense & Etiquette
    # ─────────────────────────────────────────────────────────────────
    R.append(frame("shape:f_q3",
                   "Q3  ·  UNKNOWN KNOWNS  ──  \"I'll know it when I see it\"  (Tacit Enterprise Assumptions & Social Common Sense)",
                   Q_COL1_X, Q_ROW2_Y, Q_W, Q_H, "violet", idx))

    R.append(box("shape:h_q3",
                 "UNKNOWN KNOWNS — TACIT ASSUMPTIONS, BRAND TONE & ENTERPRISE ETIQUETTE\nExpectations so obvious to humans they were never explicitly coded into rules or prompts.\nImmediately recognized as blunders by any human operator upon inspection.",
                 Q_COL1_X + 15, Q_ROW2_Y + 35, Q_W - 30, 65, "violet", "semi", idx, size="s", align="start"))

    R.append(box("shape:q3_c1",
                 "[ 1 - 4 ] Tone Deafness & Overzealous Shields\n• [1] Cheerful marketing emoji greeting to panicked outage user\n• [4] PII filter over-masks technical log identifier 'admin' as [REDACTED]\n• [1] Treating high-priority executive escalation as a routine tier-1 ticket\n• [4] Forbidding diagnostic stack trace uploads as potential PII leaks",
                 Q_COL1_X + 15, Q_ROW2_Y + 115, CW, CH, "violet", "semi", idx, size="s", align="start"))

    R.append(box("shape:q3_c2",
                 "[ 5 - 7 ] Common Sense Reasoning Blindspots\n• [5] Sycophantic compliance: Agreeing with user's disastrous suggestion\n• [6] Creepy over-personalization: Referencing 2-year-old closed personal ticket\n• [7] Dumping 5-page generic boilerplate FAQ when user stated they tried it\n• [5] Instructing user to 'clear browser cache' during total cloud outage",
                 Q_COL1_X + 15 + CW + CGAP_X, Q_ROW2_Y + 115, CW, CH, "violet", "semi", idx, size="s", align="start"))

    R.append(box("shape:q3_c3",
                 "[ 8 - 10 ] Unwritten Domain & Safety Etiquette\n• [8] Pedantically lecturing senior engineers on basic networking terms\n• [9] Applying configuration change to Production without explicit prompt\n• [10] Sub-agents pass blame back and forth ('Not a billing/tech issue')\n• [9] Querying private employee compensation data without need-to-know",
                 Q_COL1_X + 15, Q_ROW2_Y + 115 + CH + CGAP_Y, CW, CH, "violet", "semi", idx, size="s", align="start"))

    R.append(box("shape:q3_c4",
                 "[ 11 - 14 ] Approval Fatigue & Bureaucratic Churn\n• [11] Requesting human sign-off on trivial read-only documentation\n• [HITL] Support specialist blindly rubber-stamps due to approval fatigue\n• [12] Cold, robotic legal detachment during catastrophic data loss\n• [13] Dumping raw unformatted JSON stack traces into executive chat",
                 Q_COL1_X + 15 + CW + CGAP_X, Q_ROW2_Y + 115 + CH + CGAP_Y, CW, CH, "violet", "semi", idx, size="s", align="start"))

    # ─────────────────────────────────────────────────────────────────
    # QUADRANT 4: UNKNOWN UNKNOWNS (Bottom-Right, Red)
    # "What you never considered" — Emergent Cascades & Black-Swan Dynamics
    # ─────────────────────────────────────────────────────────────────
    R.append(frame("shape:f_q4",
                   "Q4  ·  UNKNOWN UNKNOWNS  ──  \"What you never considered\"  (Emergent Cascades & Black-Swan Dynamics)",
                   Q_COL2_X, Q_ROW2_Y, Q_W, Q_H, "red", idx))

    R.append(box("shape:h_q4",
                 "UNKNOWN UNKNOWNS — EMERGENT MULTI-SYSTEM & CASCADING COLLAPSES\nSystemic, compounded failure modes arising from autonomous multi-agent interactions and feedback loops.\nPotholes nobody anticipated the road could have until they occurred in production.",
                 Q_COL2_X + 15, Q_ROW2_Y + 35, Q_W - 30, 65, "red", "semi", idx, size="s", align="start"))

    R.append(box("shape:q4_c1",
                 "[ 3, 5, 7 ] Thundering Herd Amplification Loop\n• Minor cloud glitch prompts 500 concurrent users to ask identical query\n• Orchestrator fires 500 simultaneous heavy hybrid RAG searches\n• RAG DB CPU spikes, causing timeouts across all agent instances\n• System retry storm amplifies minor glitch into total company outage",
                 Q_COL2_X + 15, Q_ROW2_Y + 115, CW, CH, "red", "semi", idx, size="s", align="start"))

    R.append(box("shape:q4_c2",
                 "[ 4, 7, 8 ] Indirect Prompt Injection via Ingested Logs\n• Attacker embeds adversarial prompt inside an application error message\n• Agent reads server error logs via monitoring tool / RAG retrieval\n• Ingested log payload hijacks agent instructions mid-reasoning\n• Model silently exfiltrates database credentials to external IP",
                 Q_COL2_X + 15 + CW + CGAP_X, Q_ROW2_Y + 115, CW, CH, "red", "semi", idx, size="s", align="start"))

    R.append(box("shape:q4_c3",
                 "[ 6, 12 ] Cross-Tenant Semantic Cache Poisoning\n• Attacker crafts deceptive query yielding plausible hallucinated response\n• Semantic cache stores hallucinated response with wide vector threshold\n• Subsequent legitimate enterprise tenants receive corrupted advice\n• Cache poisoning silently persists across hundreds of customer sessions",
                 Q_COL2_X + 15, Q_ROW2_Y + 115 + CH + CGAP_Y, CW, CH, "red", "semi", idx, size="s", align="start"))

    R.append(box("shape:q4_c4",
                 "[ 8, 9, 11, HITL ] Phantasm Action & Automation Seduction\n• LLM hallucinates it dispatched the refund API and confirms it to user\n• No actual API call dispatched; customer believes invoice credited\n• Specialists develop Automation Seduction from 99% historic accuracy\n• Catastrophic financial/legal discrepancy discovered weeks later",
                 Q_COL2_X + 15 + CW + CGAP_X, Q_ROW2_Y + 115 + CH + CGAP_Y, CW, CH, "red", "semi", idx, size="s", align="start"))

    return {"tldrawFileFormatVersion": 1, "schema": SCHEMA, "records": rec}


if __name__ == "__main__":
    out = "architecture.tldr"
    diagram = build()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(diagram, f, indent=2)
    n = len(diagram["records"])
    n_arrows = len([r for r in diagram["records"] if r.get("typeName")=="shape" and r.get("type")=="arrow"])
    print(f"Generated {out} ({n} records, {n_arrows} arrows)")
