"""Message contract checks independent of the sample invoices."""

import os
import re
import unittest
from collections import Counter
from unittest.mock import patch

from messages import format_amount, render_message
from page import render_page
from plan import build_plan


class MessageTests(unittest.TestCase):
    def setUp(self):
        self.invoice = {
            "id": "INV-2048-A", "customer": "Studio 51 & Partners", "currency": "USD",
            "amount": 845.25, "due_date": "2026-09-18", "paid_date": None,
        }

    def test_required_facts_and_no_other_figures_for_all_steps(self):
        records = [self.invoice,
                   {**self.invoice, "customer": "Café 東京 73", "id": "A-005-B", "currency": "EUR",
                    "amount": 0.01, "due_date": "2024-02-29"}]
        for record in records:
            for step, days in (("friendly", 3), ("firm", 10), ("final", 24)):
                with self.subTest(customer=record["customer"], step=step):
                    message = render_message(record, step, days)
                    facts = [record["customer"], record["id"], record["due_date"],
                             f"{record['currency']} {format_amount(record['amount'])}", str(days)]
                    for fact in facts:
                        self.assertIn(fact, message)
                    self.assertIn(f"{days} days overdue", message)
                    # Count occurrences too: repeating an allowed number as a deadline must fail.
                    expected = Counter(re.findall(r"\d+(?:\.\d+)?", "\n".join(facts)))
                    actual = Counter(re.findall(r"\d+(?:\.\d+)?", message))
                    self.assertEqual(actual, expected)

    def test_amounts_have_exactly_two_decimal_places(self):
        cases = [(1, "1.00"), (0.01, "0.01"), (480.5, "480.50"), (1250.0, "1250.00"),
                 (2.675, "2.68"), (999999.99, "999999.99"), (1e6, "1000000.00")]
        for amount, expected in cases:
            for step in ("friendly", "firm", "final"):
                with self.subTest(amount=amount, step=step):
                    record = {**self.invoice, "amount": amount}
                    self.assertEqual(format_amount(amount), expected)
                    self.assertIn(f"USD {expected}", render_message(record, step, 24))

    def test_currency_is_taken_from_invoice_without_conversion(self):
        for currency in ("EUR", "USD", "GBP", "LKR", "JPY"):
            with self.subTest(currency=currency):
                record = {**self.invoice, "currency": currency}
                self.assertIn(f"{currency} 845.25", render_message(record, "friendly", 3))

    def test_requests_escalate_without_new_factual_claims(self):
        friendly = render_message(self.invoice, "friendly", 3)
        firm = render_message(self.invoice, "firm", 10)
        final = render_message(self.invoice, "final", 24)
        self.assertIn("friendly reminder", friendly)
        self.assertIn("questions about this invoice", friendly)
        self.assertIn("Payment is outstanding", firm)
        self.assertIn("confirm its status", firm)
        self.assertIn("final payment reminder", final)
        self.assertIn("priority", final)
        self.assertEqual(len({friendly, firm, final}), 3)

    def test_template_adds_no_fees_deadlines_legal_or_history_claims(self):
        record = {**self.invoice, "customer": "Fees & Interest Legal Ltd", "id": "INV-COURT-01"}
        forbidden = r"\b(fees?|interest|penalt\w*|deadlines?|tomorrow|within|hours?|percent|legal|court|" \
                    r"lawyer|collections?|previous|earlier|already|again)\b|%"
        for step in ("friendly", "firm", "final"):
            with self.subTest(step=step):
                message = render_message(record, step, 24)
                # Invoice text may contain these words; check only what the template adds.
                for field in ("customer", "id", "currency", "due_date"):
                    message = message.replace(record[field], "")
                self.assertNotRegex(message.lower(), forbidden)

    def test_generation_works_without_environment_keys_or_network(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("socket.socket", side_effect=AssertionError("Network access is forbidden")), \
             patch("socket.create_connection", side_effect=AssertionError("Network access is forbidden")):
            for step in ("friendly", "firm", "final"):
                self.assertTrue(render_message(self.invoice, step, 24))
            self.assertTrue(build_plan([self.invoice], [], "2026-10-12")["actions"][0]["message"])

    def test_emails_do_not_depend_on_the_sent_history(self):
        plan = build_plan([self.invoice], [], "2026-10-12")
        sent = [{"invoice_id": self.invoice["id"], "step": "friendly", "sent_on": "2026-09-21"},
                {"invoice_id": self.invoice["id"], "step": "firm", "sent_on": "2026-09-28"}]
        self.assertEqual(build_plan([self.invoice], sent, "2026-10-12")["actions"][0]["message"],
                         plan["actions"][0]["message"])

    def test_page_and_email_display_the_same_rounded_amount(self):
        record = {**self.invoice, "amount": 2.675}
        plan = build_plan([record], [], "2026-10-12")
        message = plan["actions"][0]["message"]
        page = render_page(plan["today"], plan, {record["id"]: record})
        self.assertIn("USD 2.68", message)
        self.assertIn("<dd>USD 2.68</dd>", page)


if __name__ == "__main__":
    unittest.main()
