# Coding-agent instructions

Read [DEVELOPMENT.md](DEVELOPMENT.md), [docs/acceptance.md](docs/acceptance.md), and the
relevant portion of [PLAN.md](PLAN.md) before changing the tracker. Later user decisions
are recorded in [docs/notifications.md](docs/notifications.md).

- Use feature branches. Inspect branch/upstream and dirty state before Git changes.
- Keep changes scoped; preserve credentials, profiles, `.env`, and unrelated work.
- Use `uv` for Python; Python 3.12 remains the supported minimum.
- Use `rg` for searches and `fd`/`find` for discovery. Use `rtk` wrappers when available.
- Add type hints and concise docstrings. Prefer a failing regression for bug fixes.
- Run relevant repo-native checks; `make check` is the full local gate.
- Plain `make` must only print help.
- Use isolated synthetic browser fixtures; never use the live account for automated tests.
- One owner per Chromium profile. Preserve interactive handoff and safe cancellation.
- Do not automate login challenges or add anti-detection modifications.
- Never expose raw tracking URLs, credentials, account data, or debug page content in
  public responses, fixtures, logs, commits, or support bundles.
- Never publish private registry/proxy/internal hostnames in source. Keep corporate
  certificate setup local and retain public upstream names in Docker/Compose files.
- Keep UI user-facing; do not expose implementation details unless they help a decision.
- Update user/developer docs and acceptance evidence with behavior changes. Distinguish
  synthetic checks, live account evidence, deployed code, and future requirements.
- Commit/push when authorized. Do not merge or rewrite published history without direction.
