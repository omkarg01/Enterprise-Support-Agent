"""The `run_turn` activity, safe to retry (ADP-02, MS-ADP-04), in two forms:

- LoopActivities(loop): the live one. A customer turn (the workflow's `turn` signal) runs through the whole
  turn loop, runtime.TurnLoop.run: screening, the statechart, the answer and its events. TurnLoop.run is
  itself safe to retry (a turn that ended returns at once; an interrupted statechart run resumes).
- TurnActivities(hooks, store): the statechart alone with its decision hooks, for the workflow's own tests.
  A retry after a crash carries on from the case's last saved stage instead of starting the turn again, and
  a retry of a turn that already finished (its result was lost) applies nothing twice: each turn's event id
  is stored in the case state.

The outcome's checkpoint_id is the case state's version.
"""

from concurrent.futures import ThreadPoolExecutor

from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from channels.api.envelope import InboundTurn
from core.orchestrator.statechart import build, thread
from workflows.ticket import TASK_QUEUE, TicketWorkflow, TurnOutcome, TurnRequest


class LoopActivities:
    def __init__(self, loop):
        self.loop = loop  # runtime.TurnLoop

    @activity.defn(name="run_turn")
    def run_turn(self, request: TurnRequest) -> TurnOutcome:
        self.loop.run(InboundTurn.model_validate(request.event["turn"]))
        state, version = self.loop.graph.store.load(request.tenant_id, request.case_id)
        return TurnOutcome(stage=state.get("stage"), checkpoint_id=str(version))


class TurnActivities:
    def __init__(self, hooks, store, **build_options):
        self.graph = build(hooks, store, **build_options)

    @activity.defn(name="run_turn")
    def run_turn(self, request: TurnRequest) -> TurnOutcome:
        config = thread(request.tenant_id, request.case_id)
        state = self.graph.get_state(config)
        seen = (state.get("event") or {}).get("id")
        if state.get("pending") and seen != request.event_id:
            raise ApplicationError(f"case {request.case_id} has an unfinished turn from another run",
                                   non_retryable=True)
        if state.get("pending"):  # an earlier attempt of this turn crashed: carry on from its last stage
            self.graph.invoke(None, config)
        elif seen != request.event_id:
            self.graph.invoke({"tenant_id": request.tenant_id, "case_id": request.case_id,
                               "session_id": request.session_id,
                               "event": {**request.event, "id": request.event_id}}, config)
        state, version = self.graph.store.load(request.tenant_id, request.case_id)
        return TurnOutcome(stage=state.get("stage"), checkpoint_id=str(version))


# Turns run at once in this process. Each holds a main-pool connection only for its short reads and writes
# between model calls; see the connection budget in channels.api.app.
ACTIVITIES = 4


def worker(client, activities, task_queue=TASK_QUEUE):
    return Worker(client, task_queue=task_queue, workflows=[TicketWorkflow], activities=[activities.run_turn],
                  activity_executor=ThreadPoolExecutor(max_workers=ACTIVITIES),
                  max_concurrent_activities=ACTIVITIES)
