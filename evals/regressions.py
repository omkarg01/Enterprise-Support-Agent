"""Thumbs-down replies as regression cases, and their replay (built outside Genesis, 2026-11-20).

    python -m evals.regressions export                         new thumbs-down replies -> regression_cases
    python -m evals.regressions replay [--api URL] [--tenant T] [--limit N]

- export(): every thumbs down not exported yet becomes one case: the question before the reply, the reply, the
  comment and its category, and the reply's models. Question and reply are masked with the PII tokenizer
  (PII_TOKEN_KEY) first, and a case whose text still holds personal data is skipped. Unlike evals.datasets'
  production samples there is no tenant opt-in: the user asked for these cases (2026-11-20).
- replay(): each case's question goes to a running API as a new guest conversation (the real turn path:
  screening, retrieval, the models), and Jev judges whether the new reply fixes the complaint. A verdict
  under 0.70 confidence, or none (Jev unavailable), is printed side by side for a person to judge.
- Erasure removes a user's cases with their conversations (memory.lifecycle.inventory).
"""

import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from psycopg.types.json import Jsonb

from core.orchestrator.decisions import JevUnavailable, Masked, SecurityViolation
from data.migrate import psql
from improvement.feedback import _context, ratings
from safety.pii.tokens import find as find_pii

SURE_AT = 0.70
MODELS = Path(__file__).resolve().parents[1] / "deploy" / "vm" / "models.json"  # the pinned Jev version
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
VERDICT = {"verdict": {
    "type": "choice",
    "instructions": "A customer disliked the old answer to their question, for the reason in the complaint. "
                    "Does the new answer to the same question fix that?",
    "criteria": {"fixed": "Yes: the new answer resolves what the complaint describes and adds no new problem",
                 "not_fixed": "No: the new answer has the same problem, or a new one"}}}

SCHEMA_NAME = "evals_0002_regression_cases.sql"
SCHEMA = """
CREATE TABLE regression_cases (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES tenants (id),
  feedback_id     bigint NOT NULL UNIQUE,
  conversation_id text NOT NULL,
  turn_id         text NOT NULL,
  question        text NOT NULL,
  answer          text NOT NULL,
  comment         text,
  category        text,
  models          jsonb NOT NULL DEFAULT '[]',
  created_at      timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE regression_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE regression_cases FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON regression_cases
  USING (tenant_id = current_tenant()) WITH CHECK (tenant_id = current_tenant());
ALTER TABLE regression_cases OWNER TO app_owner;
GRANT SELECT, INSERT, DELETE ON regression_cases TO app_runtime;
"""


def apply_schema(database):
    if not psql(database, f"SELECT 1 FROM schema_migrations WHERE name = '{SCHEMA_NAME}'"):
        psql(database, f"BEGIN;\n{SCHEMA}\nINSERT INTO schema_migrations (name) VALUES ('{SCHEMA_NAME}');\n"
                       "COMMIT;\n")


def export(connect, tokenizer, tenants):
    """Store every thumbs down not exported yet as a masked case. Returns (added, skipped)."""
    downs = [r for r in ratings(connect, tenants, EPOCH) if r["signal"] == "down"]
    added = skipped = 0
    with connect() as conn:
        for tenant in tenants:
            with conn.transaction():
                _context(conn, tenant)
                done = {fid for (fid,) in conn.execute("SELECT feedback_id FROM regression_cases")}
                for r in downs:
                    if r["tenant_id"] != tenant or r["id"] in done:
                        continue
                    if not r["question"] or not r["answer"]:
                        skipped += 1
                        continue
                    question, answer = tokenizer.mask(r["question"])[0], tokenizer.mask(r["answer"])[0]
                    if find_pii(question) or find_pii(answer):
                        skipped += 1
                        continue
                    conn.execute(
                        "INSERT INTO regression_cases (tenant_id, feedback_id, conversation_id, turn_id, "
                        "question, answer, comment, category, models) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (tenant, r["id"], r["conversation_id"], r["turn_id"], question, answer, r["comment"],
                         r["category"], Jsonb(r["models"])))
                    added += 1
    return added, skipped


def cases(connect, tenants, limit=None):
    out = []
    with connect() as conn:
        for tenant in tenants:
            with conn.transaction():
                _context(conn, tenant)
                rows = conn.execute("SELECT id, question, answer, comment, category, models "
                                    "FROM regression_cases ORDER BY id").fetchall()
            out += [{"id": i, "tenant_id": tenant, "question": q, "answer": a, "comment": c, "category": cat,
                     "models": m} for i, q, a, c, cat, m in rows]
    return out[:limit] if limit else out


