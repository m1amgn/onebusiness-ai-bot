# Frontend

React, TypeScript and Vite form a Yarn workspace. Use `yarn`, never npm install.
Run root `yarn dev:web` for behavior and `yarn verify:frontend` for checks.

## Layers

ESLint boundaries enforce app -> features/components/lib, features ->
components/api/lib, components -> lib, api -> lib, lib -> nothing.
Co-located tests belong to the element they test. Unclassified source files fail.
`main.tsx` is the entry file category; `src/test` is the shared test setup element.

## Types and validation

TypeScript is strict, including indexed access. Use type-only imports.
Pydantic OpenAPI is the canonical wire schema. Run root `yarn api:schema`
to generate `src/api/generated` types and Zod schemas together.
Never edit generated files by hand. `yarn api:check` detects stale output.
Validate incoming data with Zod at its boundary.

## Components and styles

Use Tailwind and route conditional classes through `cn()` from `src/lib/cn`.
Name component prop types OwnProps and page prop types PageProps.
Use native buttons and links. Icon-only controls need an aria-label.
Event handlers start with handle. Tests are adjacent `*.test.ts(x)` files.
Prettier sorts Tailwind classes inside cn and clsx calls.

`func-style` enforces expressions in source files; use const arrow functions.
jsx-a11y's recommended rules and control-has-associated-label enforce native
controls, keyboard access and accessible names. Component tests also check icon
buttons by role/name because lint cannot inspect a custom icon component.

## Dashboard

`api/client` validates HTTP responses with generated Zod schemas; the API barrel
exports canonical wire types and schemas. Inject fetch for tests. Back-off and
microsecond timestamp comparison are pure helpers in lib. Keep task-aware UI in
features, since components cannot import api, even for types. App composes session
and tasks; the board is keyed by token so requests and state never mix accounts.
Transport tests inject EventSource, sleep and random. Component tests use StrictMode
and real timers; test/setup supplies browser layout/dialog stubs guarded for Node.
The lint regression runs in Node from src/test/eslintRules.test.ts.

The task hook owns the client, stream, page loader and serialized per-card writes.
Its synchronous state ref lets async chains see each action before React renders.
TaskBoardPage selects the desktop columns or phone tabs through useWideLayout.
Task-aware cards own deletion confirmation; generic dialog and expandable text
components stay in components. Desktop dragging uses a private MIME type; status
buttons remain available for keyboard and touch.

## Local infrastructure

Use root `yarn infra:up` and `yarn infra:stop`; direct Compose bypasses the guard.
The shared taskbot PostgreSQL/Redis stack belongs to the machine; leave it running.
Never delete its volumes. Isolated stacks require their own project and ports.
An integration test that writes data creates and drops its own PostgreSQL database.
It uses unique prefixes for Redis keys, channels, locks and queues. Tests never assume
exclusive infrastructure and never write to the development database.
API routes use /api; the dev proxy preserves that prefix. Public health is /health.
