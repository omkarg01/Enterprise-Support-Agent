"""The customer's "How this was answered" view of one turn (FR-34 transparency; SG-ADP-05 SG-D13).

The turn loop records the steps it took in decision_records.trace["steps"] (masked text only).
customer_trace() turns them into a small agent-shaped narrative: Coordinator (screening, PII masking,
triage), Knowledge Agent (retrieval, the drafted answer) and Output Reviewer (the output checks). The names
mirror the coordinator topology so the view keeps its shape once that is wired; today they label stages of
the one turn loop.

Redaction happens here, on the server, by assurance tier: an anonymous customer (T0) gets the summary and the
agent outline only; a signed-in one (T1 / T2) also gets tool calls, their arguments and the shared state. No
tier ever gets scores, confidences, thresholds, model ids or the screening band (UU3: probing the view to map
cut-offs), and the forensic trace (Jev answers, classifier scores) is never selected.
"""

SUMMARY_ONLY = "T0_ANON"
SUMMARIES = {
    "answer": "Answered from our documentation after the output checks passed.",
    "ask_customer": "Asked one clarifying question before answering.",
    "escalate": "Handed to a specialist, who will continue here.",
    "block": "Stopped at the safety check; nothing else saw the message.",
}
FINAL = {"answer": "composed", "ask_customer": "clarifying question", "escalate": "handed to a person",
         "block": "safety notice"}


def _words(name):
    return str(name or "").replace("_", " ").capitalize()


def _call(name, result, args=None, items=None):
    return {"name": name, "args": args or {}, "result": result, "items": items or []}


def _agents(decision, steps):
    retrieval, answer = steps.get("retrieval"), steps.get("answer")
    screening = "BLOCKED" if steps.get("screening") == "block" else "PASS"   # never "review": a cut-off probe
    calls = [_call("Safety screening", screening)]
    if decision != "block":
        calls.append(_call("Personal data masking", "MASKED" if steps.get("masked") else "NONE FOUND"))
        if steps.get("intent"):
            by = "decision engine" if steps.get("classifier") == "jev" else "classifier"
            calls.append(_call("Triage", "SUCCESS", {"intent": _words(steps["intent"]), "by": by}))
    stages = " → ".join(_words(stage) for stage in steps.get("stages", []))
    specialists = steps.get("specialists", [])
    routed = ", ".join(f"the {_words(s['role']).lower()} specialist" for s in specialists) \
        or "the Knowledge Agent"
    agents = [{"id": "coordinator", "name": "Coordinator", "role": "Screens the message and routes it",
               "status": "stopped" if decision == "block" else "success",
               "outcome": {"block": "stopped at screening", "escalate": "routed to a person"}.get(
                   decision, f"routed to {routed}"),
               "calls": calls, "notes": [f"Stages: {stages}"] if stages else []}]
    if retrieval or answer:
        sources = (retrieval or {}).get("sources", [])
        calls = []
        if retrieval:
            calls.append(_call(
                "Retrieval lookup", "FAILED" if retrieval.get("failed") else "SUCCESS",
                {"index": ", ".join(retrieval.get("collections") or []) or "knowledge base",
                 "query": str(retrieval.get("query", ""))[:160]},
                [source["title"] for source in sources]))
        answered = (answer or {}).get("outcome") == "answered"
        if answer:
            calls.append(_call("Draft answer", "SUCCESS" if answered else "NO ANSWER"))
        found = f"{len(sources)} source{'s' * (len(sources) != 1)}" if sources else "no matching source"
        agents.append({"id": "knowledge", "name": "Knowledge Agent",
                       "role": "Finds documentation passages and drafts the answer",
                       "status": "success" if answered else "failed", "outcome": f"retrieved {found}",
                       "calls": calls,
                       "notes": ["Retried once after the first draft did not pass the checks"]
                       if (answer or {}).get("retried") else []})
    for spec in specialists:
        found = spec.get("claims", 0)
        agents.append({"id": spec["role"], "name": f"{_words(spec['role'])} specialist",
                       "role": "Reads the passages for its domain and reports findings",
                       "status": "success" if found else "failed",
                       "outcome": f"{found} finding{'s' * (found != 1)}" if found else "no findings",
                       "calls": [_call("Read passages", "SUCCESS" if found else "NO FINDINGS")],
                       "notes": []})
    if "review" in steps:
        passed = steps["review"] == "deliver"
        agents.append({"id": "reviewer", "name": "Output Reviewer",
                       "role": "Checks the reply before it is sent",
                       "status": "success" if passed else "failed",
                       "outcome": f"verdict {'PASS' if passed else 'HELD'}",
                       "calls": [_call("Output checks", "PASS" if passed else "HELD")],
                       "notes": ["Safety, promises and grounding checked"]})
    return agents


def customer_trace(turn_id, decision, steps, requested_at, tier):
    """The tier-filtered view; `steps` is the turn loop's record (an older record without one gives the
    summary and outline only)."""
    full = tier != SUMMARY_ONLY
    agents = _agents(decision, steps or {})
    retrieval, answer = (steps or {}).get("retrieval") or {}, (steps or {}).get("answer") or {}
    if not full:
        agents = [{**agent, "calls": [], "notes": []} for agent in agents]
    shared = {"user_query": str(retrieval.get("query", ""))[:160] or None,
              "intent": _words(steps.get("intent")) if steps and steps.get("intent") else None,
              "sources": len(retrieval.get("sources", [])), "retry_count": int(bool(answer.get("retried"))),
              "final_answer": FINAL.get(decision, decision), "requested_at": requested_at.isoformat()}
    return {"turn_id": turn_id, "detail": "full" if full else "summary", "tier": tier,
            "summary": SUMMARIES.get(decision, "Answered."),
            "final": {"label": "Final answer", "outcome": FINAL.get(decision, decision)},
            "agents": agents, "shared_state": shared if full else {}}


def load(conn, tenant_id, conversation_id, turn_id):
    """(decision, steps, created_at) of the turn's latest decision record, or None while it is running."""
    with conn.transaction():
        conn.execute("SET LOCAL ROLE app_runtime")
        conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))
        return conn.execute(
            "SELECT decision, trace -> 'steps', created_at FROM decision_records "
            "WHERE conversation_id = %s AND turn_id = %s ORDER BY id DESC LIMIT 1",
            (conversation_id, turn_id)).fetchone()
