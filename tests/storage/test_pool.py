"""data.pool.wait_for (the startup retry) and data.events.Listener's reconnect, without a database."""

import asyncio
import threading
from types import SimpleNamespace

import psycopg
import pytest

from data.events import Listener
from data.pool import wait_for


def test_wait_for_retries_with_doubling_delays_then_gives_up():
    waits, attempts = [], []

    def down(dsn, connect_timeout):
        attempts.append(dsn)
        raise psycopg.OperationalError("connection refused")
    with pytest.raises(psycopg.OperationalError):
        wait_for("postgresql://x", connect=down, sleep=waits.append)
    assert len(attempts) == 5 and waits == [2.0, 4.0, 8.0, 16.0]   # 30 s in all


def test_wait_for_returns_once_the_server_answers():
    replies = iter([psycopg.OperationalError("starting up"), None])

    class Up:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def connect(dsn, connect_timeout):
        reply = next(replies)
        if reply:
            raise reply
        return Up()
    waits = []
    wait_for("postgresql://x", connect=connect, sleep=waits.append)
    assert waits == [2.0]


def test_a_dropped_listen_connection_is_reopened_and_every_stream_woken_then_notified():
    executed, served = [], threading.Event()

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            executed.append(sql)

        def notifies(self):
            yield SimpleNamespace(payload="c1")
            served.set()
            threading.Event().wait()   # then quiet, as a live connection is

    replies = iter([psycopg.OperationalError("server closed the connection"), Conn()])

    def connect(dsn, **options):
        reply = next(replies)   # the first attempt fails, the one after the 1 s backoff succeeds
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def scenario():
        hub, loop = Listener("postgresql://x", connect=connect), asyncio.get_running_loop()
        mine, other = asyncio.Event(), asyncio.Event()
        hub.subscribe("c1", mine, loop)
        hub.subscribe("c2", other, loop)
        await asyncio.wait_for(asyncio.to_thread(served.wait, 5), 6)   # reconnected after the 1 s backoff
        await asyncio.sleep(0.05)
        assert mine.is_set() and other.is_set()          # everyone woken once after the reconnect
        hub.unsubscribe("c2", other)
        assert list(hub.waiters) == ["c1"]
    asyncio.run(scenario())
    assert executed == ["LISTEN conversation_events"]
