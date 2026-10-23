# Contributor and agent guide

This file holds the rules for everyone who changes this repository: people and coding agents. `CLAUDE.md` imports it, so the rules live in one place.

## Project

AI Task Bot turns Telegram text and voice messages into tasks. A worker transcribes voice messages through Redis queues, PostgreSQL stores tasks and users, and a web dashboard shows the tasks in real time. The bot, the worker and the API (FastAPI) are written in Python; the dashboard is a React application written in TypeScript. `README.md` explains setup; `docs/plans/` holds the design of each part.

## Commands

- Python: `uv`. Never install packages with bare `pip`.
- TypeScript: `yarn`. Never run `npm install`.

## General code rules

- Code, comments, documentation, design notes and commit messages are written in English.
- Choose the simplest design that meets the requirement. An abstraction earns its place by a second real use or by a test seam; do not add one for a hypothetical case.
- SOLID: a module, class or function has one responsibility; new behaviour extends through an interface instead of a new branch in an existing flow. Domain and application code depend on interfaces; adapters wrap the Telegram, transcription, Redis and database clients.
- DRY: each concept has one canonical implementation. Reuse it before writing a new helper. Data shared across the language boundary has one schema, and the other side's types are generated from it, never written by hand.
- Every external input is validated at the boundary where it enters: Telegram updates, HTTP requests, queue payloads, real-time events and environment variables.
- Prefer early returns to nested conditions.
- Names describe what a thing is or does.
- An implementation file stays under about 700 lines. Extract a cohesive responsibility instead of growing a file past that.
- Code passes typecheck and lint. A suppressed rule carries a comment that says why.

## Python

- Type hints on every function. The type checker runs in strict mode; `Any` needs a comment that says why.
- Ruff lints and formats.
- Pydantic models validate every boundary; settings come from environment variables through a settings model.
- Interfaces (ports) are `typing.Protocol` classes in the domain layer. Adapters implement them in the infrastructure layer. One composition root wires adapters to ports.
- I/O is asynchronous.
- Tests use pytest.

## TypeScript

- TypeScript runs in strict mode.
- Zod validates data that enters the dashboard at runtime.
- Type-only imports use `import type`.
- Functions are `const` arrow functions with explicit types where inference does not give them.
- Event handlers start with `handle`: `handleClick`, `handleKeyDown`.

### React

- Style with Tailwind classes. Route conditional classes through `cn()`.
- Component prop types are named `OwnProps`; page prop types are named `PageProps`.
- Use native interactive elements (`button`, `a`). A custom interactive element gets a role, `tabIndex={0}` and keyboard handlers beside pointer handlers; an icon-only control gets an `aria-label`.

## Configuration and secrets

- Configuration comes from environment variables and is validated at startup. `.env.example` lists every variable with a placeholder. Real values live only in an untracked `.env`.
- Never commit tokens, keys or real chat content.
- A live run uses separate test credentials, and the README says which integrations were verified live.

## Module documentation

- Discovery: before working in a directory that has its own `AGENTS.md`, read it.
- Maintenance: a change that alters a module's responsibilities, entry points or constraints updates that module's `AGENTS.md` in the same commit.

## Tests and checks

- A behaviour change comes with a test.
- A fixed defect comes with a regression test that fails without the fix.
- Integration tests run against real PostgreSQL and Redis in containers; only Telegram and the transcription provider are replaced by test doubles.
- Check behaviour on the dev server. Use a production build to catch type and lint errors.
- The Docker Compose stack is the delivered artifact: run it once on the final commit, not for every check.

## Commits

- One commit per atomic stage that can be reverted on its own.
- The message says what changed, why, how, and any deviation from the design.
- Commit messages carry no tool attribution: no `Co-Authored-By` trailer and no "Generated with" line.

## How this repository is built

The author works with two coding agents, Claude Code and Codex, under a design, design review, implementation and code review cycle. Each design is committed in `docs/plans/` and opens with a status line. Working diaries and coordination records stay local and are not part of the repository. Contributors do not need any of this tooling: every command above runs with `uv`, `yarn` and `docker compose`.
