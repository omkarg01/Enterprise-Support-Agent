"""System 2: the scoped ReAct policy inside the DELIBERATING stage (ADP-01 Pillar D, ADP-04).

Each graph cycle runs one step, so every step is checkpointed. Bounds: at most 4 steps, at most 2 reflexion
critiques, read-only tools only. A step that needs a change hands off to an SOP with a typed payload; anything
past the bounds goes to a human.
"""

from schemas.thread_state import Stage

MAX_STEPS = 4
MAX_REFLEXION = 2


def after_step(state, step, read_only_tools):
    """State updates for one step, including next_stage. `state` counters are this episode's so far."""
    iterations = state.get("iterations", 0) + 1
    trials = state.get("reflexion_trials", 0) + (step.outcome == "critique")
    update = {"trajectory": [step.model_dump()], "iterations": iterations, "reflexion_trials": trials}
    if step.action and step.action not in read_only_tools:
        return {**update, "next_stage": Stage.ESCALATED_HITL.value,
                "error": f"diagnosis may only use read-only tools; {step.action!r} needs an SOP"}
    if step.outcome == "answer" and not step.answer:  # nothing to tell the customer: a person, not "resolved"
        return {**update, "next_stage": Stage.ESCALATED_HITL.value, "error": step.thought}
    if step.outcome == "answer":
        return {**update, "next_stage": Stage.RESOLVED.value, "final_response": step.answer}
    if step.outcome == "needs_sop":
        return {**update, "next_stage": Stage.EXECUTING_SOP.value, "sop_name": step.sop_name,
                "parameters": step.parameters}
    if trials > MAX_REFLEXION or iterations >= MAX_STEPS:
        return {**update, "next_stage": Stage.ESCALATED_HITL.value,
                "error": f"diagnosis stopped after {iterations} steps and {trials} critiques"}
    return {**update, "next_stage": Stage.DELIBERATING.value}
