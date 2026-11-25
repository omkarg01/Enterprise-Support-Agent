"""Disk use on the database server against its quota (Aiven free: 1 GB): `python deploy/vm/db_size.py [N]`
in the app image or on a laptop with DATABASE_URL set. One connection, closed on exit.

Prints every database on the server with its size, then the N (default 15) largest tables of the main
database (with indexes and TOAST: pg_total_relation_size) and their estimated rows, then the tables known to
grow without bound. Exit 1 at or above 80 % of DB_DISK_LIMIT_MB (default 1024), so a cron line can alert.

Nothing in the app purges on a schedule yet (2026-11-25): EventLog.purge_expired (7-day stream window) and
memory.lifecycle.retention.sweep (facts 12 months, conversations 24, closed cases 30 days) are only called by
tests. Until a job runs them, these only grow.
"""

import os
import sys

GROWING = {
    "outbound_event_log": "every stream event; 7-day window, but purge_expired is not scheduled",
    "conversation_messages": "every message and reply (History); retention sweep not scheduled",
    "turns": "every accepted turn",
    "decision_records": "one per answer; kept 2 years (legal archive)",
    "tool_audit_records": "one per tool call; audit, kept",
    "case_state": "statechart state per case; 30 days after close, sweep not scheduled",
    "usage_ledger": "model tokens per call (spend.py reads it)",
    "sessions": "one per sign-in, never deleted",
    "otp_challenges": "one per code sent, never deleted",
    "feedback_events": "one per thumbs rating",
    "ticket_events": "one per ticket change or note",
}
SIZES = ("SELECT datname, pg_database_size(datname) FROM pg_database WHERE NOT datistemplate "
         "ORDER BY 2 DESC")
TABLES = ("SELECT c.relname, pg_total_relation_size(c.oid), c.reltuples::bigint FROM pg_class c "
          "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r' "
          "ORDER BY 2 DESC LIMIT %s")


def mb(size):
    return f"{size / 1024 / 1024:8.1f} MB"


def report(databases, tables, limit_mb):
    """(lines, over the 80 % mark?) from (name, bytes) and (table, bytes, estimated rows) rows."""
    used = sum(size for _, size in databases)
    lines = [f"server: {mb(used)} of {limit_mb} MB ({100 * used / (limit_mb * 1024 * 1024):.0f} %)"]
    lines += [f"  {name:<24}{mb(size)}" for name, size in databases]
    lines.append("largest tables of the main database:")
    lines += [f"  {name:<28}{mb(size)}  ~{max(rows, 0):>9} rows"
              + (f"  grows: {GROWING[name]}" if name in GROWING else "") for name, size, rows in tables]
    return lines, used >= 0.8 * limit_mb * 1024 * 1024


def main(argv=None):
    import psycopg

    top = int((argv or sys.argv[1:] or ["15"])[0])
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn:
        databases = conn.execute(SIZES).fetchall()
        tables = conn.execute(TABLES, (top,)).fetchall()
    lines, over = report(databases, tables, int(os.environ.get("DB_DISK_LIMIT_MB", "1024")))
    print("\n".join(lines), flush=True)
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
