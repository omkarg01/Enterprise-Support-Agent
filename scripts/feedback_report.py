"""The portal's thumbs in the terminal (built outside Genesis, 2026-11-20): the summary and the thumbs-down
list the staff console's Feedback page shows (improvement.feedback.ratings / summary).

    python scripts/feedback_report.py [--days 30] [--tenant t_us]

Reads the database the API uses (APP_DATABASE_URL, else DATABASE_URL).
"""

import argparse
from datetime import datetime, timedelta, timezone

from channels.api.app import database_url
from channels.api.staff import TENANTS
from improvement.feedback import ratings, summary
from runtime import connector


def rate(group):
    share = "-" if group["up_rate"] is None else f"{group['up_rate']:.0%}"
    return f"{share:>4} up  ({group['up']} up, {group['down']} down)"


def report(rows, days, out=print):
    s = summary(rows)
    out(f"Thumbs, last {days} days: {rate(s['total'])}")
    out("\nBy day:")
    for day, group in s["by_day"].items():
        out(f"  {day}  {rate(group)}")
    out("\nBy model:")
    for model, group in s["by_model"].items():
        out(f"  {model:<32} {rate(group)}")
    out("\nThumbs down by failure category:")
    for category, n in s["down_by_category"].items():
        out(f"  {n:>4}  {category}")
    downs = [r for r in rows if r["signal"] == "down"]
    out(f"\nThumbs down ({len(downs)}), most severe first:")
    for r in sorted(downs, key=lambda r: -(r["severity"] or 0)):
        severity = "" if r["severity"] is None else f" severity {r['severity']:.2f}"
        out(f"\n--- {r['at'][:16]} {r['tenant_id']} {r['conversation_id']} turn {r['turn_id']}"
            f" ({r['category'] or 'no reason given'}{severity}; {', '.join(r['models']) or 'model unknown'})")
        out(f"Question: {r['question'] or '(not kept)'}\nAnswer:   {r['answer'] or '(not kept)'}")
        out(f"Comment:  {r['comment'] or '(none)'}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--tenant", help="one tenant (default STAFF_TENANTS)")
    args = parser.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    report(ratings(connector(database_url()), (args.tenant,) if args.tenant else TENANTS, since), args.days)


if __name__ == "__main__":
    main()
