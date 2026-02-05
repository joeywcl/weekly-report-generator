AI agent instructions (weekly-report-generator)

- Never add or modify secrets. Do not edit `.env` or any production server config.
- Do not log or print secret values.
- Keep diffs small and focused on the issue request.
- Prefer simple, readable code. Avoid refactors unrelated to the request.
- Update the web UI (`templates/index.html`) and generator scripts only when needed.
- After changes, ensure basic checks pass:
  - `python -m py_compile app.py generate_weekly_report_from_template.py fill_weekly_report.py`
