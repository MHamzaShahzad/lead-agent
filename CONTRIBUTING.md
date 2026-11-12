# Contributing

Thanks for helping! Keep changes small and simple.

## Set up

```bash
git clone https://github.com/MHamzaShahzad/lead-agent.git && cd lead-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: py -3 -m venv .venv
./run.sh --demo        # sample data, fake WhatsApp/email, no Ollama or WAHA needed (http://127.0.0.1:8000)
```

Demo mode uses its own `demo.db` and never sends anything, so it is the easiest way to try a UI change.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Tests need no Ollama, WAHA or SMTP (all faked) and CI runs them on Python 3.11 and 3.12. Add a test for every behaviour
you change or add. Tests must never make real network calls.

## Style

- Plain Python 3.11+, standard library first, few dependencies (ask before adding one).
- The dashboard is server-rendered HTML in `app/ui.py`: no build step, no JS framework. Check phone width (375px) and desktop.
- Match the surrounding code. Short functions, comments only for the "why". No new linters or formatters in a feature PR.
- Safety features stay on by default: approval before sending, daily limit, sending hours, STOP / opt-out.

## Pull request flow

1. Fork and branch: `git checkout -b my-change`.
2. Make the change with tests; run the suite.
3. Open a PR using the template: what, why, how you tested, a screenshot for UI changes (phone + desktop).
4. A maintainer reviews. Squash-merge once CI is green.

For bigger ideas, open an issue first so we can agree on the approach.

## Never commit secrets or private data

- Never commit `.env`, `leads.db`, `demo.db`, `backups/` or `waha_sessions/` (all git-ignored), API keys, tokens, or real phone
  numbers and emails. Use example numbers (`447700900101`) and `example.com` addresses in tests and docs.
- Don't paste real conversations or leads into issues. Redact first.
- Found a security problem? Don't open a public issue: use GitHub's private "Report a vulnerability" on the Security tab.

Be kind in issues and reviews.
