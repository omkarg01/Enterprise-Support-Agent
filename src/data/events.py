"""Outbound event log for SSE streaming with a 7-day replay window (NFR-4, DP-ADP-04: DP-D6, DP-D7).

Events are written directly (no event bus). A client reconnecting with Last-Event-ID gets every later
event of the conversation while that event is inside the window; otherwise replay() returns None and the
caller rebuilds the conversation from its transcript with rebuild_from_transcript().

conversation_messages keeps every customer message and every reply (final, handoff) as raw text, so the
portal's History list can reopen a past conversation: its stream, asked from Last-Event-ID 0, sends the
transcript(). Built outside the spec at the user's request (2026-11-17): it reopens the rule that raw PII is
not kept. It lives and dies with its conversation (retention 24 months, erasure). A reply also keeps its
turn id and the citations sent with it (2026-11-19), so a reopened chat still shows its sources and its
"How this was answered" link (the trace itself is read from decision_records, which are kept).

The tables' SQL lives here (module-owned schema, DECISION-d4b79fe1) and is applied with apply_schema().
"""

import json

from data.migrate import psql

CHANNEL = "conversation_events"  # LISTEN / NOTIFY so an open stream does not wait out a poll (B14)
SCHEMA_NAME = "events_0001_outbound_event_log.sql"
SCHEMA = """
CREATE TABLE outbound_event_log (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  conversation_id text NOT NULL,
  event_type      text NOT NULL,
  data            jsonb NOT NULL DEFAULT '{}',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX outbound_event_log_conversation ON outbound_event_log (conversation_id, id);
CREATE INDEX outbound_event_log_created ON outbound_event_log (created_at);

ALTER TABLE outbound_event_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbound_event_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON outbound_event_log FOR SELECT USING (tenant_id = current_tenant());
CREATE POLICY tenant_write ON outbound_event_log FOR INSERT WITH CHECK (tenant_id = current_tenant());
-- Deletes only ever remove events past the window. A DELETE without WHERE reads no rows, so only this policy
-- applies and the purge covers every tenant; any WHERE clause adds the tenant_read policy on top.
CREATE POLICY expired_only ON outbound_event_log FOR DELETE USING (created_at < now() - interval '7 days');

ALTER TABLE outbound_event_log OWNER TO app_owner;
GRANT SELECT, INSERT, DELETE ON outbound_event_log TO app_runtime;
"""
MESSAGES_NAME = "events_0002_conversation_messages.sql"
SCHEMA_MESSAGES = """
CREATE TABLE conversation_messages (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  conversation_id text NOT NULL,
  role            text NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  text            text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX conversation_messages_conversation ON conversation_messages (conversation_id, id);

ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON conversation_messages
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());

ALTER TABLE conversation_messages OWNER TO app_owner;
GRANT SELECT, INSERT ON conversation_messages TO app_runtime;
"""
MESSAGES_TURNS_NAME = "events_0003_conversation_message_turns.sql"
SCHEMA_MESSAGES_TURNS = """
ALTER TABLE conversation_messages ADD COLUMN turn_id text, ADD COLUMN citations jsonb NOT NULL DEFAULT '[]';
"""
KEPT = {"final": "assistant", "handoff": "system"}  # event type -> the role its text is kept under


def apply_schema(database):
    """Create each table once, recorded in the schema_migrations ledger like the files in migrations/."""
    for name, sql in ((SCHEMA_NAME, SCHEMA), (MESSAGES_NAME, SCHEMA_MESSAGES),
                      (MESSAGES_TURNS_NAME, SCHEMA_MESSAGES_TURNS)):
        if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{name}'"):
            psql(database, f"BEGIN;\n{sql}\nINSERT INTO schema_migrations (name) VALUES ('{name}');\nCOMMIT;\n")


