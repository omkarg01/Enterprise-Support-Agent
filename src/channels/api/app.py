"""Web API: POST 202 turn submission and the resumable SSE event stream (FR-6, FR-9; UA-ADP-01, UA-ADP-04).

    POST /v1/conversations/{id}/turns    Idempotency-Key header, {"content": ...} -> 202 {"turn_id", "status"}
    GET  /v1/conversations/{id}/events   text/event-stream; Last-Event-ID replays what the client missed
    GET  /v1/conversations/{id}/decisions[?turn_id=]   the customer's "why" view (FR-34)
    GET  /v1/conversations/{id}/turns/{turn_id}/trace  "how this was answered", filtered by assurance tier
    POST /v1/conversations/{id}/tickets/{TKT-n}/confirm   the Raise ticket button: a turn carrying the action
    GET  /v1/tickets, GET /v1/tickets/{TKT-n}           the signed-in customer's own tickets (FR-47)
    POST /v1/conversations/{id}/actions/{ACT-n}/confirm   a service action card's button (tools.services)
    GET  /v1/services                                     the Services tab: own servers, apps and activity
    GET  /v1/conversations                                the History list: own conversations, newest first
    POST /v1/conversations/{id}/turns/{turn_id}/feedback  {"signal": "up"|"down", "comment"}: a reply's thumb,
                                                          final (409 on a second vote)
    GET  /v1/conversations/{id}/feedback                  the caller's thumbs: {"votes": {turn_id: signal}}

Sign-in, the orchestrator hand-off and transcript loading are passed to create_app() so later tasks plug in
without editing this module. Until sign-in exists (M1-02), dev_header_auth trusts X-Tenant-ID / X-User-ID
headers, and only when SUPPORT_AGENT_DEV_AUTH=1; otherwise every request is 401.
"""

import asyncio
import contextlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import anyio.to_thread
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from channels import sessions
from channels.api import trace
from channels.api.envelope import EventType, FeedbackRequest, Principal, TurnAction, TurnRequest
from channels.api.turns import events_after, last_event_id, submit_turn
from data import pool as pools
from data.events import EventLog, listener, rebuild_from_transcript
from data.keys.envelope import TENANT_ID
from hitl.tickets import Tickets, number_id
from improvement.feedback import AlreadyRated
from safety.governance.records import DecisionLog
from tools import services

SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TICKET = re.compile(r"^TKT-[0-9]{5,}$")
ACTION = re.compile(r"^ACT-[0-9]{5,}$")
# Connection budget of one API process (data.pool): DB_POOL_MAX 8 + DB_STORE_POOL_MAX 2 + 1 LISTEN = 11 <= 12,
# enforced by the pools whatever the thread counts. The threads below only bound how many callers can queue
# for the 8 main connections: WORK 16 (async routes' DB work and sign-in checks) + SYNC_THREADS 8 (FastAPI's
# sync routes: staff, identity, /healthz) + workflows.activities.ACTIVITIES 4 (turns) = 28 callers at most,
# each holding a connection for a short query; one that waits 10 s gets PoolTimeout instead of piling up.
# An open stream holds no thread and no connection while it waits (B12).
WORK = ThreadPoolExecutor(max_workers=16)
SYNC_THREADS = 8


def dev_header_auth(request):
    if os.environ.get("SUPPORT_AGENT_DEV_AUTH") != "1":
        raise HTTPException(401, "sign-in is not configured")
    tenant_id, user_id = request.headers.get("X-Tenant-ID", ""), request.headers.get("X-User-ID", "")
    if not TENANT_ID.match(tenant_id) or not SAFE_ID.match(user_id):
        raise HTTPException(401, "X-Tenant-ID and X-User-ID headers are required")
    return Principal(tenant_id=tenant_id, principal_id=user_id)


def no_orchestrator(turn):
    """Default hand-off until the orchestrator exists: the turn is accepted and recorded, nothing runs it."""


