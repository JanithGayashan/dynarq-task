"""Payment reminder rules engine (part 1).

Run from inside python/:

    python plan.py --invoices ../data/invoices.json --sent ../data/sent.json --today 2026-10-12 --out ../actions.json

The pure build_plan function is also used by the owner's page.
"""

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import date

from messages import render_message


# In escalation order: threshold in calendar days, then approval requirement.
STEPS = {"friendly": (3, False), "firm": (10, True), "final": (24, True)}
STEP_RANK = {step: rank for rank, step in enumerate(STEPS)}


def parse_date(value):
    """Accept only a real date written with four/two/two ASCII digits."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("expected a real date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("expected a real date in YYYY-MM-DD format") from exc


def _invalid_reason(invoice, today_date):
    """Check invoice data in the required priority order (duplicates checked first)."""
    amount = invoice.get("amount")
    # bool is a subclass of int, but JSON true/false are not amounts.
    if type(amount) not in (int, float) or amount <= 0:
        return "invalid_amount"
    if isinstance(amount, float) and not math.isfinite(amount):
        return "invalid_amount"

    due_date = invoice.get("due_date")
    if due_date is None or due_date == "":
        return "missing_due_date"
    try:
        parse_date(due_date)
    except ValueError:
        return "invalid_due_date"

    paid_date = invoice.get("paid_date")
    if paid_date is not None:
        try:
            if parse_date(paid_date) > today_date:
                return "invalid_paid_date"
        except ValueError:
            return "invalid_paid_date"
    return None


def _validate_input_shape(invoices, sent):
    """Report broken file structure separately from the defined skip reasons."""
    for name, records in (("invoices", invoices), ("sent", sent)):
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError(f"{name} must contain a JSON list of objects")
    for index, invoice in enumerate(invoices):
        for field in ("id", "customer", "currency"):
            if not isinstance(invoice.get(field), str):
                raise ValueError(f"invoices entry {index + 1} must have a text {field}")


def build_plan(invoices, sent, today):
    """Return the plan for `today` as a dict with the keys today, actions and skipped.

    invoices: the list from invoices.json
    sent: the list from sent.json
    today: a date string, YYYY-MM-DD
    """
    try:
        today_date = parse_date(today)
    except ValueError as exc:
        raise ValueError(f"Invalid today: {exc}") from exc
    _validate_input_shape(invoices, sent)
    counts = Counter(invoice["id"] for invoice in invoices)

    highest_sent = {}
    for reminder in sent:
        invoice_id, step = reminder.get("invoice_id"), reminder.get("step")
        if not isinstance(invoice_id, str) or invoice_id not in counts:
            continue
        if not isinstance(step, str) or step not in STEP_RANK:
            continue
        highest_sent[invoice_id] = max(highest_sent.get(invoice_id, -1), STEP_RANK[step])

    actions, skipped = [], []
    duplicate_ids = set()
    for invoice in invoices:
        invoice_id = invoice["id"]
        if counts[invoice_id] > 1:
            if invoice_id not in duplicate_ids:
                skipped.append({"invoice_id": invoice_id, "reason": "duplicate_id"})
                duplicate_ids.add(invoice_id)
            continue

        reason = _invalid_reason(invoice, today_date)
        if reason:
            skipped.append({"invoice_id": invoice_id, "reason": reason})
            continue
        if invoice.get("paid_date") is not None:
            continue

        days_overdue = (today_date - parse_date(invoice["due_date"])).days
        for step in reversed(STEPS):
            threshold, needs_approval = STEPS[step]
            if days_overdue < threshold:
                continue
            # Once the highest reached step is selected, never fall back to a lower one.
            if highest_sent.get(invoice_id, -1) < STEP_RANK[step]:
                actions.append({
                    "invoice_id": invoice_id,
                    "step": step,
                    "needs_approval": needs_approval,
                    "days_overdue": days_overdue,
                    "message": render_message(invoice, step, days_overdue),
                })
            break

    return {
        "today": today,
        "actions": sorted(actions, key=lambda item: item["invoice_id"]),
        "skipped": sorted(skipped, key=lambda item: item["invoice_id"]),
    }


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read JSON file '{path}': {exc}") from exc


def main():
    parser = argparse.ArgumentParser(description="Plan today's payment reminders.")
    parser.add_argument("--invoices", required=True, help="path to invoices.json")
    parser.add_argument("--sent", required=True, help="path to sent.json")
    parser.add_argument("--today", required=True, help="date as YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="path to write the plan JSON")
    args = parser.parse_args()

    try:
        # Validate today before touching the input or output files.
        try:
            parse_date(args.today)
        except ValueError as exc:
            raise ValueError(f"Invalid --today: {exc}") from exc
        invoices = read_json(args.invoices)
        sent = read_json(args.sent)
        plan = build_plan(invoices, sent, args.today)

        try:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2, ensure_ascii=False, allow_nan=False)
                f.write("\n")
        except OSError as exc:
            raise ValueError(f"Cannot write output file '{args.out}': {exc}") from exc
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
