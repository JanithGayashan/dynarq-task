"""Deterministic reminder emails, with no API key or network dependency.

All facts come from a validated invoice and its calculated days overdue. The
templates add only a greeting and a request for payment, never fees, interest,
new deadlines, legal threats, or a claim about previous reminders.
"""

from decimal import Decimal


OPENINGS = {
    "friendly": "A friendly reminder: payment is outstanding for",
    "firm": "Payment is outstanding for",
    "final": "This is a final payment reminder for",
}
REQUESTS = {
    "friendly": ("Please arrange payment. If you have any questions about this invoice, "
                 "please let us know. Thank you."),
    "firm": "Please arrange payment and confirm its status. Thank you.",
    "final": ("Please give this invoice priority. Arrange payment and contact us "
              "to confirm its status. Thank you."),
}


def format_amount(amount):
    """Render two decimal places from the number's decimal representation."""
    return format(Decimal(str(amount)), ".2f")


def render_message(invoice, step, days_overdue):
    """Use only validated invoice facts, with no fees or invented deadlines."""
    amount = format_amount(invoice["amount"])
    return (
        f"Hello {invoice['customer']},\n\n"
        f"{OPENINGS[step]} invoice {invoice['id']} for {invoice['currency']} {amount}, "
        f"due on {invoice['due_date']}. It is {days_overdue} days overdue.\n\n"
        f"{REQUESTS[step]}"
    )
