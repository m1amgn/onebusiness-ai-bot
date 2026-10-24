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

## Local infrastructure

Use root `yarn infra:up` and `yarn infra:stop`; direct Compose bypasses the guard.
The shared taskbot PostgreSQL/Redis stack belongs to the machine; leave it running.
Never delete its volumes. Isolated stacks require their own project and ports.
Writing integration tests create and drop a private PostgreSQL database and use
unique prefixes for Redis keys, channels, locks and queues. Tests never assume
exclusive infrastructure and never write to the development database.
API routes use /api; the dev proxy preserves that prefix. Public health is /health.
