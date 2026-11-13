"""Tool execution: credentials, isolation, safe retries, breakers and output screening (FR-23 / TA-ADP-04:
TA-D4, TA-D9, TA-D10, TA-D11).

One System per external system: its hosts, how it is credentialed, whether it honours idempotency keys, and
its pool size. Executor.run(call):

1. breaker: after 5 consecutive transient failures within 60 s the system's breaker opens for 30 s and calls
   fail at once (SYSTEM_UNAVAILABLE); one trial call is let through after the cool-down.
2. identity and credentials, resolved here inside the activity: the principal is the one the call's live
   session belongs to (a mismatch is FORBIDDEN, an ended session AUTHENTICATION_EXPIRED); then an
   on-behalf-of token for the system's audience, minted from the session (the downstream enforces the
   user's rights); or, for a system without OBO, a scoped service account and an agent-side Cedar check
   first, with roles from the trusted roles_of() directory: a refusal stops the call before anything goes
   on the network (FORBIDDEN). No bearer token or role list ever travels in the call.
3. the call: tool code gets, through current(), an HTTP client that can only reach the system's own hosts
   (no metadata endpoint, no other host: SSRF), the credential, and an idempotency key.
4. retries: reads, and writes to systems that honour idempotency keys (key = checkpoint_id:tool_call_id,
   the same on every attempt), are retried on timeouts, 429 and 5xx, at most 3 times, with full-jitter
   back-off (uniform 0..min(15 s, 1 s x 2^n)) and within the retry budget. A write without a key is never
   retried. 401 (AUTHENTICATION_EXPIRED), 403 (FORBIDDEN) and other 4xx (REJECTED) never retry.
5. output: screened as tool output before any model sees it (block: withheld; review: free text stripped),
   spotlighted, and stored by reference when the screened text is over 4 KB.
6. audit (when the executor has one): one tools.audit record per call, ok or failed with its code.

Tool failures are ToolFailure(code); through Temporal they become non-retryable ApplicationErrors of that
type.
"""

import contextvars
import ipaddress
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from channels.identity.tiers import StepUpRequired
from core.context.assembler import delimit
from reliability.fallbacks import RetryBudget
from safety.authz.cedar import User
from tools.execution.calls import ToolCall, ToolResult, task_queue
from tools.registry import Risk

MAX_RETRIES, BASE_SECONDS, CAP_SECONDS = 3, 1.0, 15.0
BREAKER_FAILURES, BREAKER_WINDOW, BREAKER_COOLDOWN = 5, 60.0, 30.0
INLINE_LIMIT = 4096
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class System:
    name: str
    hosts: frozenset[str]
    credential: str = "obo"                 # "obo" or "service_account"
    audience: str | None = None             # OBO audience (channels.identity.tiers.AUDIENCES)
    service_secret: str | None = None       # scoped service-account credential, from OpenBao in production
    idempotent_writes: bool = False         # the system honours an Idempotency-Key on writes
    concurrency: int = 20


class ToolFailure(RuntimeError):
    def __init__(self, code, detail="", retryable=False):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.retryable = code, retryable


@dataclass
class Context:
    http: httpx.Client
    credential: str | None
    idempotency_key: str | None
    call: ToolCall


_current = contextvars.ContextVar("tool_context")


def current():
    """Inside a tool function: the HTTP client, credential and idempotency key for this call."""
    return _current.get()


