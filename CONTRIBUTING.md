# Contributing

Thanks for helping! Keep it small and simple.

1. Fork, then create a branch: `git checkout -b my-change`.
2. Set up: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (Windows: `py -3 -m venv .venv`).
3. Run the tests: `.venv/bin/python -m pytest -q`. They need no Ollama and no WhatsApp (both are faked).
4. Add a test for any behaviour you change. Keep the dashboard server-rendered (no build step, no JS framework).
5. Open a pull request describing what and why, with a screenshot for UI changes (phone + desktop).

Rules:
- Never commit `.env`, `leads.db`, `backups/` or `waha_sessions/`, or real phone numbers in tests or docs.
- Safety features stay on by default: approval before sending, daily limit, sending hours, STOP/opt-out.
- Be kind in issues and reviews.
