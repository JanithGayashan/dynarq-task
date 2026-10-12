"""Offline email templates; all figures come from the invoice and the plan."""

from decimal import Decimal


OPENINGS = {
    "friendly": "A friendly reminder about",
    "firm": "Payment is outstanding for",
    "final": "This is a final payment reminder for",
}
REQUESTS = {
    "friendly": "Please arrange payment when you can. Thank you.",
    "firm": "Please arrange payment and let us know its status. Thank you.",
    "final": "Please treat this as a priority and contact us about payment. Thank you.",
}


def render_message(invoice, step, days_overdue):
    """Use only validated invoice facts, with no fees or invented deadlines."""
    amount = format(Decimal(str(invoice["amount"])), ".2f")
    return (
        f"Hello {invoice['customer']},\n\n"
        f"{OPENINGS[step]} invoice {invoice['id']} for {invoice['currency']} {amount}, "
        f"due on {invoice['due_date']}. It is {days_overdue} days overdue.\n\n"
        f"{REQUESTS[step]}"
    )
