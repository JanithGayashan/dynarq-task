"""HTTP integration tests for the owner's plan and in-memory decisions."""

import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import app
from page import action_key


TODAY = "2026-10-12"


def invoice(invoice_id, due_date="2026-09-30", **changes):
    record = {"id": invoice_id, "customer": "Acme & Sons", "currency": "EUR",
              "amount": 1250.5, "due_date": due_date, "paid_date": None}
    record.update(changes)
    return record


class FormParser(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(body)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form":
            self.current = {}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current[attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class PageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.records = [invoice("INV-FIRM"), invoice("INV-FINAL", "2026-09-18"),
                        invoice("INV-FRIENDLY", "2026-10-09")]
        self.write_data(self.records)
        quiet = patch.object(app.Handler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.server = app.ReminderServer(("127.0.0.1", 0), self.folder, TODAY)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def write_data(self, invoices, sent=None):
        (self.folder / "invoices.json").write_text(json.dumps(invoices), encoding="utf-8")
        (self.folder / "sent.json").write_text(json.dumps(sent or []), encoding="utf-8")

    def request(self, method="GET", path="/", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def get_forms(self):
        status, _, body = self.request()
        self.assertEqual(status, 200)
        return FormParser(body).forms

    def submit(self, fields, decision="approved"):
        return self.request("POST", "/decision", urlencode({**fields, "decision": decision}),
                            {"Content-Type": "application/x-www-form-urlencoded"})

    def test_page_shows_all_invoice_facts_and_messages(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        for text in ("Acme &amp; Sons", "INV-FIRM", "INV-FINAL", "INV-FRIENDLY", "EUR 1250.50",
                     "12 days", "24 days", "3 days", "Firm", "Final", "Friendly", "2026-09-30",
                     "Payment is outstanding", "A friendly reminder", "final payment reminder",
                     "No emails are sent", "Monday, 12 October 2026"):
            self.assertIn(text, body)
        self.assertEqual(len(FormParser(body).forms), 2)
        self.assertIn('href="#main"', body)
        self.assertIn('name="viewport"', body)

    def test_get_and_post_use_the_shared_planner(self):
        with patch.object(app, "build_plan", wraps=app.build_plan) as planner:
            forms = self.get_forms()
            self.assertEqual(self.submit(forms[0])[0], 303)
            self.assertEqual(planner.call_count, 2)
            planner.assert_called_with(self.records, [], TODAY)

    def test_approve_and_reject_persist_without_sending_or_changing_data(self):
        original = {path.name: path.read_bytes() for path in self.folder.iterdir()}
        forms = self.get_forms()
        for form, decision in zip(forms, ("approved", "rejected")):
            status, headers, _ = self.submit(form, decision)
            self.assertEqual(status, 303)
            self.assertEqual(headers["Location"], f"/?decision={decision}#reminder-{form['action_key']}")
        for _ in range(2):
            _, _, body = self.request()
            self.assertIn('class="decision-status approved">Approved', body)
            self.assertIn('class="decision-status rejected">Rejected', body)
            self.assertNotIn('class="decision-status pending"', body)
        self.assertEqual(len(self.server.decisions), 2)
        self.assertEqual({path.name: path.read_bytes() for path in self.folder.iterdir()}, original)

    def test_owner_can_change_a_decision(self):
        form = self.get_forms()[0]
        self.assertEqual(self.submit(form, "approved")[0], 303)
        self.assertEqual(self.submit(form, "rejected")[0], 303)
        self.assertEqual(self.server.decisions, {form["action_key"]: "rejected"})
        self.assertIn('class="decision-status rejected">Rejected', self.request()[2])

    def test_friendly_reminder_cannot_receive_a_decision(self):
        plan, _ = self.server.load_plan()
        friendly = next(action for action in plan["actions"] if action["step"] == "friendly")
        form = {"action_key": action_key(TODAY, friendly), "csrf_token": self.server.csrf_token}
        status, _, body = self.submit(form)
        self.assertEqual(status, 400)
        self.assertIn("does not need an approval", body)
        self.assertEqual(self.server.decisions, {})

    def test_stale_decisions_are_rejected_when_invoice_changes(self):
        cases = [invoice("INV-FINAL", "2026-09-18", amount=10),
                 invoice("INV-FINAL", "2026-09-18", paid_date=TODAY),
                 invoice("INV-FINAL", "2026-10-09"),
                 invoice("INV-FINAL", "2026-09-18", amount=0)]
        for changed in cases:
            with self.subTest(changed=changed):
                self.write_data(self.records)
                form = self.get_forms()[0]
                self.write_data([changed])
                status, _, body = self.submit(form)
                self.assertEqual(status, 409)
                self.assertIn("no longer in today", body)
                self.assertEqual(self.server.decisions, {})

    def test_stale_decisions_are_rejected_when_reminder_is_sent_or_date_changes(self):
        form = self.get_forms()[0]
        self.write_data(self.records, [{"invoice_id": "INV-FINAL", "step": "final"}])
        self.assertEqual(self.submit(form)[0], 409)
        self.write_data(self.records)
        self.server.today_override = "2026-10-13"
        self.assertEqual(self.submit(form)[0], 409)
        self.assertEqual(self.server.decisions, {})

    def test_recorded_decision_does_not_apply_to_changed_reminder(self):
        form = self.get_forms()[0]
        self.assertEqual(self.submit(form)[0], 303)
        changed = [dict(record, amount=999) for record in self.records]
        self.write_data(changed)
        body = self.request()[2]
        self.assertNotIn('class="decision-status approved"', body)
        self.assertIn('class="decision-status pending"', body)

    def test_bad_forms_do_not_record_decisions(self):
        form = self.get_forms()[0]
        for token in ("wrong", "expired", "非ASCII"):
            with self.subTest(token=token):
                self.assertEqual(self.submit({**form, "csrf_token": token})[0], 403)
        self.assertEqual(self.submit(form, "send")[0], 400)
        self.assertEqual(self.submit({**form, "action_key": "unknown"})[0], 409)
        self.assertEqual(self.submit({})[0], 400)
        for body, headers in (("invalid", {}),
                              (urlencode({**form, "decision": ["approved", "rejected"]}, doseq=True),
                               {"Content-Type": "application/x-www-form-urlencoded"})):
            self.assertEqual(self.request("POST", "/decision", body, headers)[0], 400)
        self.assertEqual(self.server.decisions, {})

    def test_empty_plan_has_clear_success_state(self):
        self.write_data([])
        status, _, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn("No reminders to review", body)
        self.assertIn("No reminders are due today", body)
        self.assertIn("All invoice data passed", body)
        self.assertEqual(FormParser(body).forms, [])

    def test_every_skip_reason_is_explained_in_plain_words(self):
        self.write_data([
            invoice("BAD-AMOUNT", amount=True), invoice("MISSING-DUE", due_date=None),
            invoice("BAD-DUE", due_date="2026-02-30"), invoice("BAD-PAID", paid_date=""),
            invoice("DUPLICATE"), invoice("DUPLICATE"),
        ])
        status, _, body = self.request()
        self.assertEqual(status, 200)
        for text in ("amount is missing or invalid", "due date is missing", "due date is invalid",
                     "payment date is invalid", "More than one invoice has this ID"):
            self.assertIn(text, body)
        for code in ("duplicate_id", "invalid_amount", "missing_due_date", "invalid_due_date", "invalid_paid_date"):
            self.assertNotIn(code, body)
        self.assertIn("No reminders can be planned", body)
        self.assertNotIn("all caught up", body)

    def test_data_errors_are_visible_and_app_recovers(self):
        for filename in ("invoices.json", "sent.json"):
            path = self.folder / filename
            original = path.read_bytes()
            for invalid in (None, b"{broken", b"{}", b"\xff"):
                with self.subTest(filename=filename, invalid=invalid):
                    if invalid is None:
                        path.unlink()
                    else:
                        path.write_bytes(invalid)
                    status, _, body = self.request()
                    self.assertEqual(status, 503)
                    self.assertIn("Today&#x27;s plan is unavailable", body)
                    self.assertIn("We couldn&#x27;t read", body)
                    self.assertIn('href="/">Try again', body)
                    path.write_bytes(original)
                    self.assertEqual(self.request()[0], 200)

    def test_data_error_during_post_does_not_record_decision(self):
        form = self.get_forms()[0]
        (self.folder / "invoices.json").unlink()
        self.assertEqual(self.submit(form)[0], 503)
        self.assertEqual(self.server.decisions, {})

    def test_invalid_today_has_a_visible_error(self):
        self.server.today_override = "2026-02-30"
        status, _, body = self.request()
        self.assertEqual(status, 503)
        self.assertIn("Today&#x27;s date is invalid", body)
        self.assertIn("Date unavailable", body)

    def test_today_defaults_to_real_calendar_date(self):
        self.server.today_override = None
        self.assertEqual(self.server.current_today(), date.today().isoformat())

    def test_invoice_text_cannot_inject_html(self):
        self.write_data([invoice('INV-"<test>', customer='<script>alert("x")</script> & Sons')])
        status, _, body = self.request()
        self.assertEqual(status, 200)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertIn("INV-&quot;&lt;test&gt;", body)
        self.assertEqual(len(FormParser(body).forms), 1)

    def test_stylesheet_and_unknown_routes(self):
        status, headers, body = self.request(path="/static/style.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertIn("max-width: 540px", body)
        self.assertIn(":focus-visible", body)
        self.assertIn("min-height: 44px", body)
        for method, path in (("GET", "/unknown"), ("POST", "/unknown"), ("GET", "/static/../app.py")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 404)


class StartupTests(unittest.TestCase):
    def test_main_reads_port_data_dir_and_today_from_environment(self):
        with patch.dict(os.environ, {"PORT": "8123", "DATA_DIR": "custom-data", "TODAY": TODAY}), \
             patch.object(app, "ReminderServer") as server_class, patch("builtins.print"):
            server_class.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(app.main(), 0)
            server_class.assert_called_once_with(("127.0.0.1", 8123), "custom-data", TODAY)
            server_class.return_value.server_close.assert_called_once()

    def test_main_uses_documented_defaults(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(app, "ReminderServer") as server_class, patch("builtins.print"):
            server_class.return_value.serve_forever.side_effect = KeyboardInterrupt
            self.assertEqual(app.main(), 0)
            server_class.assert_called_once_with(("127.0.0.1", 8000), "../data", None)

    def test_invalid_port_fails_clearly(self):
        for value in ("wrong", "0", "65536", "-1"):
            with self.subTest(value=value), patch.dict(os.environ, {"PORT": value}), \
                 patch("builtins.print") as output:
                self.assertEqual(app.main(), 1)
                self.assertIn("Cannot start the page", output.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
