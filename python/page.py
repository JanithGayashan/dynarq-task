"""Accessible, server-rendered views for the owner's daily reminder plan."""

import hashlib
import json
from html import escape

from messages import format_amount
from plan import parse_date


SKIP_REASONS = {
    "duplicate_id": "More than one invoice has this ID. Give each invoice a unique ID.",
    "invalid_amount": "The amount is missing or invalid. Enter a number greater than zero.",
    "missing_due_date": "The due date is missing. Add the invoice's due date.",
    "invalid_due_date": "The due date is invalid. Enter a real date in YYYY-MM-DD format.",
    "invalid_paid_date": "The payment date is invalid or in the future. Check the date, or leave it unset if unpaid.",
}


def action_key(today, action):
    """Tie a decision to the exact dated reminder the owner reviewed."""
    content = json.dumps({"today": today, "action": action}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _action_card(today, action, invoice, decisions, csrf_token):
    key = action_key(today, action)
    anchor = f"reminder-{key}"
    decision = decisions.get(key)
    step = action["step"].title()
    customer = escape(invoice["customer"])
    invoice_id = escape(action["invoice_id"])
    amount = escape(f"{invoice['currency']} {format_amount(invoice['amount'])}")
    due_date = escape(invoice["due_date"])

    if action["needs_approval"]:
        status = {"approved": "Approved", "rejected": "Rejected"}.get(decision, "Awaiting decision")
        state = decision or "pending"
        controls = f"""
        <form class="decision-form" method="post" action="/decision">
          <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
          <input type="hidden" name="action_key" value="{key}">
          <button class="button approve" type="submit" name="decision" value="approved"
            aria-label="Approve {step.lower()} reminder for {invoice_id}"
            {'disabled' if decision == 'approved' else ''}>Approve reminder</button>
          <button class="button reject" type="submit" name="decision" value="rejected"
            aria-label="Reject {step.lower()} reminder for {invoice_id}"
            {'disabled' if decision == 'rejected' else ''}>Reject reminder</button>
        </form>"""
        status_text = f'<span class="decision-status {state}">{status}</span>'
    else:
        controls = '<p class="ready-note">Ready to remind &middot; No approval needed</p>'
        status_text = '<span class="decision-status ready">Ready</span>'

    return f"""
    <article class="reminder-card" id="{anchor}" tabindex="-1" aria-labelledby="title-{key}">
      <div class="card-heading">
        <div><p class="invoice-id">{invoice_id}</p><h3 id="title-{key}">{customer}</h3></div>
        <span class="step-badge {action['step']}">{step}</span>
      </div>
      <dl class="invoice-facts">
        <div><dt>Amount</dt><dd>{amount}</dd></div>
        <div><dt>Days overdue</dt><dd>{action['days_overdue']} days</dd></div>
        <div><dt>Due date</dt><dd>{due_date}</dd></div>
      </dl>
      <details class="message-preview" open>
        <summary>Reminder message</summary>
        <div class="message-text">{escape(action['message'])}</div>
      </details>
      <div class="card-footer">{status_text}{controls}</div>
    </article>"""


def _section(title, section_id, description, actions, today, invoices, decisions, csrf_token):
    cards = "".join(_action_card(today, action, invoices[action["invoice_id"]], decisions, csrf_token)
                    for action in actions)
    return f"""
    <section class="reminder-section" aria-labelledby="{section_id}">
      <div class="section-heading"><h2 id="{section_id}">{title}</h2><span class="count">{len(actions)}</span></div>
      <p class="section-description">{description}</p>
      {cards}
    </section>"""


def render_page(today, plan=None, invoices=None, decisions=None, csrf_token="", notice=None,
                error=None, error_title="Today's plan is unavailable"):
    """Escape all data, and render complete plan, empty, and failure states."""
    decisions = decisions or {}
    try:
        parsed = parse_date(today)
        display_date = f"{parsed.strftime('%A')}, {parsed.day} {parsed.strftime('%B %Y')}"
        date_label = f'<time datetime="{escape(today)}">{escape(display_date)}</time>'
    except ValueError:
        date_label = "Date unavailable"

    flash = ""
    if notice in ("approved", "rejected"):
        flash = (f'<div class="notice" role="status">Reminder {notice}. Your decision has been recorded. '
                 'No email has been sent.</div>')

    if error:
        content = f"""
        <section class="empty-state error-state" role="alert" aria-labelledby="error-heading">
          <span class="state-icon" aria-hidden="true">!</span>
          <h2 id="error-heading">{escape(error_title)}</h2><p>{escape(error)}</p>
          <a class="button approve" href="/">Try again</a>
        </section>"""
    else:
        actions, skipped = plan["actions"], plan["skipped"]
        approvals = [action for action in actions if action["needs_approval"]]
        ready = [action for action in actions if not action["needs_approval"]]
        pending = sum(action_key(today, action) not in decisions for action in approvals)
        stats = ((len(actions), "Reminders today"), (pending, "Awaiting approval"),
                 (len(ready), "No approval needed"), (len(skipped), "Data issues"))
        summary = '<dl class="summary-grid" aria-label="Plan summary">' + "".join(
            f'<div class="summary-card"><dt>{label}</dt><dd>{value}</dd></div>' for value, label in stats
        ) + '</dl>'

        reminder_content = ""
        if approvals:
            reminder_content += _section("Approval reminders", "approval-heading",
                "Review each message. Your recorded decisions remain visible here.", approvals,
                today, invoices, decisions, csrf_token)
        if ready:
            reminder_content += _section("Ready to remind", "ready-heading",
                "These friendly reminders do not need your approval.", ready,
                today, invoices, decisions, csrf_token)
        if not actions:
            description = ("No reminders can be planned from the current data. Review the invoice issues below."
                           if skipped else "No reminders are due today. You are all caught up.")
            state_class = "empty-state error-state" if skipped else "empty-state"
            state_icon = "!" if skipped else "&#10003;"
            reminder_content = f"""
            <section class="{state_class}" aria-labelledby="empty-heading">
              <span class="state-icon" aria-hidden="true">{state_icon}</span>
              <h2 id="empty-heading">No reminders to review</h2><p>{description}</p>
            </section>"""

        issues = "".join(
            f'<li><h3>{escape(item["invoice_id"])}</h3><p>{SKIP_REASONS[item["reason"]]}</p></li>'
            for item in skipped
        )
        issue_content = (f'<ul class="issue-list">{issues}</ul>' if skipped else
                         '<p class="checks-passed">All invoice data passed the checks.</p>')
        issue_title = "Needs a data fix" if skipped else "Data checks"
        issue_description = ("These invoices are skipped until their data is corrected." if skipped else
                             "Every invoice was checked for missing or invalid data.")
        content = f"""
        {summary}<div class="plan-layout"><div class="reminders">{reminder_content}</div>
          <aside class="issues-panel" aria-labelledby="issues-heading">
            <div class="section-heading"><h2 id="issues-heading">{issue_title}</h2><span class="count">{len(skipped)}</span></div>
            <p class="section-description">{issue_description}</p>
            {issue_content}
          </aside>
        </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Today's plan | Payment reminders</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23234033'/%3E%3Ctext x='8' y='24' fill='white' font-size='24'%3ER%3C/text%3E%3C/svg%3E">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to today's plan</a>
  <header class="site-header"><div class="header-inner">
    <a class="brand" href="/" aria-label="Payment reminders home"><span class="brand-mark" aria-hidden="true">R</span><span>Payment reminders</span></a>
    <span class="local-label"><span class="status-dot" aria-hidden="true"></span>Owner workspace</span>
  </div></header>
  <main id="main" class="container" tabindex="-1">
    <div class="page-heading"><div><p class="eyebrow">YOUR DAILY PLAN</p><h1>Today's reminders</h1>
      <p class="intro">A clear view of who to remind, what to review, and what needs fixing.</p></div>
      <div class="date-label">{date_label}</div>
    </div>
    <p class="planning-note"><strong>Planning only.</strong> No emails are sent. Decisions are kept until the app restarts.</p>
    {flash}{content}
    <footer class="page-footer">A decision changes the plan's review status. It does not send a reminder.</footer>
  </main>
</body></html>"""
