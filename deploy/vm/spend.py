"""This month's model spend across every tenant against the USD 10 cap (NFR-36, AC-15):
`python deploy/vm/spend.py [YYYY-MM]` in the app image.

The usage ledger (cost.budgets) is priced with the deployed models' prices in deploy/vm/models.json. A model
in the ledger without a price there is reported, never silently counted as free. Exit 1 when the month is at
or over the cap or a model is unpriced. The provider spend limits (the Anthropic console limit; free plans
everywhere else) stay the enforcement; this is the check that the books agree.
"""

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MILLION = Decimal(1_000_000)


def prices(lock):
    return {spec["model"]: tuple(Decimal(str(p)) for p in spec["usd_per_mtok"])
            for spec in lock["tiers"].values()}


def month_spend(rows, price_table):
    """rows: (model, prompt tokens, completion tokens) -> (USD, models without a price)."""
    total, unpriced = Decimal(0), []
    for model, prompt, completion in rows:
        if model not in price_table:
            unpriced.append(model)
            continue
        rate_in, rate_out = price_table[model]
        total += (Decimal(prompt) * rate_in + Decimal(completion) * rate_out) / MILLION
    return total, sorted(set(unpriced))


def verdict(total, unpriced, cap):
    lines = [f"model spend this month: {total:.4f} USD of {cap} USD"]
    if unpriced:
        lines.append(f"models without a price in deploy/vm/models.json: {', '.join(unpriced)}")
    if total >= Decimal(str(cap)):
        lines.append("the month is at or over the cap")
    return lines, bool(unpriced) or total >= Decimal(str(cap))


def main(argv=None):
    import psycopg

    month = (argv or sys.argv[1:] or [datetime.now(timezone.utc).strftime("%Y-%m")])[0]
    lock = json.loads((ROOT / "deploy" / "vm" / "models.json").read_text(encoding="utf-8"))
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute("SELECT model, sum(prompt_tokens), sum(completion_tokens) FROM usage_ledger "
                            "WHERE month = %s GROUP BY model", (month,)).fetchall()
    lines, over = verdict(*month_spend(rows, prices(lock)), lock["monthly_cap_usd"])
    print("\n".join(lines), flush=True)
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