class Breaker:
    """Per system: opens after BREAKER_FAILURES consecutive transient failures within the window."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.failures, self.opened_at, self.trial = clock, [], None, False

    def allow(self, system):
        if self.opened_at is None:
            return
        if self.clock() - self.opened_at < BREAKER_COOLDOWN or self.trial:
            raise ToolFailure("SYSTEM_UNAVAILABLE", f"{system} breaker is open")
        self.trial = True  # half-open: this one call decides

    def success(self):
        self.failures, self.opened_at, self.trial = [], None, False

    def failure(self):
        now = self.clock()
        self.failures = [t for t in self.failures if now - t < BREAKER_WINDOW] + [now]
        if self.trial or len(self.failures) >= BREAKER_FAILURES:
            self.opened_at, self.trial = now, False


def egress(system, transport=None, timeout=10.0):
    """An HTTP client that refuses any host outside the system's allow-list, and every link-local or private
    address literal (cloud metadata, internal ranges), before a packet leaves."""
    def guard(request):
        host = request.url.host
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host not in system.hosts or (address and (address.is_link_local or address.is_private
                                                     or address.is_loopback)):
            raise ToolFailure("EGRESS_BLOCKED", f"{system.name} may not reach {host}")
    return httpx.Client(transport=transport, timeout=timeout, event_hooks={"request": [guard]})


def classify(error):
    if isinstance(error, ToolFailure):
        return error
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 401:
            return ToolFailure("AUTHENTICATION_EXPIRED", "401 from the system")
        if status == 403:
            return ToolFailure("FORBIDDEN", "403 from the system")
        if status in TRANSIENT_STATUS:
            return ToolFailure("TRANSIENT", f"HTTP {status}", retryable=True)
        return ToolFailure("REJECTED", f"HTTP {status}")
    if isinstance(error, httpx.TimeoutException | httpx.TransportError):
        return ToolFailure("TRANSIENT", type(error).__name__, retryable=True)
    return ToolFailure("TOOL_ERROR", type(error).__name__)


def backoff(attempt, rng=random):
    return rng.uniform(0, min(CAP_SECONDS, BASE_SECONDS * 2 ** attempt))


def as_text(data):
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)


class Executor:
    def __init__(self, registry, systems, screener, *, identity=None, authorizer=None, blobs=None,
                 transport=None, sleep=time.sleep, clock=time.monotonic, rng=random, budget=None,
                 roles_of=lambda tenant_id, principal_id: (), audit=None):
        """systems: {name: System}; identity: channels.identity Identity (sessions and OBO); authorizer: Cedar
        Authorizer; blobs: object store for large outputs; transport: httpx transport (tests);
        roles_of(tenant, principal): the principal's roles from a trusted directory. Customers carry no roles
        today (their tokens have none), so the default is none; never take roles from the call.
        audit: tools.audit.AuditLog for direct calls (the specialists', register S11): every call is recorded,
        whatever its outcome, and a call whose record cannot be written returns nothing (AUDIT_UNAVAILABLE).
        Leave it None under tools.sagas, whose activities audit each step themselves."""
        self.registry, self.systems, self.screener, self.roles_of = registry, systems, screener, roles_of
        self.identity, self.authorizer, self.blobs, self.transport = identity, authorizer, blobs, transport
        self.sleep, self.rng, self.budget, self.audit = sleep, rng, budget or RetryBudget(), audit
        self.breakers = {name: Breaker(clock) for name in systems}

    def run(self, call):
        if self.audit is None:
            return self._run(call)
        try:
            result = self._run(call)
        except Exception as error:  # noqa: BLE001 - audited, then raised as it was
            self._audit(call, getattr(error, "code", None) or type(error).__name__)
            raise
        self._audit(call, None)
        return result

    def _audit(self, call, error_code):
        tool = self.registry.tools.get(call.tool_id)
        system = self.systems.get(call.tool_id.split(".", 1)[0])
        try:
            # ponytail: the case stands in for the conversation (one per case until case linking, L36)
            self.audit.record(tenant_id=call.tenant_id, conversation_id=call.case_id,
                              user_id=call.principal_id, tool_id=call.tool_id, action="forward",
                              outcome="failed" if error_code else "ok",
                              case_id=call.case_id, call_id=call.tool_call_id,
                              credential_basis=system and system.credential,
                              risk_class=tool and tool.risk.value, error_code=error_code,
                              personal=call.arguments or None)
        except Exception as error:  # noqa: BLE001 - no record, no output
            raise ToolFailure("AUDIT_UNAVAILABLE", type(error).__name__) from error

    def _principal(self, call):
        """The call's principal, verified against the live session it names (never taken on trust)."""
        if not call.session_id:
            raise ToolFailure("AUTHENTICATION_EXPIRED", "the call names no session")
        try:
            principal_id, _ = self.identity.session_principal(call.tenant_id, call.session_id)
        except Exception as error:  # ended, expired or another tenant's session
            raise ToolFailure("AUTHENTICATION_EXPIRED", type(error).__name__) from error
        if principal_id != call.principal_id:
            raise ToolFailure("FORBIDDEN", "the session belongs to someone else")
        return principal_id

    def _credential(self, call, system):
        principal_id = self._principal(call)
        if system.credential == "obo":
            try:
                return self.identity.exchange_for_session(call.tenant_id, call.session_id, system.audience)
            except StepUpRequired as error:
                raise ToolFailure("STEP_UP_REQUIRED", str(error)) from error
            except Exception as error:  # the session ended between the two checks
                raise ToolFailure("AUTHENTICATION_EXPIRED", type(error).__name__) from error
        resource = call.resource
        user = User(call.tenant_id, principal_id, tuple(self.roles_of(call.tenant_id, principal_id)))
        verdict = self.authorizer.check(user, resource.get("action", ""), resource.get("type", ""),
                                        resource.get("id", ""), resource.get("attrs", {}))
        if verdict != "allow":
            raise ToolFailure("FORBIDDEN", f"agent-side check said {verdict}")
        return system.service_secret

    def _run(self, call):
        tool = self.registry.tools[call.tool_id]
        system = self.systems[call.tool_id.split(".", 1)[0]]
        breaker = self.breakers[system.name]
        breaker.allow(system.name)
        credential = self._credential(call, system)
        arguments = tool.args(**call.arguments)
        is_read = tool.risk is Risk.READ_ONLY
        keyed = not is_read and system.idempotent_writes
        key = f"{call.checkpoint_id}:{call.tool_call_id}" if keyed else None
        retryable_call = is_read or key is not None
        attempt = 0
        with egress(system, self.transport) as http:
            while True:
                attempt += 1
                token = _current.set(Context(http, credential, key, call))
                try:
                    output = tool.func(arguments)
                    breaker.success()
                    self.budget.record(system.name, attempt > 1)
                    return self._screen(call, output, attempt)
                except Exception as error:  # noqa: BLE001 - every failure is classified below
                    failure = classify(error)
                    if failure.code == "TRANSIENT":
                        breaker.failure()
                    self.budget.record(system.name, attempt > 1)
                    if not (failure.retryable and retryable_call and attempt <= MAX_RETRIES
                            and self.budget.allows_retry(system.name)):
                        raise failure from error
                    breaker.allow(system.name)
                    self.sleep(backoff(attempt - 1, self.rng))
                finally:
                    _current.reset(token)

    def _screen(self, call, output, attempts):
        verdict = self.screener.screen("tool_output", output, call.tool_id)
        data = output if (verdict.band == "pass" and isinstance(output, dict)) else {}
        content, blob_ref = verdict.content, None
        if len(content.encode()) > INLINE_LIMIT and self.blobs is not None:
            blob_ref = f"tool-output/{call.tenant_id}/{call.case_id}/{call.tool_call_id}.txt"
            self.blobs.put(blob_ref, content.encode())
            content = delimit(f"[{len(content.encode())} bytes stored as {blob_ref}; read it only if needed]",
                              f"{call.tool_id} output reference")
        return ToolResult(call.tool_call_id, call.tool_id, data, content, verdict.band, attempts, blob_ref)


class ToolActivities:
    def __init__(self, executor):
        self.executor = executor

    @activity.defn(name="run_tool")
    def run_tool(self, call: ToolCall) -> ToolResult:
        try:
            return self.executor.run(call)
        except ToolFailure as failure:
            raise ApplicationError(str(failure), type=failure.code, non_retryable=True) from failure


def workers(client, activities, systems):
    """One worker per system on its own task queue, sized by the system's concurrency (bulkheads)."""
    return [Worker(client, task_queue=task_queue(s.name), activities=[activities.run_tool],
                   activity_executor=ThreadPoolExecutor(max_workers=s.concurrency),
                   max_concurrent_activities=s.concurrency) for s in systems.values()]
