# Payment reminder assistant

A daily invoice plan with reminder messages, approval decisions, and data-quality checks.
Requires Python 3.11 or newer; standard library only, with no installation, API key, or internet needed.

## Run

From the repository root (the remaining commands run inside `python/`):
```sh
cd python
python -m unittest
python plan.py --invoices ../data/invoices.json --sent ../data/sent.json --today 2026-10-12 --out ../actions.json
python app.py
```

Verification: 55 rules, message, CLI, and HTTP tests; the sample date produces 5 reminders and 3 skipped invoices.
Open http://localhost:8000/; stop the app with Ctrl+C.
Environment: `PORT` defaults to `8000`, `DATA_DIR` to `../data`, and `TODAY` to the real local date.
For the sample page date in PowerShell, set `$env:TODAY = "2026-10-12"` before `python app.py`.

## Decisions

- `plan.py` is shared by the CLI and page; validation follows the required priority and IDs sort as plain text.
- Dates must be real `YYYY-MM-DD` dates; amounts must be positive finite JSON numbers, excluding booleans.
- Server-rendered HTML forms work without JavaScript; the page supports keyboard use and a 390-pixel layout.
- Template messages use invoice facts only; decimal amounts round to two places consistently in the page and email.
- Approvals stay in memory until restart, apply to the exact dated message, and can be changed; no emails are sent.
- `RESEARCH.md` cites only `sources/`; the current ECB rate is marked unavailable from those files.

With more time: persist decisions in SQLite, add owner authentication and an audit log, and integrate approved email delivery.
