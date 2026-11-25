"""channels.api's dispatch hook on Temporal (FR-2 / ADP-02; DECISION-bc6301b4).

TemporalDispatcher(address, loop) keeps a background event loop with the Temporal client and, in this process,
the worker that runs the turns (workflows.activities.LoopActivities over runtime.TurnLoop). dispatch(turn)
signal-with-starts the case's ticket workflow, ticket:<tenant>:<conversation>, with the turn: a running
workflow gets the signal, a finished one starts again. It raises when Temporal does not answer in time, so
the API keeps no turn and the client can retry with the same idempotency key.
"""

import asyncio
import threading

from temporalio.client import Client

from workflows.activities import LoopActivities, worker
from workflows.ticket import TASK_QUEUE, TicketStart, TicketWorkflow, workflow_id

STARTUP_SECONDS = 60  # connecting and building the worker once; each turn keeps `timeout`


class TemporalDispatcher:
    def __init__(self, address, loop, task_queue=TASK_QUEUE, timeout=10):
        self.task_queue, self.timeout = task_queue, timeout
        self.events = asyncio.new_event_loop()
        threading.Thread(target=self.events.run_forever, name="temporal", daemon=True).start()
        # building the worker validates the workflows in Temporal's sandbox: seconds, more on a busy laptop
        self._call(self._start(address, loop), STARTUP_SECONDS)

    async def _start(self, address, loop):
        self.client = await Client.connect(address)
        self.worker = worker(self.client, LoopActivities(loop), self.task_queue)
        self.running = asyncio.ensure_future(self.worker.run())

    def _call(self, coroutine, timeout=None):
        return asyncio.run_coroutine_threadsafe(coroutine, self.events).result(timeout or self.timeout)

    def __call__(self, turn):
        start = TicketStart(turn.tenant_id, turn.conversation_id, turn.session_id or turn.principal_id)
        return self._call(self.client.start_workflow(
            TicketWorkflow.run, start, id=workflow_id(turn.tenant_id, turn.conversation_id),
            task_queue=self.task_queue, start_signal="turn",
            start_signal_args=[turn.model_dump(mode="json")]))

    def close(self):
        self._call(self.worker.shutdown())
        self.events.call_soon_threadsafe(self.events.stop)
