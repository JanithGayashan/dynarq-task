"""Rules and command-line tests. Run from python/: python -m unittest."""

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from plan import build_plan


TODAY = "2026-10-12"
PYTHON_DIR = Path(__file__).resolve().parents[1]


def invoice(**changes):
    record = {
        "id": "INV-1001", "customer": "Example Ltd", "email": "accounts@example.test",
        "amount": 1250.0, "currency": "EUR", "due_date": "2026-09-30", "paid_date": None,
    }
    record.update(changes)
    return record


class PlanTests(unittest.TestCase):
    def test_empty_input_gives_exact_empty_shape(self):
        self.assertEqual(build_plan([], [], TODAY), {"today": TODAY, "actions": [], "skipped": []})

    def test_thresholds_and_one_action_at_most(self):
        cases = [(-8, None), (0, None), (1, None), (2, None), (3, "friendly"),
                 (9, "friendly"), (10, "firm"), (12, "firm"), (23, "firm"),
                 (24, "final"), (90, "final")]
        for days, expected in cases:
            with self.subTest(days=days):
                due = (date.fromisoformat(TODAY) - timedelta(days=days)).isoformat()
                plan = build_plan([invoice(due_date=due)], [], TODAY)
                self.assertEqual(plan["skipped"], [])
                if expected is None:
                    self.assertEqual(plan["actions"], [])
                else:
                    self.assertEqual(len(plan["actions"]), 1)
                    action = plan["actions"][0]
                    self.assertEqual(set(action), {
                        "invoice_id", "step", "needs_approval", "days_overdue", "message",
                    })
                    self.assertEqual(action["invoice_id"], "INV-1001")
                    self.assertEqual(action["step"], expected)
                    self.assertIs(action["needs_approval"], expected != "friendly")
                    self.assertEqual(action["days_overdue"], days)
                    self.assertIsInstance(action["message"], str)

    def test_sent_step_matrix(self):
        # Explicit expectations: the same or a higher sent step blocks the reached step.
        cases = [
            ("2026-10-09", "friendly", {"friendly", "firm", "final"}),
            ("2026-10-02", "firm", {"firm", "final"}),
            ("2026-09-18", "final", {"final"}),
        ]
        for due, expected_step, blocking_steps in cases:
            for sent_step in (None, "friendly", "firm", "final", "unknown"):
                with self.subTest(due=due, sent_step=sent_step):
                    sent = [] if sent_step is None else [{"invoice_id": "INV-1001", "step": sent_step}]
                    actions = build_plan([invoice(due_date=due)], sent, TODAY)["actions"]
                    self.assertEqual([a["step"] for a in actions],
                                     [] if sent_step in blocking_steps else [expected_step])

    def test_highest_history_step_wins_regardless_of_order_or_sent_on(self):
        sent = [
            {"invoice_id": "INV-1001", "step": "final", "sent_on": "2099-12-31"},
            {"invoice_id": "INV-1001", "step": "friendly", "sent_on": "not a date"},
            {"invoice_id": "INV-1001", "step": "firm"},
            {"invoice_id": "INV-1001", "step": "friendly"},
        ]
        for history in (sent, list(reversed(sent))):
            self.assertEqual(build_plan([invoice()], history, TODAY)["actions"], [])

    def test_unknown_history_is_ignored(self):
        sent = [
            {"invoice_id": "UNKNOWN", "step": "final"},
            {"invoice_id": "INV-1001", "step": "FIRM"},
            {"invoice_id": "INV-1001", "step": "unknown"},
            {"invoice_id": "INV-1001", "step": None},
            {"invoice_id": "INV-1001", "step": []},
            {"invoice_id": [], "step": "final"}, {},
        ]
        self.assertEqual(build_plan([invoice()], sent, TODAY), build_plan([invoice()], [], TODAY))

    def test_paid_invoices_plan_nothing_even_with_history(self):
        for paid in ("2026-09-29", TODAY):
            for sent in ([], [{"invoice_id": "INV-1001", "step": "friendly"}]):
                with self.subTest(paid=paid, sent=sent):
                    self.assertEqual(build_plan([invoice(paid_date=paid)], sent, TODAY),
                                     {"today": TODAY, "actions": [], "skipped": []})

    def test_missing_and_null_paid_date_are_unpaid(self):
        missing = invoice()
        del missing["paid_date"]
        self.assertEqual(build_plan([missing], [], TODAY), build_plan([invoice()], [], TODAY))

    def test_invalid_amounts(self):
        cases = [None, "1250", "", True, False, 0, 0.0, -1, -0.5, [], {},
                 float("nan"), float("inf"), float("-inf")]
        missing = invoice()
        del missing["amount"]
        for record in [missing] + [invoice(amount=value) for value in cases]:
            with self.subTest(amount=record.get("amount", "missing")):
                self.assert_skipped(record, "invalid_amount")

    def test_positive_integer_and_float_amounts_are_valid(self):
        for amount in (1, 0.01, 1250, 1250.50, 10 ** 400):
            with self.subTest(amount=amount):
                plan = build_plan([invoice(amount=amount)], [], TODAY)
                self.assertEqual(len(plan["actions"]), 1)
                self.assertEqual(plan["skipped"], [])

    def test_missing_due_dates(self):
        missing = invoice()
        del missing["due_date"]
        for record in (missing, invoice(due_date=None), invoice(due_date="")):
            with self.subTest(record=record):
                self.assert_skipped(record, "missing_due_date")

    def test_invalid_due_dates(self):
        values = ["2026-02-30", "2025-02-29", "30/09/2026", "2026-9-30", "20260930",
                  "2026-W40-1", "2026-09-30T00:00:00", "2026-09-30\n", " 2026-09-30",
                  "0000-01-01", "2026-13-01", "2026-01-00", "２０２６-０９-３０",
                  20260930, True, [], {}]
        for value in values:
            with self.subTest(value=value):
                self.assert_skipped(invoice(due_date=value), "invalid_due_date")

    def test_invalid_paid_dates(self):
        for value in ("", "2026-02-30", "2026-10-13", "2026-1-01", "20261001",
                      "2026-W40-1", "2026-10-01T00:00:00", "2026-10-01\n", True, 0, [], {}):
            with self.subTest(value=value):
                self.assert_skipped(invoice(paid_date=value), "invalid_paid_date")

    def test_validation_priority_before_paid_and_not_yet(self):
        cases = [
            (invoice(amount=0, due_date=None, paid_date="bad"), "invalid_amount"),
            (invoice(due_date=None, paid_date="bad"), "missing_due_date"),
            (invoice(due_date="bad", paid_date="bad"), "invalid_due_date"),
            (invoice(due_date="2026-10-20", paid_date="bad"), "invalid_paid_date"),
            (invoice(amount=0, paid_date=TODAY), "invalid_amount"),
            (invoice(due_date="bad", paid_date=TODAY), "invalid_due_date"),
        ]
        for record, reason in cases:
            with self.subTest(record=record):
                self.assert_skipped(record, reason)

    def test_all_duplicates_skipped_once_before_other_validation(self):
        records = [invoice(amount=0, due_date=None, paid_date="bad"),
                   invoice(paid_date=TODAY), invoice(), invoice(id="INV-1002")]
        original = copy.deepcopy(records)
        plan = build_plan(records, [], TODAY)
        self.assertEqual(plan["skipped"], [{"invoice_id": "INV-1001", "reason": "duplicate_id"}])
        self.assertEqual([a["invoice_id"] for a in plan["actions"]], ["INV-1002"])
        self.assertEqual(records, original)

    def test_lists_use_character_by_character_sorting(self):
        ids = ["INV-99", "INV-100", "inv-1", "INV-2", "Inv-1"]
        records = [invoice(id=value) for value in ids]
        expected = ["INV-100", "INV-2", "INV-99", "Inv-1", "inv-1"]
        self.assertEqual([a["invoice_id"] for a in build_plan(records, [], TODAY)["actions"]], expected)
        for record in records:
            record["amount"] = 0
        self.assertEqual([a["invoice_id"] for a in build_plan(records, [], TODAY)["skipped"]], expected)

    def test_calendar_days_across_month_year_and_leap_day(self):
        cases = [("2026-01-03", "2025-12-31", 3),
                 ("2024-03-02", "2024-02-28", 3),
                 ("2024-03-03", "2024-02-29", 3)]
        for today, due, days in cases:
            with self.subTest(today=today, due=due):
                action = build_plan([invoice(due_date=due)], [], today)["actions"][0]
                self.assertEqual(action["days_overdue"], days)
                self.assertEqual(action["step"], "friendly")

    def test_invalid_today_is_rejected_even_for_empty_input(self):
        for value in (None, "", "2026-02-30", "12/10/2026", "2026-1-12",
                      "20261012", "2026-W42-1", "2026-10-12\n", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Invalid today"):
                    build_plan([], [], value)

    def test_broken_input_structure_is_reported(self):
        for records, sent in (({}, []), ([], {}), ([None], []), ([], [None]),
                              ([invoice(id=1)], []), ([invoice(customer=None)], [])):
            with self.subTest(records=records, sent=sent):
                with self.assertRaises(ValueError):
                    build_plan(records, sent, TODAY)

    def test_engine_does_not_change_inputs(self):
        records = [invoice(id="INV-2"), invoice(id="INV-1")]
        sent = [{"invoice_id": "INV-1", "step": "friendly"}]
        original = copy.deepcopy((records, sent))
        first = build_plan(records, sent, TODAY)
        self.assertEqual(build_plan(records, sent, TODAY), first)
        self.assertEqual((records, sent), original)

    def test_messages_contain_only_required_figures_for_every_step(self):
        for due in ("2026-10-09", "2026-10-02", "2026-09-18"):
            with self.subTest(due=due):
                record = invoice(due_date=due, amount=1250.5, currency="USD")
                action = build_plan([record], [], TODAY)["actions"][0]
                message = action["message"]
                for fact in (record["customer"], record["id"], record["due_date"],
                             "USD 1250.50", f"{action['days_overdue']} days overdue"):
                    self.assertIn(fact, message)
                self.assertEqual(re.findall(r"\d+(?:\.\d+)?", message),
                                 ["1001", "1250.50", *due.split("-"), str(action["days_overdue"])])
                for unsupported in ("fee", "interest", "previous", "earlier", "legal"):
                    self.assertNotIn(unsupported, message.lower())

    def assert_skipped(self, record, reason):
        self.assertEqual(build_plan([record], [], TODAY), {
            "today": TODAY, "actions": [],
            "skipped": [{"invoice_id": record["id"], "reason": reason}],
        })


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.invoices = self.folder / "invoices.json"
        self.sent = self.folder / "sent.json"
        self.output = self.folder / "actions.json"
        self.invoices.write_text(json.dumps([invoice()]), encoding="utf-8")
        self.sent.write_text("[]", encoding="utf-8")

    def run_plan(self, today=TODAY):
        return subprocess.run([
            sys.executable, str(PYTHON_DIR / "plan.py"), "--invoices", str(self.invoices),
            "--sent", str(self.sent), "--today", today, "--out", str(self.output),
        ], cwd=PYTHON_DIR, capture_output=True, text=True, timeout=10)

    def assert_clear_error(self, result, expected):
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_command_writes_the_complete_plan_with_exit_zero(self):
        result = self.run_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")),
                         build_plan([invoice()], [], TODAY))

    def test_invalid_today_does_not_overwrite_output(self):
        self.output.write_text("keep existing output", encoding="utf-8")
        result = self.run_plan(today="2026-02-30")
        self.assert_clear_error(result, "Invalid --today")
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep existing output")

    def test_missing_input_files(self):
        for path in (self.invoices, self.sent):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                self.assert_clear_error(self.run_plan(), str(path))
                path.write_bytes(original)
        self.assertFalse(self.output.exists())

    def test_invalid_json_and_encoding(self):
        for path in (self.invoices, self.sent):
            for value in (b"{broken", b"\xff"):
                with self.subTest(path=path.name, value=value):
                    original = path.read_bytes()
                    path.write_bytes(value)
                    self.assert_clear_error(self.run_plan(), "Cannot read JSON file")
                    path.write_bytes(original)
        self.assertFalse(self.output.exists())

    def test_wrong_input_shape(self):
        self.invoices.write_text("{}", encoding="utf-8")
        self.assert_clear_error(self.run_plan(), "invoices must contain a JSON list of objects")
        self.assertFalse(self.output.exists())

    def test_unwritable_output_path(self):
        self.output = self.folder / "missing-folder" / "actions.json"
        self.assert_clear_error(self.run_plan(), "Cannot write output file")

    def test_sample_data_command(self):
        self.invoices = PYTHON_DIR.parent / "data" / "invoices.json"
        self.sent = PYTHON_DIR.parent / "data" / "sent.json"
        result = self.run_plan()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual([(a["invoice_id"], a["step"]) for a in plan["actions"]], [
            ("INV-1001", "firm"), ("INV-1002", "friendly"), ("INV-1004", "final"),
            ("INV-1009", "firm"), ("INV-1011", "firm"),
        ])
        self.assertEqual(plan["skipped"], [
            {"invoice_id": "INV-1007", "reason": "invalid_due_date"},
            {"invoice_id": "INV-1008", "reason": "missing_due_date"},
            {"invoice_id": "INV-1010", "reason": "duplicate_id"},
        ])


if __name__ == "__main__":
    unittest.main()