def ask(http, tenant_id, question, wait=180.0):
    """The reply a running API gives `question` in a new guest conversation: the text its turn ended with."""
    signed = http.post("/v1/identity/anonymous", json={"tenant_id": tenant_id})
    signed.raise_for_status()
    auth = {"Authorization": f"Bearer {signed.json()['access_token']}"}
    conversation = uuid.uuid4().hex
    deadline, kind = time.monotonic() + wait, None
    # the stream is open (its cursor taken) before the turn is sent, so even a fast reply is on it.
    # ponytail: one stream, no reconnect; a reply slower than the server's stream window times out the case
    with http.stream("GET", f"/v1/conversations/{conversation}/events", headers=auth, timeout=wait) as reply:
        reply.raise_for_status()
        sent = http.post(f"/v1/conversations/{conversation}/turns", json={"content": question},
                         headers={**auth, "Idempotency-Key": uuid.uuid4().hex})
        sent.raise_for_status()
        turn_id = sent.json()["turn_id"]
        for line in reply.iter_lines():
            if line.startswith("event: "):
                kind = line.removeprefix("event: ")
            elif line.startswith("data: ") and kind in ("final", "handoff", "error"):
                data = json.loads(line.removeprefix("data: "))
                if data.get("turn_id") in (None, turn_id) and not data.get("history"):
                    return data.get("text") or ""
            if time.monotonic() > deadline:
                break
    raise TimeoutError(f"no reply within {wait:.0f} s")


def judge(engine, case, new_answer):
    """(verdict, confidence) from Jev, or (None, None) when Jev cannot answer."""
    state = {"question": Masked(case["question"]), "old_answer": Masked(case["answer"]),
             "new_answer": Masked(new_answer), "complaint": Masked(case["comment"] or "No reason given")}
    try:
        answer = engine.ask(state, VERDICT)["verdict"]
    except (JevUnavailable, SecurityViolation):
        return None, None
    return answer.value, answer.confidence


def replay(regressions, ask_one, engine, tokenizer):
    """One result per case: {"case", "new_answer" (masked), "verdict", "confidence", "sure", "error"}."""
    results = []
    for case in regressions:
        try:
            new_answer, error = tokenizer.mask(ask_one(case["tenant_id"], case["question"]))[0], None
        except (httpx.HTTPError, TimeoutError) as failure:
            new_answer, error = "", str(failure)
        verdict, confidence = judge(engine, case, new_answer) if not error else (None, None)
        results.append({"case": case, "new_answer": new_answer, "verdict": verdict, "confidence": confidence,
                        "sure": verdict is not None and confidence >= SURE_AT, "error": error})
    return results


def report(results, out=print):
    def short(text, n=70):
        text = " ".join((text or "").split())
        return text if len(text) <= n else text[:n - 1] + "…"
    for r in results:
        c = r["case"]
        label = r["error"] and "error" or (r["verdict"] or "no verdict") + ("" if r["sure"] else " (unsure)")
        score = "" if r["confidence"] is None else f"{r['confidence']:.2f}"
        out(f"#{c['id']:<5} {c['tenant_id']:<6} {label:<20} {score:>4}  {short(c['question'])}")
    review = [r for r in results if not r["sure"]]
    if review:
        out("\nFor you to judge (Jev unsure, unavailable, or the replay failed):")
    for r in review:
        c = r["case"]
        out(f"\n--- case #{c['id']} ({c['tenant_id']}, {c['category'] or 'no category'}, "
            f"{', '.join(c['models']) or 'model unknown'}) ---")
        out(f"Question:   {c['question']}\nComplaint:  {c['comment'] or '(no reason given)'}")
        out(f"Old answer: {c['answer']}\nNew answer: {r['new_answer'] or '(none: ' + str(r['error']) + ')'}")
    fixed = sum(r["sure"] and r["verdict"] == "fixed" for r in results)
    not_fixed = sum(r["sure"] and r["verdict"] == "not_fixed" for r in results)
    out(f"\n{len(results)} cases: {fixed} fixed, {not_fixed} not fixed, {len(review)} for you to judge")


def main(argv=None):
    from channels.api.app import database_url
    from channels.api.staff import TENANTS
    from core.orchestrator.decisions import DecisionEngine, JevClient
    from core.secrets import load_secret
    from runtime import NoJev, Remasked, connector
    from safety.pii.tokens import Tokenizer

    parser = argparse.ArgumentParser(prog="python -m evals.regressions")
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("export", help="store new thumbs-down replies as regression cases")
    run = command.add_parser("replay", help="ask a running API each case again and judge the new replies")
    run.add_argument("--api", default=os.environ.get("SUPPORT_AGENT_API", "http://127.0.0.1:8000"))
    run.add_argument("--tenant", help="one tenant (default STAFF_TENANTS)")
    run.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    connect, tokenizer = connector(database_url()), Tokenizer(load_secret("PII_TOKEN_KEY"))
    tenants = (args.tenant,) if getattr(args, "tenant", None) else TENANTS
    if args.command == "export":
        added, skipped = export(connect, tokenizer, tenants)
        print(f"{added} new regression cases; {skipped} thumbs down skipped (no question or reply, or "
              "personal data left after masking)")
        return
    lock = json.loads(MODELS.read_text("utf-8"))
    pinned, key = (lock.get("jev") or {}).get("model") or "jev-latest", os.environ.get("TYPESAFE_API_KEY")
    jev = JevClient(key, model=pinned, timeout=15.0) if key else NoJev()
    with httpx.Client(base_url=args.api, timeout=30.0) as http:
        report(replay(cases(connect, tenants, args.limit), lambda t, q: ask(http, t, q),
                      Remasked(DecisionEngine(jev)), tokenizer))


if __name__ == "__main__":
    main()
