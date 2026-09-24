"""
Generate Abstract, High-Level Low-Level Design (LLD)
Component [ 5 ]: Agent Runtime & Orchestration Core

Design Philosophy:
- Abstract & Simple: Clean, visual system design without text overload or jargon density.
- Instant Human Explainability: Understandable in 5 seconds at a glance.
- Zero Arrow / Label Collisions: Wide 140px routing corridors.
- Deep Theory & Technical Details are preserved in 'component_5_orchestration_deep_dive.md'.
"""

import json
import os

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
    def __init__(self, start=1000):
        self._n = start
    def __call__(self):
        self._n += 1
        n = self._n
        b = len(self._C)
        return "b" + self._C[(n // b) % b] + self._C[n % b]

def rt(text: str) -> dict:
    parts = []
    for line in str(text).split("\n"):
        if line.strip():
            parts.append({"type": "paragraph", "content": [{"type": "text", "text": line}]})
        else:
            parts.append({"type": "paragraph"})
    return {"type": "doc", "content": parts}

def _base(sid, kind, x, y, idx, page_id="page:lld_orchestration"):
    return {
        "typeName": "shape", "id": sid, "type": kind,
        "x": x, "y": y, "rotation": 0, "isLocked": False,
        "opacity": 1, "parentId": page_id,
        "index": idx(), "meta": {},
    }

def box(sid, label, x, y, w, h, color, fill, idx, size="s", font="sans", align="start", vertical_align="start", page_id="page:lld_orchestration"):
    s = _base(sid, "geo", x, y, idx, page_id=page_id)
    s["props"] = {
        "geo": "rectangle", "dash": "draw", "url": "",
        "w": w, "h": h, "growY": 0, "scale": 1,
        "labelColor": "black", "color": color, "fill": fill,
        "size": size, "font": font,
        "align": align, "verticalAlign": vertical_align,
        "richText": rt(label),
    }
    return s

def frame(sid, label, x, y, w, h, color, idx, page_id="page:lld_orchestration"):
    s = _base(sid, "geo", x, y, idx, page_id=page_id)
    s["props"] = {
        "geo": "rectangle", "dash": "dashed", "url": "",
        "w": w, "h": h, "growY": 0, "scale": 1,
        "labelColor": color, "color": color, "fill": "none",
        "size": "s", "font": "sans",
        "align": "start", "verticalAlign": "start",
        "richText": rt(label),
    }
    return s

def arrow(sid, x1, y1, x2, y2, label, idx, bend=0, color="black", size="s", page_id="page:lld_orchestration"):
    s = _base(sid, "arrow", x1, y1, idx, page_id=page_id)
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

def build_abstract_lld_records():
    idx = _Idx(500)
    R = []
    pid = "page:lld_orchestration"

    PAGE_REC = {
        "typeName": "page",
        "id": pid,
        "name": "LLD - Agent Orchestration & Planning Core",
        "index": "a2",
        "meta": {}
    }
    CAMERA_REC = {
        "typeName": "camera",
        "id": f"camera:{pid}",
        "x": 0,
        "y": 0,
        "z": 0.46,
        "meta": {}
    }

    _b: dict[str, tuple[float, float, float, float]] = {}
    def reg(nid, x, y, w, h):
        _b[nid] = (x, y, w, h)

    def R_(nid): bx, by, bw, bh = _b[nid]; return bx + bw, by + bh / 2
    def L_(nid): bx, by, bw, bh = _b[nid]; return bx,      by + bh / 2
    def T_(nid): bx, by, bw, bh = _b[nid]; return bx + bw / 2, by
    def B_(nid): bx, by, bw, bh = _b[nid]; return bx + bw / 2, by + bh

    def connect(aid, from_nid, to_nid, label="", from_anchor="R", to_anchor="L", color="black", size="s", bend=0):
        anchors = {"R": R_, "L": L_, "T": T_, "B": B_}
        x1, y1 = anchors[from_anchor](from_nid)
        x2, y2 = anchors[to_anchor](to_nid)
        R.append(arrow(f"shape:lld_a_{aid}", x1, y1, x2, y2, label, idx, bend=bend, color=color, size=size, page_id=pid))

    TOTAL_W = 2600
    START_X = 80

    # ═════════════════════════════════════════════════════════════════
    # 1. HEADER & FOUNDATION PILLARS (y: 40 to 195)
    # ═════════════════════════════════════════════════════════════════
    R.append(box("shape:lld_title",
                 "COMPONENT [ 5 ]: AGENT RUNTIME & ORCHESTRATION CORE\n"
                 "Dual-Process Cognition (System 1/2) · Acuity Triage · Durable Statecharts & Circuit Breakers",
                 START_X, 40, TOTAL_W, 65, "blue", "semi", idx, size="m", align="middle", vertical_align="middle", page_id=pid))

    PW = (TOTAL_W - 3 * 30) / 4  # ~627px
    P_Y = 120
    P_H = 75

    R.append(box("shape:p1",
                 "① Dual-Process Cognition\n• System 1: Fast deterministic SOPs\n• System 2: Deliberative reasoning",
                 START_X, P_Y, PW, P_H, "light-blue", "semi", idx, size="s", page_id=pid))
    R.append(box("shape:p2",
                 "② Clinical Acuity Triage\n• Emergency Severity Index (ESI)\n• Intent & urgency routing",
                 START_X + (PW + 30), P_Y, PW, P_H, "light-green", "semi", idx, size="s", page_id=pid))
    R.append(box("shape:p3",
                 "③ Baddeley Working Memory\n• Structured 5-slot token budget\n• Prompt compaction & pruning",
                 START_X + 2 * (PW + 30), P_Y, PW, P_H, "light-violet", "semi", idx, size="s", page_id=pid))
    R.append(box("shape:p4",
                 "④ Reliability & Circuit Breaker\n• Anti-loop tripwires (N ≤ 4)\n• Reflexion retry + HITL handoff",
                 START_X + 3 * (PW + 30), P_Y, PW, P_H, "light-red", "semi", idx, size="s", page_id=pid))

    # ═════════════════════════════════════════════════════════════════
    # 2. ABSTRACT DUAL-PROCESS RUNTIME PIPELINE (y: 215 to 745)
    # ═════════════════════════════════════════════════════════════════
    Y_FLOW = 215
    H_FLOW = 530
    R.append(frame("shape:f_pipeline", "RUNTIME ORCHESTRATION PIPELINE (DUAL-PROCESS FLOW)", START_X, Y_FLOW, TOTAL_W, H_FLOW, "blue", idx, page_id=pid))

    # Column 1: Context Assembly & Acuity Triage (Left)
    C1_X = START_X + 30
    C1_W = 420

    reg("c1_asm", C1_X, Y_FLOW + 50, C1_W, 170)
    R.append(box("shape:c1_asm",
                 "1. Context Assembler & Ingress\n"
                 "• Receives query, channel ID & auth token\n"
                 "• Allocates 5 Baddeley token slots\n"
                 "  (System, Profile, RAG, Chat, Scratchpad)\n"
                 "• Compresses prompt payload via pruning",
                 C1_X, Y_FLOW + 50, C1_W, 170, "light-blue", "semi", idx, size="s", page_id=pid))

    reg("c1_triage", C1_X, Y_FLOW + 270, C1_W, 170)
    R.append(box("shape:c1_triage",
                 "2. Acuity & Intent Triage Gate (Jev, ADP-05)\n"
                 "• One Jev call: intent · urgency · missing info\n"
                 "• Routed in code by answer + confidence:\n"
                 "  ► Level 1/2: Standard SOP → System 1\n"
                 "  ► Level 4/5: Complex Issue → System 2\n"
                 "  ► Level 3: Ambiguous Intent → Clarify",
                 C1_X, Y_FLOW + 270, C1_W, 170, "light-green", "semi", idx, size="s", page_id=pid))

    connect("asm_to_trg", "c1_asm", "c1_triage", from_anchor="B", to_anchor="T", color="blue")

    # Column 2: Dual-Process Cognitive Engine (Center - 3 Parallel Tracks!)
    # Wide 150px gap between C1 and C2!
    C2_X = C1_X + C1_W + 150
    C2_W = 540

    # Top Track: System 1 (Deterministic FSM)
    reg("c2_fsm", C2_X, Y_FLOW + 45, C2_W, 140)
    R.append(box("shape:c2_fsm",
                 "System 1: Deterministic Statechart (FSM)\n"
                 "• Fast LangGraph state machine for verified SOPs\n"
                 "• Jev yes/no guards on edges · amounts & dates in code\n"
                 "• <300ms velocity · Zero generative hallucinations",
                 C2_X, Y_FLOW + 45, C2_W, 140, "green", "semi", idx, size="s", page_id=pid))

    # Middle Track: System 2 (Deliberative ReAct & Replanner)
    reg("c2_react", C2_X, Y_FLOW + 215, C2_W, 160)
    R.append(box("shape:c2_react",
                 "System 2: Deliberative ReAct & Replanner\n"
                 "• Multi-step Plan-and-Solve reasoning DAG\n"
                 "• Jev picks the tool · LLM writes the plan\n"
                 "• Reflexion critique (max 2) · Jev checks step success\n"
                 "• Anti-loop tripwire: Stops if steps > 4",
                 C2_X, Y_FLOW + 215, C2_W, 160, "orange", "semi", idx, size="s", page_id=pid))

    # Bottom Track: Ambiguity Return
    reg("c2_clarify", C2_X, Y_FLOW + 405, C2_W, 90)
    R.append(box("shape:c2_clarify",
                 "Ambiguity Gate: Clarification Return\n"
                 "• Detects missing information or 50/50 intent ties\n"
                 "• Returns clarifying question to customer UI",
                 C2_X, Y_FLOW + 405, C2_W, 90, "yellow", "semi", idx, size="s", page_id=pid))

    # Clean, spacious routing arrows from Triage into Dual-Process Core
    connect("trg_to_fsm", "c1_triage", "c2_fsm", label="SOP Route", from_anchor="R", to_anchor="L", color="green", size="m", bend=-12)
    connect("trg_to_react", "c1_triage", "c2_react", label="Complex Route", from_anchor="R", to_anchor="L", color="orange", size="m", bend=0)
    connect("trg_to_clarify", "c1_triage", "c2_clarify", label="Ambiguous", from_anchor="R", to_anchor="L", color="yellow", size="s", bend=12)

    # Fallback connector: System 1 error -> System 2 (clean downward arrow)
    connect("fsm_to_react", "c2_fsm", "c2_react", label="", from_anchor="B", to_anchor="T", color="orange", size="s")

    # Column 3: Execution Sandbox & Confidence Gate
    # Wide 150px gap between C2 and C3!
    C3_X = C2_X + C2_W + 150
    C3_W = 460

    reg("c3_sand", C3_X, Y_FLOW + 50, C3_W, 160)
    R.append(box("shape:c3_sand",
                 "3. Tool Execution Sandbox & Sagas\n"
                 "• Isolated MCP tool runner with RBAC checks\n"
                 "• Two-phase mutation checks & dry-run staging\n"
                 "• Distributed Saga compensating rollbacks",
                 C3_X, Y_FLOW + 50, C3_W, 160, "light-violet", "semi", idx, size="s", page_id=pid))

    reg("c3_gate", C3_X, Y_FLOW + 250, C3_W, 190)
    R.append(box("shape:c3_gate",
                 "4. Confidence & Policy Gate\n"
                 "• Evaluates citation groundedness & safety\n"
                 "• Human approval required for operations > $1,000\n"
                 "• Decision Threshold:\n"
                 "  ► Score ≥ 0.90: Approved → Persistence & Delivery\n"
                 "  ► Score < 0.90 / Breaker Tripped → Escalate to HITL",
                 C3_X, Y_FLOW + 250, C3_W, 190, "yellow", "semi", idx, size="s", page_id=pid))

    connect("sand_to_gate", "c3_sand", "c3_gate", from_anchor="B", to_anchor="T", color="violet")

    # Converging connectors into Sandbox
    connect("fsm_to_sand", "c2_fsm", "c3_sand", label="SOP Action", from_anchor="R", to_anchor="L", color="green", size="m", bend=-10)
    connect("react_to_sand", "c2_react", "c3_sand", label="Plan Action", from_anchor="R", to_anchor="L", color="orange", size="m", bend=6)

    # Column 4: Delivery & HITL Escalation (Right)
    # Wide 150px gap between C3 and C4!
    C4_X = C3_X + C3_W + 150
    C4_W = 460

    reg("c4_delivery", C4_X, Y_FLOW + 50, C4_W, 170)
    R.append(box("shape:c4_delivery",
                 "5. Persistence & Response Delivery\n"
                 "• Temporal durable saga snapshot (zero data loss)\n"
                 "• Real-time SSE / WebSocket token streaming to UI\n"
                 "• Async update to persistent customer memory\n"
                 "• Full OpenTelemetry trace & token cost logging",
                 C4_X, Y_FLOW + 50, C4_W, 170, "light-blue", "semi", idx, size="s", page_id=pid))

    reg("c4_hitl", C4_X, Y_FLOW + 270, C4_W, 170)
    R.append(box("shape:c4_hitl",
                 "Human Specialist Escalation (HITL)\n"
                 "• Dispatched on low confidence or repeated failure\n"
                 "• Full reasoning trajectory & citations presented\n"
                 "• Specialist reviews, edits, or approves action\n"
                 "• Human feedback feeds continuous evaluation",
                 C4_X, Y_FLOW + 270, C4_W, 170, "light-red", "semi", idx, size="s", page_id=pid))

    # Gating outcome connectors to Delivery and HITL
    connect("gate_to_delivery", "c3_gate", "c4_delivery", label="Score ≥ 0.90 (Approved)", from_anchor="R", to_anchor="L", color="green", size="m", bend=-12)
    connect("gate_to_hitl", "c3_gate", "c4_hitl", label="Score < 0.90 / Tripped", from_anchor="R", to_anchor="L", color="red", size="m", bend=10)

    # ═════════════════════════════════════════════════════════════════
    # 3. HIGH-LEVEL FAILURE MODES MATRIX (y: 765 to 1115)
    # ═════════════════════════════════════════════════════════════════
    Q_Y = 765
    Q_H = 320
    R.append(box("shape:lld_fail_hdr",
                 "COMPONENT [ 5 ] FAILURE MODES MATRIX (KNOWING YOUR UNKNOWNS)",
                 START_X, Q_Y, TOTAL_W, 45, "red", "semi", idx, size="m", align="middle", vertical_align="middle", page_id=pid))

    Q_HALF_W = (TOTAL_W - 30) / 2  # ~1285px each
    Q_COL1_X = START_X
    Q_COL2_X = START_X + Q_HALF_W + 30
    Q_BOX_H = 120

    # QUADRANT 1: KNOWN KNOWNS
    R.append(box("shape:q1",
                 "Q1 · KNOWN KNOWNS (Deterministic Contract Breaches)\n"
                 "• Token Overflow → Mitigated by strict Baddeley slot budget ceilings\n"
                 "• Illegal State Jump → Mitigated by typed LangGraph Pydantic StateGraph edges\n"
                 "• Tool Schema Rejection → Mitigated by Jev typed tool choice & schema validation\n"
                 "• Recursion Depth Ceiling → Mitigated by anti-loop StepCeilingGuard (N ≤ 4)",
                 Q_COL1_X, Q_Y + 55, Q_HALF_W, Q_BOX_H, "light-blue", "semi", idx, size="s", page_id=pid))

    # QUADRANT 2: KNOWN UNKNOWNS
    R.append(box("shape:q2",
                 "Q2 · KNOWN UNKNOWNS (Probabilistic Reasoning Variance)\n"
                 "• Intent Ambiguity Tie → Mitigated by Level-3 Acuity Gate clarifying question\n"
                 "• ReAct Reasoning Drift → Mitigated by Plan-and-Solve upfront DAG limits\n"
                 "• Context Compaction Loss → Mitigated by preserved negative constraint slots\n"
                 "• Latency Spikes → Mitigated by token chunk streaming & Temporal heartbeats",
                 Q_COL2_X, Q_Y + 55, Q_HALF_W, Q_BOX_H, "orange", "semi", idx, size="s", page_id=pid))

    # QUADRANT 3: UNKNOWN KNOWNS
    R.append(box("shape:q3",
                 "Q3 · UNKNOWN KNOWNS (Tacit Enterprise Etiquette)\n"
                 "• Sycophantic Compliance → Mitigated by hard policy forbidding destructive commands\n"
                 "• Outage Tone Dissonance → Mitigated by sentiment classifier enforcing clinical tone\n"
                 "• Over-Apologizing Loops → Mitigated by system prompt invariant forbidding repetitive apologies\n"
                 "• Premature Ticket Close → Mitigated by mandatory customer verification before resolution",
                 Q_COL1_X, Q_Y + 190, Q_HALF_W, Q_BOX_H, "light-violet", "semi", idx, size="s", page_id=pid))

    # QUADRANT 4: UNKNOWN UNKNOWNS
    R.append(box("shape:q4",
                 "Q4 · UNKNOWN UNKNOWNS (Emergent Cascades & Black Swans)\n"
                 "• Cyclic Tool Deadlock → Mitigated by saga compensations explicitly mutating shared state\n"
                 "• Indirect Prompt Injection → Mitigated by sandboxed execution & isolated prompt boundaries\n"
                 "• Sub-Plan Explosion → Mitigated by global execution DAG tree depth ceiling (≤ 2)\n"
                 "• Automation Seduction → Mitigated by forced active friction UI requiring manual inspection",
                 Q_COL2_X, Q_Y + 190, Q_HALF_W, Q_BOX_H, "light-red", "semi", idx, size="s", page_id=pid))

    # ═════════════════════════════════════════════════════════════════
    # 4. ARCHITECTURAL DECISION CHECKPOINTS (y: 1105 to 1235)
    # ═════════════════════════════════════════════════════════════════
    DEC_Y = 1105
    R.append(frame("shape:f_lld_dec",
                   "ARCHITECTURAL DECISION CHECKPOINTS (CONFIRMED FOR GENESIS INITIALIZATION)",
                   START_X, DEC_Y, TOTAL_W, 125, "green", idx, page_id=pid))

    D_CARD_W = (TOTAL_W - 30 - 3 * 20) / 4  # ~627px
    D_CARD_H = 80
    D_CARD_Y = DEC_Y + 32

    dec_cards = [
        ("⚡ ADP-01: Planning Paradigm", "Option C: Hybrid Dual-Process Statechart\n(Deterministic LangGraph FSM + Scoped ReAct)"),
        ("⚡ ADP-02: Workflow Durability", "Option C: Two-Tier Hybrid Substrate\n(Temporal.io Outer Saga + LangGraph Inner Loop)"),
        ("⚡ ADP-03: Context Compaction", "Option C: Tripartite Structured Slot Allocator\n(15% System | 15% Profile | 35% RAG | 25% Chat | 10% Scratch)"),
        ("⚡ ADP-04: Error Recovery", "Option C: Dual-Process Circuit Breaker\n(Max 2 Reflexion trials → Immediate HITL Escalation)"),
    ]

    for i, (title, body) in enumerate(dec_cards):
        dx = START_X + 15 + i * (D_CARD_W + 20)
        R.append(box(f"shape:dec_{i+1}", f"{title}\n✔ {body}", dx, D_CARD_Y, D_CARD_W, D_CARD_H, "green", "semi", idx, size="s", page_id=pid))

    return PAGE_REC, CAMERA_REC, R

def main():
    target = "architecture.tldr"
    if not os.path.exists(target):
        print(f"Error: {target} not found!")
        return

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)

    records = data.get("records", [])
    pid = "page:lld_orchestration"

    filtered_records = [r for r in records if r.get("id") != pid and r.get("parentId") != pid and r.get("id") != f"camera:{pid}"]

    page_rec, camera_rec, lld_shapes = build_abstract_lld_records()

    filtered_records.append(page_rec)
    filtered_records.append(camera_rec)
    filtered_records.extend(lld_shapes)

    data["records"] = filtered_records

    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print(f"Successfully generated Abstract Page 2: '{page_rec['name']}' with {len(lld_shapes)} shapes in {target}.")

if __name__ == "__main__":
    main()