class EventLog:
    """Reads and writes as app_runtime under the tenant's context; needs an autocommit connection."""

    def __init__(self, conn):
        if not conn.autocommit:
            raise ValueError("EventLog needs an autocommit connection")
        self.conn = conn

    def append(self, tenant_id, conversation_id, event_type, data):
        """Store one event and return its id, which the SSE stream sends as the event id."""
        with self.conn.transaction():
            self._context(tenant_id)
            (event_id,) = self.conn.execute(
                "INSERT INTO outbound_event_log (tenant_id, conversation_id, event_type, data) "
                "VALUES (%s, %s, %s, %s::jsonb) RETURNING id",
                (tenant_id, conversation_id, event_type, json.dumps(data))).fetchone()
            if event_type in KEPT and data.get("text"):
                turn_id = data.get("turn_id")
                citations = [row for (row,) in self.conn.execute(
                    "SELECT data - 'turn_id' FROM outbound_event_log WHERE conversation_id = %s "
                    "AND event_type = 'citation' AND data ->> 'turn_id' = %s ORDER BY id",
                    (conversation_id, turn_id)).fetchall()] if turn_id else []
                self.keep(tenant_id, conversation_id, KEPT[event_type], str(data["text"]), turn_id, citations)
        self.conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, conversation_id))
        return str(event_id)

    def keep(self, tenant_id, conversation_id, role, text, turn_id=None, citations=()):
        """Add one message to the conversation's transcript (History); a reply with its turn and sources."""
        with self.conn.transaction():
            self._context(tenant_id)
            self.conn.execute("INSERT INTO conversation_messages (tenant_id, conversation_id, role, text, "
                              "turn_id, citations) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                              (tenant_id, conversation_id, role, text, turn_id, json.dumps(list(citations))))

    def transcript(self, tenant_id, conversation_id):
        """The conversation's messages, oldest first, as [{"role", "text", "at"}], a reply also with its
        "turn_id" and "citations" when it has them."""
        with self.conn.transaction():
            self._context(tenant_id)
            rows = self.conn.execute("SELECT role, text, created_at, turn_id, citations "
                                     "FROM conversation_messages WHERE conversation_id = %s ORDER BY id",
                                     (conversation_id,)).fetchall()
        return [{"role": role, "text": text, "at": at.isoformat(),
                 **({"turn_id": turn_id} if turn_id else {}), **({"citations": cited} if cited else {})}
                for role, text, at, turn_id, cited in rows]

    def replay(self, tenant_id, conversation_id, last_event_id):
        """Events after last_event_id as (id, type, data), or None if that event is unknown or expired."""
        try:
            last = int(last_event_id)
        except (TypeError, ValueError):
            return None
        with self.conn.transaction():
            self._context(tenant_id)
            known = self.conn.execute(
                "SELECT 1 FROM outbound_event_log WHERE id = %s AND conversation_id = %s "
                "AND created_at > now() - interval '7 days'", (last, conversation_id)).fetchone()
            if known is None:
                return None
            rows = self.conn.execute(
                "SELECT id, event_type, data FROM outbound_event_log WHERE conversation_id = %s AND id > %s "
                "ORDER BY id", (conversation_id, last)).fetchall()
        return [(str(event_id), event_type, data) for event_id, event_type, data in rows]

    def purge_expired(self):
        """Delete every tenant's events older than 7 days (run daily); returns how many were deleted."""
        with self.conn.transaction():
            self.conn.execute("SET LOCAL ROLE app_runtime")
            return self.conn.execute("DELETE FROM outbound_event_log").rowcount

    def _context(self, tenant_id):
        self.conn.execute("SET LOCAL ROLE app_runtime")
        self.conn.execute("SELECT set_config('app.current_tenant_id', %s, true)", (tenant_id,))


def rebuild_from_transcript(messages):
    """Events for a client whose Last-Event-ID is past the window: an expiry marker, then the transcript."""
    return [(None, "stream_expired", {}), *((None, "message", message) for message in messages)]

