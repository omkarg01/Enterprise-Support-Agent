"""Idempotent turn submission (UA-ADP-01 UA-D2) and typed event publishing (UA-ADP-04 UA-D7).

A turn is keyed by (tenant, principal, conversation, Idempotency-Key): a retried POST finds the existing turn
instead of creating a second one. The message text is not stored here; it goes to the orchestrator through
`dispatch` inside the same transaction, so a failed hand-off leaves no turn behind and the client can retry.

The table's SQL lives here (module-owned schema, DECISION-d4b79fe1) and is applied with apply_schema().
"""

from channels.api.envelope import EventType, InboundTurn
from data.events import EventLog
from data.migrate import psql

SCHEMA_NAME = "channels_0001_turns.sql"
SCHEMA = """
CREATE TABLE turns (
  id              text PRIMARY KEY DEFAULT gen_random_uuid()::text,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  principal_id    text NOT NULL,
  conversation_id text NOT NULL,
  idempotency_key text NOT NULL,
  status          text NOT NULL DEFAULT 'QUEUED',
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, principal_id, conversation_id, idempotency_key)
);

ALTER TABLE turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE turns FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON turns
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE turns OWNER TO app_owner;
GRANT SELECT, INSERT ON turns TO app_runtime;
"""


def apply_schema(database):
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")


def submit_turn(conn, principal, conversation_id, idempotency_key, request, dispatch):
    """Return (turn_id, created). A new turn gets a 'status' event and goes to dispatch, all or nothing."""
    with conn.transaction():
        _context(conn, principal.tenant_id)
        key = (principal.tenant_id, principal.principal_id, conversation_id, idempotency_key)
        row = conn.execute(
            "INSERT INTO turns (tenant_id, principal_id, conversation_id, idempotency_key) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id", key).fetchone()
        if row is None:
            (turn_id,) = conn.execute(
                "SELECT id FROM turns WHERE tenant_id = %s AND principal_id = %s AND conversation_id = %s "
                "AND idempotency_key = %s", key).fetchone()
            return turn_id, False
        (turn_id,) = row
        publish(EventLog(conn), principal.tenant_id, conversation_id, EventType.STATUS,
                {"turn_id": turn_id, "state": "queued", "text": "Message received"})
        dispatch(InboundTurn(
            tenant_id=principal.tenant_id, principal_id=principal.principal_id,
            assurance_tier=principal.assurance_tier, session_id=principal.session_id,
            conversation_id=conversation_id, turn_id=turn_id,
            content=request.content, attachment_refs=request.attachment_refs))
    return turn_id, True


def publish(log, tenant_id, conversation_id, event_type, data):
    """The only way components should write client events: the type must be one of the contract's types."""
    return log.append(tenant_id, conversation_id, EventType(event_type).value, data)


def events_after(conn, tenant_id, conversation_id, after_id):
    """Events of the conversation with id > after_id, as (id, type, data)."""
    with conn.transaction():
        _context(conn, tenant_id)
        rows = conn.execute(
            "SELECT id, event_type, data FROM outbound_event_log WHERE conversation_id = %s AND id > %s "
            "ORDER BY id", (conversation_id, after_id)).fetchall()
    return [(str(event_id), event_type, data) for event_id, event_type, data in rows]


def last_event_id(conn, tenant_id, conversation_id):
    with conn.transaction():
        _context(conn, tenant_id)
        (latest,) = conn.execute("SELECT coalesce(max(id), 0) FROM outbound_event_log "
                                 "WHERE conversation_id = %s", (conversation_id,)).fetchone()
    return latest


def _context(conn, tenant_id):
    conn.execute("SET LOCAL ROLE app_runtime")
    conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))