def database_url():
    """APP_DATABASE_URL is the non-bypass login role (register S17); DATABASE_URL stays the owner for
    migrations. Local tests keep using DATABASE_URL (the compose superuser) when the app URL is unset."""
    return os.environ.get("APP_DATABASE_URL") or os.environ.get("DATABASE_URL") or (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'agent')}:{os.environ['POSTGRES_PASSWORD']}"
        f"@127.0.0.1:5432/{os.environ.get('POSTGRES_DB', 'agent')}")


def sse(event_id, event_type, data):
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event_type}\ndata: {json.dumps(data)}\n\n"


def create_app(dsn=None, authenticate=dev_header_auth, dispatch=no_orchestrator, transcript=None,
               poll_seconds=0.05, heartbeat_seconds=15.0, stream_seconds=None, feedback=None):
    """feedback: an improvement.feedback.Feedback for the thumbs routes (they answer 503 without one).
    poll_seconds: how often an open stream reads the log when no notification woke it, the fallback for one
    lost while the LISTEN connection reconnects (data.events.Listener)."""
    @contextlib.asynccontextmanager
    async def lifespan(app):
        anyio.to_thread.current_default_thread_limiter().total_tokens = SYNC_THREADS  # FastAPI's sync routes
        yield

    app = FastAPI(title="Support Agent API", lifespan=lifespan)
    dsn = dsn or database_url()
    connect = pools.pool(dsn).connection
    notifications = listener(dsn)

    def checked(conversation_id):
        if not SAFE_ID.match(conversation_id):
            raise HTTPException(422, "invalid conversation id")
        return conversation_id

    async def in_pool(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(WORK, fn, *args)

    async def authenticated(request):
        """authenticate() reads and touches the session in the database: off the event loop."""
        return await in_pool(authenticate, request)

    @app.post("/v1/conversations/{conversation_id}/turns", status_code=202)
    async def submit(conversation_id: str, body: TurnRequest, request: Request,
                     idempotency_key: str | None = Header(default=None)):
        principal = await authenticated(request)
        if not idempotency_key or not SAFE_ID.match(idempotency_key):
            raise HTTPException(400, "an Idempotency-Key header (1-128 safe characters) is required")
        conversation_id = checked(conversation_id)

        def go():
            with connect() as conn:
                return submit_turn(conn, principal, conversation_id, idempotency_key, body, dispatch)

        turn_id, created = await in_pool(go)
        return {"turn_id": turn_id, "status": "QUEUED" if created else "ALREADY_QUEUED"}

    async def signed_in(request):
        principal = await authenticated(request)
        if principal.assurance_tier == "T0_ANON":
            raise HTTPException(403, "sign in to raise and see tickets")
        return principal

    @app.post("/v1/conversations/{conversation_id}/tickets/{proposal}/confirm", status_code=202)
    async def confirm(conversation_id: str, proposal: str, request: Request,
                      idempotency_key: str | None = Header(default=None)):
        principal = await signed_in(request)
        if not idempotency_key or not SAFE_ID.match(idempotency_key):
            raise HTTPException(400, "an Idempotency-Key header (1-128 safe characters) is required")
        conversation_id = checked(conversation_id)
        if not TICKET.match(proposal):
            raise HTTPException(422, "invalid ticket number")
        body, action = TurnRequest(content=f"Raise ticket {proposal}"), TurnAction(type="confirm_ticket",
                                                                                  proposal=proposal)

        def go():
            with connect() as conn:
                return submit_turn(conn, principal, conversation_id, idempotency_key, body, dispatch, action)

        turn_id, created = await in_pool(go)
        return {"turn_id": turn_id, "status": "QUEUED" if created else "ALREADY_QUEUED"}

    @app.post("/v1/conversations/{conversation_id}/actions/{proposal}/confirm", status_code=202)
    async def confirm_action(conversation_id: str, proposal: str, request: Request,
                             idempotency_key: str | None = Header(default=None)):
        principal = await signed_in(request)
        if not idempotency_key or not SAFE_ID.match(idempotency_key):
            raise HTTPException(400, "an Idempotency-Key header (1-128 safe characters) is required")
        conversation_id = checked(conversation_id)
        if not ACTION.match(proposal):
            raise HTTPException(422, "invalid action number")
        body = TurnRequest(content=f"Confirm {proposal}")
        action = TurnAction(type="confirm_action", proposal=proposal)

        def go():
            with connect() as conn:
                return submit_turn(conn, principal, conversation_id, idempotency_key, body, dispatch, action)

        turn_id, created = await in_pool(go)
        return {"turn_id": turn_id, "status": "QUEUED" if created else "ALREADY_QUEUED"}

    @app.get("/v1/services")
    async def my_services(request: Request):
        """The Services tab: the signed-in customer's servers and apps (settled status) and what the agent
        did on them."""
        principal = await signed_in(request)
        own = await in_pool(services.services, connect, principal.tenant_id, principal.principal_id)
        done = await in_pool(services.activity, connect, principal.tenant_id, principal.principal_id)
        return {"services": own, "activity": done}

    @app.get("/v1/conversations")
    async def history(request: Request):
        principal = await authenticated(request)
        def find():
            with connect() as conn:
                return sessions.history(conn, principal.tenant_id, principal.principal_id)
        return {"conversations": await in_pool(find)}

    @app.get("/v1/tickets")
    async def my_tickets(request: Request):
        principal = await signed_in(request)
        rows = await in_pool(Tickets(connect).list_for, principal.tenant_id, principal.principal_id)
        return {"tickets": [ticket.card() for ticket in rows]}

    @app.get("/v1/tickets/{number}")
    async def my_ticket(number: str, request: Request):
        principal = await signed_in(request)
        ticket_id = number_id(number) if TICKET.match(number) else None
        ticket = ticket_id and await in_pool(Tickets(connect).get, principal.tenant_id,
                                             principal.principal_id, ticket_id)
        if not ticket:
            raise HTTPException(404, "no such ticket")
        return {**ticket.card(), "description": ticket.description, "reason": ticket.reason,
                "conversation_id": ticket.conversation_id, "updated_at": ticket.updated_at.isoformat(),
                "opened_at": ticket.opened_at and ticket.opened_at.isoformat(), "events": list(ticket.events)}

    @app.get("/v1/conversations/{conversation_id}/decisions")
    async def decisions(conversation_id: str, request: Request, turn_id: str | None = None):
        principal = await authenticated(request)
        conversation_id = checked(conversation_id)
        if turn_id is not None and not SAFE_ID.match(turn_id):
            raise HTTPException(422, "invalid turn id")
        return await in_pool(DecisionLog(connect).why, principal.tenant_id, conversation_id, turn_id)

    @app.get("/v1/conversations/{conversation_id}/turns/{turn_id}/trace")
    async def turn_trace(conversation_id: str, turn_id: str, request: Request):
        # with sign-in, this also checks the conversation is the caller's
        principal = await authenticated(request)
        conversation_id = checked(conversation_id)
        if not SAFE_ID.match(turn_id):
            raise HTTPException(422, "invalid turn id")

        def find():
            with connect() as conn:
                return trace.load(conn, principal.tenant_id, conversation_id, turn_id)

        row = await in_pool(find)
        if row is None:
            raise HTTPException(404, "no trace for this turn yet")
        decision, steps, created_at = row
        return trace.customer_trace(turn_id, decision, steps, created_at, principal.assurance_tier)

    @app.post("/v1/conversations/{conversation_id}/turns/{turn_id}/feedback", status_code=201)
    async def rate(conversation_id: str, turn_id: str, body: FeedbackRequest, request: Request):
        # with sign-in, this also checks the conversation is the caller's
        principal = await authenticated(request)
        conversation_id = checked(conversation_id)
        if not SAFE_ID.match(turn_id):
            raise HTTPException(422, "invalid turn id")
        if feedback is None:
            raise HTTPException(503, "feedback is not configured")

        def go():
            with connect() as conn:
                said = EventLog(conn).transcript(principal.tenant_id, conversation_id)
            if not any(m["role"] == "assistant" and m.get("turn_id") == turn_id for m in said):
                raise HTTPException(404, "no reply with that turn id in this conversation")
            try:
                return feedback.thumbs(principal.tenant_id, principal.principal_id, conversation_id, turn_id,
                                       body.signal == "up", body.comment)
            except AlreadyRated:
                raise HTTPException(409, "this reply is already rated") from None

        _, _, dropped = await in_pool(go)
        given = bool(body.comment and body.comment.strip())
        return {"signal": body.signal, "comment": "dropped" if dropped else "kept" if given else "none"}

    @app.get("/v1/conversations/{conversation_id}/feedback")
    async def my_votes(conversation_id: str, request: Request):
        principal = await authenticated(request)
        conversation_id = checked(conversation_id)
        if feedback is None:
            return {"votes": {}}
        return {"votes": await in_pool(feedback.votes, principal.tenant_id, principal.principal_id,
                                       conversation_id)}

    @app.get("/v1/conversations/{conversation_id}/events")
    async def events(conversation_id: str, request: Request, last_event_id_header: str | None = Header(
            default=None, alias="Last-Event-ID")):
        principal = await authenticated(request)
        conversation_id = checked(conversation_id)
        cursor = None
        if last_event_id_header is None:
            def snapshot():
                with connect() as conn:  # on connect, not when the generator first runs (B13)
                    return last_event_id(conn, principal.tenant_id, conversation_id)
            cursor = await in_pool(snapshot)
        return StreamingResponse(_stream(principal.tenant_id, conversation_id, last_event_id_header, cursor),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def _stream(tenant_id, conversation_id, last_seen, cursor):
        """Async so an open stream does not occupy a worker thread (B12). DB work is short and sync."""
        def replay():
            with connect() as conn:
                log = EventLog(conn)
                missed = log.replay(tenant_id, conversation_id, last_seen)
                earlier = None if missed is not None else (
                    transcript or log.transcript)(tenant_id, conversation_id)
                return missed, earlier, last_event_id(conn, tenant_id, conversation_id)

        if last_seen is not None:
            missed, earlier, latest = await in_pool(replay)
            if missed is not None:
                for event in missed:
                    yield sse(*event)
                cursor = int(missed[-1][0]) if missed else int(last_seen)
            else:
                yield sse(None, EventType.STATUS.value, {"code": "stream_expired"})
                for _, _, message in rebuild_from_transcript(earlier)[1:]:
                    yield sse(None, EventType.FINAL.value, {"history": True, **message})
                cursor = latest
        def poll():
            with connect() as conn:
                return events_after(conn, tenant_id, conversation_id, cursor)

        # Read once now (catch-up), then again only when a notification for this conversation wakes the stream
        # or poll_seconds pass without one. The wake-up is cleared before each read, so an event appended
        # during a read wakes the next wait at once.
        woken = asyncio.Event()
        notifications.subscribe(conversation_id, woken, asyncio.get_running_loop())
        try:
            deadline = None if stream_seconds is None else time.monotonic() + stream_seconds
            beat = time.monotonic()
            while deadline is None or time.monotonic() < deadline:
                woken.clear()
                batch = await in_pool(poll)
                for event in batch:
                    yield sse(*event)
                    cursor, beat = int(event[0]), time.monotonic()
                if time.monotonic() - beat >= heartbeat_seconds:
                    yield ": keep-alive\n\n"
                    beat = time.monotonic()
                wait = min(poll_seconds, max(0.01, beat + heartbeat_seconds - time.monotonic()))
                if deadline is not None:
                    wait = min(wait, max(0.01, deadline - time.monotonic()))
                try:
                    await asyncio.wait_for(woken.wait(), wait)
                except TimeoutError:
                    pass
        finally:
            notifications.unsubscribe(conversation_id, woken)

    return app
