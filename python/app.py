"""The owner's page (part 2). Standard library only, no install needed.

Run from inside python/:

    python app.py

Environment variables:
    PORT      port to listen on, default 8000
    DATA_DIR  folder with invoices.json and sent.json, default ../data
    TODAY     date as YYYY-MM-DD, default the real date
"""

import os
import secrets
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from page import action_key, render_page
from plan import build_plan, parse_date, read_json


STYLE_PATH = Path(__file__).resolve().parent / "static" / "style.css"
DATA_ERROR = (
    "We couldn't read today's invoice data. Check that invoices.json and sent.json "
    "are available and contain valid invoice and reminder lists, then try again."
)


class ReminderServer(ThreadingHTTPServer):
    """One shared, in-memory decision store for this app run."""

    daemon_threads = True

    def __init__(self, address, data_dir, today=None):
        self.data_dir = Path(data_dir)
        self.today_override = today
        self.decisions = {}
        self.decision_lock = Lock()
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)

    def current_today(self):
        return self.today_override or date.today().isoformat()

    def load_plan(self):
        """Read fresh data and call the same engine used by the command line."""
        invoices = read_json(self.data_dir / "invoices.json")
        sent = read_json(self.data_dir / "sent.json")
        plan = build_plan(invoices, sent, self.current_today())
        return plan, {invoice["id"]: invoice for invoice in invoices}

    def decision_snapshot(self):
        with self.decision_lock:
            return dict(self.decisions)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="text/html; charset=utf-8", location=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(data)

    def _error_page(self, status, message, title="Today's plan is unavailable"):
        self._send(status, render_page(self.server.current_today(), error=message, error_title=title))

    def _load_plan(self):
        try:
            parse_date(self.server.current_today())
        except ValueError:
            self._error_page(503, "Today's date is invalid. Use a real YYYY-MM-DD date for the TODAY setting.")
            return None
        try:
            return self.server.load_plan()
        except ValueError as exc:
            self.log_error("Unable to load reminder plan: %s", exc)
            self._error_page(503, DATA_ERROR)
            return None

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/static/style.css":
            self._send(200, STYLE_PATH.read_bytes(), "text/css; charset=utf-8")
            return
        if path.path != "/":
            self.send_error(404)
            return
        loaded = self._load_plan()
        if loaded is None:
            return
        plan, invoices = loaded
        notice = parse_qs(path.query).get("decision", [None])[0]
        self._send(200, render_page(plan["today"], plan, invoices,
                   self.server.decision_snapshot(), self.server.csrf_token, notice=notice))

    def do_POST(self):
        if urlsplit(self.path).path != "/decision":
            self.send_error(404)
            return
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
                raise ValueError("unsupported form encoding")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("invalid form size")
            fields = parse_qs(self.rfile.read(length).decode("utf-8"),
                              keep_blank_values=True, max_num_fields=8)
            if any(len(fields.get(field, [])) != 1 for field in ("csrf_token", "action_key", "decision")):
                raise ValueError("missing or duplicate form fields")
        except (ValueError, UnicodeError):
            self._error_page(400, "The decision form was invalid. Refresh the page and try again.",
                             "Decision not recorded")
            return

        if not secrets.compare_digest(fields["csrf_token"][0].encode("utf-8"),
                                      self.server.csrf_token.encode("utf-8")):
            self._error_page(403, "This form has expired. Refresh the page and try again.",
                             "Decision not recorded")
            return
        decision = fields["decision"][0]
        if decision not in ("approved", "rejected"):
            self._error_page(400, "Choose approve or reject, then try again.", "Decision not recorded")
            return
        loaded = self._load_plan()
        if loaded is None:
            return
        plan, _ = loaded
        key = fields["action_key"][0]
        action = next((item for item in plan["actions"] if action_key(plan["today"], item) == key), None)
        if action is None:
            self._error_page(409, "This reminder has changed or is no longer in today's plan. "
                             "Refresh the page and review the latest message.", "Review the latest plan")
            return
        if not action["needs_approval"]:
            self._error_page(400, "This friendly reminder does not need an approval decision.",
                             "Decision not recorded")
            return

        with self.server.decision_lock:
            self.server.decisions[key] = decision
        # Redirect after POST: refresh must never resubmit a decision.
        self._send(303, "", location=f"/?decision={decision}#reminder-{key}")


def main():
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        server = ReminderServer(("127.0.0.1", port),
                                os.environ.get("DATA_DIR", "../data"), os.environ.get("TODAY") or None)
    except (OSError, ValueError) as exc:
        print(f"Cannot start the page: {exc}", file=sys.stderr)
        return 1
    print(f"Serving on http://localhost:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
