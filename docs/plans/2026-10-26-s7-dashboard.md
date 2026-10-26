# S7 — Dashboard

> **STATUS 2026-10-26: ACTIVE.** Stage design, round 3 (design gate round 1: G1 frontier-based resync, G2 request epochs; round 2: G4 one board frontier).

Stage S7 of the umbrella design `docs/plans/2026-10-23-umbrella-assessment.md` (umbrella section 5, S7), read with its amendments 1 and 4 (umbrella sections 12 and 15), the S6 design `docs/plans/2026-10-26-s6-api.md` (3.3–3.10 and section 9, "Guidance for S7") and the S1 design `docs/plans/2026-10-24-s1-skeleton.md`. The umbrella fixes the scope; this document fixes how S7 delivers it, records the owner's decisions for this stage, and lists where S7 departs from the umbrella's and S6's text (section 9, rows K1–K6). "Umbrella x.y" names a section of the umbrella design; a bare number names a section of this document. The implementation steps are S7.1 to S7.4; the acceptance criteria are C1–C13.

## 1. Problem

The API (S6) serves the user's tasks page by page, changes and deletes them, and streams task events over Server-Sent Events (SSE). The dashboard does not exist: `frontend/src/app/App.tsx` renders one heading. S7 builds the React application that reads the personal token from the `/dashboard` link, shows the tasks on a Kanban board, lets the user move and delete them, and keeps the board current without a reload, on a phone and on a desktop. Every wire type and schema comes from the generated client (`frontend/src/api/generated/`), never from hand-written copies (umbrella section 4).

## 2. Owner decisions

Taken on 2026-10-26, after exploration (`.tasks/artifacts/2026-10-26-s7/exploration-{A-contract,B-architecture,C-tooling}.md`). The visual choices were made on an interactive mockup in Orca's browser, at the owner's request («Можешь накидать в браузере и показать с вариантами для выбора?»): view (Kanban or list), status control (arrow buttons, a select, or a three-part switch), drag-and-drop (on or off), source text (a tooltip or a collapsible block) and theme. The owner's selections were read from the page. The owner's words are quoted.

| # | Decision | Owner's answer |
|---|---|---|
| S7-D1 | Look and interaction. A Kanban board with three columns (Pending, In Progress, Completed); on a phone one column at a time behind three tabs. Each card has arrow buttons to the neighbouring statuses ("Pending" with a left chevron, "Completed" with a right chevron), the forward button coloured by its target status. On a desktop a card can also be dragged to another column with the browser's native drag-and-drop (no library); the buttons stay for touch and keyboard. The description is clamped to three lines with a "Show more" / "Show less" toggle when it is longer. The original message text is in a collapsible "Source message" block on each card (K6). Dark theme only, no switch. Palette (3.9): a warm graphite neutral; colour only for status (amber Pending, cyan In Progress, jade Completed) and red only for deletion; no indigo, gradients or shadows. Typeface Golos Text. | mockup selections: Kanban, arrow buttons, drag-and-drop on, collapsible source block; «Только один момент: description обрезается, его надо бы тоже как-то открывать и сворачивать»; «И тему темную хочу.»; «Только цвета не нейрослоповые пожалуйста, посмотри в интернете как лучше сделать по цветам»; on the restyled mockup «Пойдет. Всегда темная. Перетаскивание какое рекомендуешь» (the recommendation: native drag-and-drop) |
| S7-D2 | `yarn dev:seed` gains a mode that keeps the dashboard token: it creates the task and publishes `task.created` without issuing a new token, so an open dashboard stays signed in and shows the task arrive. Today every seed run rotates the token and publishes `auth.revoked`, which signs the open dashboard out right after the task appears. A backend change of S7 (K2). | asked twice (first: «Не понимаю вопроса»); on the restatement with a diagram «Да» |
| S7-D3 | The interface is English throughout, with the statuses as the assignment names them. Task text stays in the language it was written or dictated in. | «1» |
| S7-D4 | The repository guide's "const arrow functions" and "native interactive elements" rules become lint rules: `func-style` (expression) and `eslint-plugin-jsx-a11y` (flat recommended plus `control-has-associated-label`). The plugin's missing ESLint 10 peer range (a yarn warning) is accepted. This answers the open question of umbrella amendment 1. | «1» |
| S7-D5 | A task is always shown in the column of its current status. A card that moves (by the user or from another tab) into a column that is not loaded down to its position appears at the bottom of that column, above "Load more"; a later page sorts it into place. S6's guidance said such a task is ignored (K1). | «1» |
| S7-D6 | Status changes are optimistic: the card moves at once; when the server refuses or cannot be reached it moves back and shows "Couldn't change the status. Try again." Deletion waits for the server, because it cannot be undone. | «1» |
| S7-D7 | Architecture: approach 2 of section 4, pure modules (client, stream loop, board state) wired by one hook. | «2» |

## 3. Design

### 3.1 Files

```
frontend/
  package.json                     + @fontsource-variable/golos-text; dev: @testing-library/user-event,
                                   eslint-plugin-jsx-a11y
  eslint.config.js                 + func-style, jsx-a11y (3.10)
  AGENTS.md                        dashboard modules, test seams, lint rules (3.10)
  index.html                       title "Tasks"; <meta name="color-scheme" content="dark">
  src/
    index.css                      Tailwind import, @theme tokens (3.9), base colours
    main.tsx                       + import '@fontsource-variable/golos-text'
    app/App.tsx                    SessionGate render prop -> TaskBoardPage keyed by token (3.3)
    app/App.test.tsx               rewritten: signed-out and signed-in shells
    app/replay.test.tsx            owner acceptance replay (section 7)
    lib/
      backoff.ts                   reconnectDelayMs(attempt, random)
      timestamps.ts                compareTimestamps(a, b)
    api/
      index.ts                     barrel: generated types and the Zod schemas S7 uses
      client.ts                    createApiClient: list, change status, delete, ticket (3.2)
      eventStream.ts               createEventStream: the connection loop (3.4)
    features/
      session/
        sessionStore.ts            token capture, storage, cross-tab updates (3.3)
        SessionGate.tsx, SignedOut.tsx
      tasks/
        taskState.ts               pure board state and rules (3.5)
        loadPages.ts               page chains for the first load, "Load more" and the resync (3.6)
        useTaskBoard.ts            hook: state, client, stream, moves, deletes (3.7)
        useWideLayout.ts           matchMedia('(min-width: 768px)') through useSyncExternalStore
        TaskBoardPage.tsx, BoardColumns.tsx, PhoneTabs.tsx, TaskCard.tsx,
        StatusButtons.tsx, ConnectionStatus.tsx
    components/
      ConfirmDialog.tsx            native <dialog>
      ExpandableText.tsx           three-line clamp with a measured toggle
      ChevronIcon.tsx              inline SVG (left, right)
    test/setup.ts                  + stubs and StrictMode for tests (3.11)
    test/eslintRules.test.ts       lint rule regression test (C1, node environment)
backend/src/taskbot/entrypoints/seed/{__init__.py,__main__.py}   --keep-token (3.8)
backend/tests/integration/test_seed.py, backend/tests/unit/test_seed_cli.py   (3.8)
backend/AGENTS.md                  "Seed entry point" section (3.8)
README.md                          dashboard section; seed flag; text that S7 makes false (3.8, K6)
```

Every new file stays under 300 lines. Co-located tests sit beside each module (`*.test.ts(x)`), as `frontend/AGENTS.md` requires.

Layer facts the mapping follows (executed, exploration B section 1; premise lens): `components` cannot import `api` (even types), so every task-aware component lives in `features/tasks` and `components` holds generic parts only; `app` cannot import `api`; `main.tsx` imports only `app` and `lib` (a package import such as the font is not an element and passes); a feature test cannot import `src/test/*`, so each test declares its own fakes; `api/generated/index.ts` re-exports types only, so `api/index.ts` re-exports the Zod schemas from `generated/zod.gen`. The two features do not import each other: `App` composes them (3.3), and the replay test that needs both lives in `app`.

### 3.2 API client (`api/client.ts`)

`createApiClient({ token, fetchImpl = fetch })` returns `listTasks({ limit, cursor, status }, signal)`, `changeStatus(id, status, signal)`, `deleteTask(id, signal)` and `issueTicket(signal)`. The client is built for one token (3.3) and sends `Authorization: Bearer <token>` with every request to the page's own origin (`/api/...`: the Vite proxy in development, nginx in S8).

- Every response body is validated with the generated schema: `zTaskPage`, `zTaskView`, `zRealtimeTicket`. `DELETE` answers `204` with no body; the client does not read it (`zDeleteTaskResponse` is `z.void()`).
- Every failure is one class, `ApiRequestError { status, detail }`: `status` 0 for a network error, a timeout or an invalid body; `detail` from `zApiError` when the body parses. The error carries no token. Callers branch on `status`: `401` (token), `404` (gone), `5xx` and `0` (retry or roll back).
- The ticket request times out after 10 s (`AbortSignal.any([signal, AbortSignal.timeout(10_000)])`); a timeout is status 0.

### 3.3 Session (`features/session`) and the board's lifetime

`createSessionStore({ storage, location, history })` returns `getSnapshot`, `subscribe`, `capture()` and `clearIfCurrent(token)`.

- `capture()` reads `#token=<token>` from the fragment (umbrella 3.6) once per page load: the parsed value is memoised at module level, so StrictMode's double call of the `useState` initialiser that builds the store, and a store instance React discards, cannot lose a token even when storage is blocked. It stores the token under one `localStorage` key and removes the fragment with `history.replaceState(null, '', pathname + search)`. A fragment token replaces the stored one (a new `/dashboard` link wins).
- `subscribe` also listens to the `storage` event: a changed or removed token key, and `key === null` (storage cleared), update the snapshot, so another tab's new token or sign-out reaches this tab. With blocked storage the token lives in memory and there are no cross-tab updates.
- `clearIfCurrent(token)` removes the stored token only if it still equals `token` (a read, a compare and a remove; not atomic across tabs, which is acceptable for one user's tabs). A `401` signs out through this call; when another tab already stored a newer token, the store keeps it.
- `SessionGate` renders `children(token)` when a token exists and `SignedOut` otherwise. `App` renders `<SessionGate>{(token) => <TaskBoardPage key={token} token={token} onUnauthorized={...clearIfCurrent} />}</SessionGate>`. **The board is keyed by its token**: a token change from any source unmounts the old board (its effects abort every request, close the stream and drop its state) and mounts a new one for the new token. Every request of a board uses the token it was mounted with, so one board never mixes two users' tasks (adversarial lens F1).
- `SignedOut` has two texts: after a rejected token, "This link no longer works." followed by the API's `401` text "Send /dashboard to the bot for a new link"; with no token at all, "Open your dashboard from the bot: send /dashboard to get a link."

### 3.4 Connection loop (`api/eventStream.ts`)

`createEventStream({ issueTicket, openSource, sleep, random, onOpen, onEvent, onStatus, onUnauthorized })` returns `start()`, `stop()` and `reconnect()`. `openSource(url)` is `(url) => new EventSource(url)` in production and a fake in tests (jsdom and Node have no `EventSource`, exploration C 2). The loop knows nothing about tasks. Terms: an **attempt** is one pass through steps 1–3; a **connection** is an attempt that reached `onopen`, numbered by a **generation** counter that increases on every `onopen`.

1. `issueTicket()`. A `401` calls `onUnauthorized()` and ends the loop. Any other failure, including the 10 s timeout, goes to step 5.
2. `openSource('/api/events?ticket=<ticket>')`. If neither `onopen` nor `onerror` fires within 15 s, the loop treats it as an error (step 5).
3. `onopen` is the confirmed subscription (S6 3.9; `EventSource` does not expose the `: connected` comment): generation + 1, `onStatus('live')`, `onOpen(generation)`. A 30 s timer starts; if the connection is still open when it fires, the attempt counter resets to 0.
4. `onmessage`: parse `JSON.parse(data)` with `zServerEvent` (never a member schema alone: the `type` default applies only there, exploration A). An invalid frame is logged and ignored. `auth.revoked` goes to step 5 without signing out (S6 section 9, Z5); the next ticket request decides.
5. On `onerror`, `auth.revoked`, a failed or timed-out ticket, the open timeout, or `reconnect()`: `source.close()` first (the browser would otherwise reconnect by itself to the same URL with the spent ticket, exploration B 2.3), clear the 30 s timer, `onStatus('reconnecting')`, attempt + 1, wait `reconnectDelayMs(attempt, random)`, then step 1.

`onerror` carries no HTTP status, so a stream `401`, a `503` and a dropped connection are treated alike; `Retry-After: 5` is invisible to the page and is covered by the back-off. `reconnectDelayMs(attempt, random) = max(500, random() × min(30 000, 1 000 × 2^(attempt − 1)))` ms: exponential with full jitter, capped at 30 s (umbrella 3.6), with a 500 ms floor. Because the counter resets only after 30 s of an open connection, a stream that opens and closes at once backs off like any other failure (adversarial lens F3). `reconnect()` is how the board asks for a new connection (a failed resync, the browser's `online` event); it never skips the wait. `stop()` closes the source, clears every timer and aborts a pending ticket request. Before the first `onopen` the status is `connecting`.

### 3.5 Board state and rules (`features/tasks/taskState.ts`)

Pure functions over one state value; no React, no I/O. A prototype of the merge rules (85 lines, 16 passing tests) is in exploration B section 2.4.

```
type TaskKey = { inbound_message_id: number; position: number }
type ColumnLoad = { cursor: string | null; complete: boolean; edge: TaskKey | null; loading: boolean; error: boolean }
type BoardState = {
  byId: Map<number, TaskView>                  // every task known, from pages and events
  deleted: Set<number>                         // tombstones for the page's life
  columns: Record<TaskStatus, ColumnLoad>
  moves: Map<number, { to: TaskStatus; inFlight: boolean }>   // optimistic status (3.7)
  deleting: Set<number>                        // DELETE in flight
  cardErrors: Map<number, 'move' | 'delete'>   // the line under a card
  arrived: Set<number>                         // ids of live task.created, for the entry mark
  epoch: number                                 // list-request epoch (3.6)
  resync: { epoch: number; frontier: TaskKey | null; log: BoardAction[] } | null
}
type BoardAction = ServerEvent | { type: 'local.updated'; task: TaskView } | { type: 'local.removed'; id: number }
```

- **Order**: `inbound_message_id DESC, position ASC` (S6 3.5). The cursor is opaque; the state never parses it.
- **Merge by id**: an incoming task replaces the stored one only when its `updated_at` is strictly later, compared by `compareTimestamps` (`lib/timestamps.ts`): the part before the fraction, then the fraction right-padded to six digits. `Date.parse` and string comparison both mis-order the backend's values (it prints no fraction when the microseconds are zero and bumps a tie by 1 µs; exploration A, B, premise lens).
- **Tombstones**: `task.deleted`, a `204` and a `404` on `PATCH` or `DELETE` remove the task and add its id to `deleted`; a later event or page row with that id is dropped.
- **Displayed status** is the optimistic target in `moves` if there is one, else the stored status.
- **Visibility (S7-D5)**: a column shows every known, non-deleted task whose displayed status is that column, sorted by the order above. There is no range filter: a task that arrives by event, or moves, below the column's loaded part shows at the bottom of the column and is sorted into place when a later page brings its neighbours (K1). Cursors are unaffected: a page continues from the last row the server returned, so an extra card at the bottom neither skips nor repeats a row (adversarial lens, checked).
- **Shown** count of a column = the number of tasks it shows. The phone tabs display it, with `+` when the column is not complete ("50+").
- **Arrival mark**: only a live `task.created` for an id not yet known adds the id to `arrived`; pages and the resync never do. The card renders the mark once and the id is removed when its animation ends.

### 3.6 First load, "Load more" and the resync (`features/tasks/loadPages.ts`)

- **Keys and the frontier.** A task's key is `(inbound_message_id, position)`; "older" means later in the order of 3.5. A column's `edge` is the key of the last row its pages returned. When a connection is lost (and at the first `onopen`), the board captures one **frontier**: the oldest of every column's `edge` and the key of every card on the board, including cards that arrived by event below a column's loaded part. The frontier is kept across failed attempts until a resync completes. A board with no card and no loaded page has no frontier.
- **First load**: on mount, independently of the stream, each column requests `GET /api/tasks?status=<column>&limit=50` (three requests in parallel), so the board appears even when the stream cannot open (a `503` while Redis is down). The stream starts at the same time.
- **Request epochs** (gate G2). Every list request (first load, "Load more", resync chain) records the board's `epoch` when it is sent, and its result is applied only if `epoch` is unchanged. A resync start increments `epoch` and aborts every list request in flight; whatever happens to a request, applied, discarded or failed, it clears the `loading` flag it set. So a first-load or "Load more" reply that arrives after a resync began can neither bring back a task deleted before the subscription nor move a cursor backwards. "Load more" also checks that the column's cursor still equals the one it sent.
- **Resync on every `onopen`**, the first included, because the first load ran before the subscription existed and may have missed a change in between. Every column's chain requests `limit=200` pages from the start, following `next_cursor`, until a returned row's key is at or older than the board's frontier, or `next_cursor` is `null`; without a frontier each column loads one page of 50. Because the frontier is common to all columns, every chain covers the key of every card the board showed, so a card that moved offline to another column is found in its new column however many newer tasks that column has (gate G4), and a count of shown cards cannot stop short when tasks were created offline (gate G1). The price: a column reloads every task newer than the board's oldest card, which can be more than it showed (section 10). There is no upper cap. The three columns run in parallel. At start this means three first-load requests and three resync requests.
- **Race with events** (exploration B section 4.2): the refetch starts only after `onopen`, so a change committed before the subscription is in the pages and one committed after it arrives as an event. While a resync runs, every event and every local result is applied to the live state and also appended to `resync.log`.
- **Completion: merge, prune, replay.** When all three chains finish: (1) the page rows merge into `byId` by the rule of 3.5 (a task that moved offline gets its new status here); (2) a stored task is removed when its key is at or newer than the frontier, it is in none of the three columns' pages, it has no entry in `moves`, and the log does not mention it: the server no longer has it, so it was deleted; (3) the log is replayed; (4) each column's `cursor`, `complete` and `edge` come from its chain's last response. Every card shown before the disconnect lies at or newer than the frontier, so each one is either refreshed from a page, kept because a move or the log owns it, or removed because the server no longer has it; none is kept unchecked and none that still exists is dropped (S7-D5). "Load more" is disabled while a resync runs.
- **Generations**: a resync carries the epoch it started with; its results are applied only when that is still the board's epoch. A new `onopen` starts a new resync (new epoch), which aborts the chains of the old one. A failed chain keeps the live state and the frontier and calls `stream.reconnect()`; a `401` goes to `onUnauthorized`.
- **Load more**: one request per column at a time with that column's cursor, `limit=50`, under the epoch and cursor checks above.

### 3.7 The hook, moves and deletes (`features/tasks/useTaskBoard.ts`)

`useTaskBoard(token, onUnauthorized)` owns a `useReducer` over the state of 3.5, a client for `token` (3.2) and the stream (3.4), and returns the columns, the per-column flags, the connection status and the handlers `handleMove(id, to)`, `handleDelete(id)`, `handleLoadMore(column)` and `handleRetry(column)`. It listens to the window's `online` event and calls `stream.reconnect()`.

- **StrictMode**: `main.tsx` renders in `<StrictMode>`, which runs every effect twice in development. The effects that start the stream and the first load defer their start by one timer tick and clear that timer in the cleanup, and every async step checks an abort signal; a naive effect leaks a second `EventSource` (executed, exploration B section 5 and premise lens: one ticket and one source with the deferral, two tickets without it).
- **Move (S7-D6)**: `handleMove` sets `moves[id].to` (the card moves at once). At most one `PATCH` per card is in flight. If none is, it sends `PATCH {status: to}`; a click while one is in flight only updates `to`. When the response arrives: on `200` the task merges (3.5); if `moves[id].to` still differs from the merged status, the next `PATCH` goes out with the newest target; otherwise the move entry is dropped. So the user's last click wins whatever order the server commits in (adversarial lens F5). On a failure other than `404` the move entry is dropped (the card returns to its stored status) and `cardErrors[id] = 'move'` shows "Couldn't change the status. Try again." until the next move of that card. `404` removes the card (3.5). `401` calls `onUnauthorized`.
- **Delete**: the card's delete button (icon, `aria-label="Delete task"`) opens `ConfirmDialog`: title "Delete this task?", the task title, "This cannot be undone. The message stays in your Telegram chat.", buttons "Cancel" and "Delete". On "Delete" the id joins `deleting` (the card stays, dimmed, with its controls disabled) and `DELETE` is sent; `204` or `404` removes the card; another failure keeps it and sets `cardErrors[id] = 'delete'`: "Couldn't delete the task. Try again." A card in `deleting` cannot be moved or dragged.
- **Layout**: `useWideLayout()` reads `matchMedia('(min-width: 768px)')` through `useSyncExternalStore`. Wide renders `BoardColumns` (three columns, cards draggable); narrow renders `PhoneTabs` (`role="tablist"`, one column, no dragging). One rendering at a time, so tests assert what is in the DOM.
- **Drag-and-drop (wide only)**: `dragstart` sets `dataTransfer` type `application/x-taskbot-task` to the task id; a column accepts `dragover` only for that type; `drop` parses the id and calls `handleMove(id, column)` only when the id is a known, non-deleted, not-deleting task whose displayed status differs from the column. Dragged plain text is ignored (F9).
- **Header**: "Tasks" and the connection status in words: "Connecting…", "Live" or "Reconnecting…".

### 3.8 Seed mode that keeps the token (S7-D2)

`yarn --silent dev:seed --keep-token <telegram_user_id> [text …]` (either order works through yarn 1 and argparse; premise lens).

1. `RegisterOrGetUser` gives the internal user id (an upsert: an unknown Telegram id creates the user row, as the plain seed does).
2. `users.get_token_version(user_id)` in a unit of work. `0` means the user never had a link: the command prints "This user has no dashboard link yet; run without --keep-token first.", creates no task, publishes nothing and exits with status 2.
3. Otherwise it runs `CreateTasksFromMessage`, publishes its `task.created` events, and prints the titles, the events channel and "Token unchanged; open dashboards stay signed in." A duplicate message (time-based ids make it rare) prints "Message already seeded." and publishes nothing; the channel comes from step 1's user id.

The plain seed is unchanged. The kept-token path is a second function `seed_keep_token(ports, telegram_user_id, text, message_id) -> KeptTokenSeedResult(user_id, titles, duplicate, events)`; `SeedResult` keeps its required `token` and `link`, so the existing `test_seed.py` assertions and strict mypy hold (premise lens). `SeedPorts` gains the `RegisterOrGetUser` it already builds and the unit-of-work factory. `__main__.py` adds the flag and calls one function or the other; `run()` keeps its signature for the plain path and `test_seed_cli.py`'s four-argument fakes, and the flag path gets its own `run_keep_token`. Touched: `entrypoints/seed/__init__.py`, `__main__.py`, `tests/integration/test_seed.py`, `tests/unit/test_seed_cli.py` (new cases only), `backend/AGENTS.md` ("Seed entry point"), README (command table line 52 and lines 74–80 "every run rotates", "the dashboard arrives later"; lines 7–9 "frontend is a placeholder"; line 296; line 310 "tooltip", K6). No API route, schema, Dockerfile or Compose file.

### 3.9 Look (S7-D1)

Colour tokens are Radix Colors 3.0 dark scales written as Tailwind 4 `@theme` variables in `index.css` beside the existing `@import 'tailwindcss'` (no runtime dependency): neutral `sand` (page `#191918`, column well `#111110`, card `#222221`, hover `#2a2a28`, borders `#3b3a37` and `#494844`, strong border `#62605b`, text `#eeeeec`, secondary text and icons `#b5b3ad`); status `amber` (`#ffc53d`, text `#ffca16`), `cyan` (`#00a2c7`, `#4ccce6`), `jade` (`#29a383`, `#1fd8a4`); deletion `tomato` (`#e54d2e`, `#ff977d`). Every text and icon colour is at least 4.5:1 on its surface; `#62605b` is a border colour only (2.5:1 on a card; premise lens). Tailwind 4 emits only token classes it finds written in full, so status classes come from a fixed map, never `bg-${status}`. Depth comes from lighter surfaces and 1 px borders, never from shadows; no gradients. The typeface is Golos Text, family `Golos Text Variable`, self-hosted through `@fontsource-variable/golos-text` (5.3, Cyrillic and Latin subsets) imported once in `main.tsx`, so the page makes no request to a font service. Chevrons are inline SVG (`ChevronIcon`), not the `◀ ▶` characters, which some phones draw as colour emoji.

A newly arrived card (3.5) shows a 3 px amber left border that fades to the normal border in 1.8 s, a CSS transition on `border-color`, disabled under `prefers-reduced-motion`. Focus is visible on every control (`focus-visible` ring in the text colour).

Layout: at 768 px and wider the three columns sit side by side; below that, tabs "Pending", "In Progress", "Completed" with shown counts switch one column.

`ExpandableText` clamps the description to three lines (`line-clamp-3`) and shows the toggle only when the text overflows: it measures `scrollHeight > clientHeight` after mount and in a `ResizeObserver` callback; the toggle carries `aria-expanded` and `aria-controls`. The "Source message" block is a native `<details>` with a `<summary>`.

States and their copy: loading (three column skeletons, `aria-busy`); empty columns "No pending tasks.", "No tasks in progress.", "No completed tasks."; a column whose load failed "Couldn't load tasks." with a "Retry" button; the two signed-out texts of 3.3.

### 3.10 Lint and guide (S7-D4)

`eslint.config.js` adds `func-style: ['error', 'expression']` for `src/**/*.{ts,tsx}` and, in the `src/**/*.tsx` block, `jsxA11y.flatConfigs.recommended` plus `'jsx-a11y/control-has-associated-label': 'error'` (off in `recommended`; it catches an icon-only `<button><svg/></button>`). An icon inside a custom component is invisible to the rule, so the component tests find every control by role and accessible name. `frontend/AGENTS.md` records the module map of 3.1, the test seams (injected `fetch`, `EventSource`, `sleep`, `random`; the board keyed by token) and that these two guide rules are now lint-enforced.

### 3.11 Test setup

`src/test/setup.ts` adds `configure({ reactStrictMode: true })` from `@testing-library/react`, so component tests run effects twice like the browser in development; prototype stubs for `HTMLDialogElement.prototype.showModal`, `show` and `close`; a `matchMedia` stub whose width a test sets (wide by default); and a `ResizeObserver` stub. jsdom lacks all of them (exploration C 2, premise lens). Drag tests fire `dragStart`, `dragOver` and `drop` with a stub `dataTransfer` (works in jsdom, premise lens). User interactions use `@testing-library/user-event` 14 with real timers; the back-off is tested as a pure function and the loop with an injected `sleep`, because Testing Library hangs under `vi.useFakeTimers()` (exploration C 2).

## 4. Approaches considered

1. **Minimal**: the loop, the board rules and the requests inside one hook. Fewest files; the rules are testable only through rendering.
2. **Pure modules** (chosen, S7-D7): client, stream loop and board state as plain modules with injected seams, wired by one hook; the merge, resync and reconnect rules have fast unit tests (C2–C7).
3. **External store** (`useSyncExternalStore` over a store object): earns its place when several component trees share the data; this page has one.

Within approach 2, the loop could live in `features/tasks` instead of `api`; it lost because the loop is transport (tickets, `EventSource`, back-off) and the feature passes everything task-specific as callbacks.

## 5. Stages

| Step | Content | Criteria |
|---|---|---|
| S7.1 Foundations | dependencies; lint rules, their test and the guide; `index.css` tokens; `lib/backoff.ts`, `lib/timestamps.ts`; `api/index.ts`, `api/client.ts`; test setup | C1, C2, C3 |
| S7.2 State and stream | `api/eventStream.ts`; `features/session/*`; `features/tasks/taskState.ts`, `loadPages.ts` | C4, C5, C6, C7 |
| S7.3 Board | `useTaskBoard.ts`, `useWideLayout.ts`, the page, columns, tabs, card, buttons, drag-and-drop, dialog, expandable text, chevrons, states; `App.tsx` and `App.test.tsx`; `index.html`; font | C8, C9, C10, C13 |
| S7.4 Seed mode, replay, check | `--keep-token` (3.8) with its tests and the backend guide; `app/replay.test.tsx`; README changes; the dev-server check at 360 px and 1280 px | C11, C12 |

Ledger: S7.1; S7.2 after S7.1; S7.3 after S7.2; S7.4 after S7.3. Each step runs `yarn verify:frontend`; S7.4 also runs the backend checks, `uv run --locked --directory backend pytest tests/unit/test_seed_cli.py` and `uv run --locked --directory backend pytest -m integration tests/integration/test_seed.py`; `yarn verify` runs on S7.4's final commit. The worktree needs `yarn install --frozen-lockfile` first. When another stage integrates into `umbrella/assessment` (S4 is in design), S7 merges the umbrella tip before its next implementation commit.

## 6. Acceptance criteria

Unit and component tests run under vitest with jsdom; component tests in StrictMode (3.11).

- **C1 Lint.** `src/test/eslintRules.test.ts` (inside Vitest's `src/**/*.test.{ts,tsx}` discovery and `tsconfig.app.json`; `// @vitest-environment node`) lints three snippets through the ESLint API with the repository config: `export function f() {}` fails `func-style`; `<div onClick={handleClick} />` fails `jsx-a11y`; `<button><svg /></button>` fails `control-has-associated-label`. `yarn verify:frontend` is green.
- **C2 Client.** With a stubbed `fetch`: the bearer header is sent; a `TaskPage`, a `TaskView` and a ticket are validated and an invalid body becomes `ApiRequestError` with status 0; `204` is not parsed; `401`, `404`, `503` and a network error each become `ApiRequestError` with their status; a ticket request that never answers fails with status 0 after the timeout; no error object contains the token (`JSON.stringify` and `util.inspect` of the error do not include it).
- **C3 Helpers.** `compareTimestamps` orders `…:00Z` before `…:00.000001Z`, `….000001Z` before `….000002Z`, and `….5Z` after `….400000Z`; `reconnectDelayMs` stays within `[500, max(500, min(30 000, 1 000 × 2^(n−1)))]` for draws 0 and 1 and every attempt 1–10.
- **C4 Session.** A fragment token is stored and the fragment removed; a second `capture()` and a second store return the same token, also with a `Storage` that throws; a token stored by another tab (a `storage` event) and `key === null` reach the store; `clearIfCurrent` clears only the current token; `App` with token A shows A's board, and after a `storage` event with token B it shows a new board whose requests carry B only, A's stream is closed and none of A's tasks remain.
- **C5 Board rules.** Merge by id with a strictly later `updated_at` (an older `task.updated` after a newer one changes nothing); `task.created` for a known id is idempotent; `task.deleted` removes the card and a later event or page row for that id is dropped; a task moved into a column that is not loaded down to it shows at that column's bottom (S7-D5); a page row for an unknown older task sorts into place; `arrived` is set by a live `task.created` only.
- **C6 Resync.** A task deleted while offline disappears, also when more new tasks than the column showed were created offline (G1: 50 shown, the oldest deleted and 50 created offline; after the resync the column shows the 50 new and the 49 remaining, not the deleted one); a task moved offline from Pending (50 shown) to Completed, below the first 200 of more than 200 newer Completed tasks, appears in Completed after the resync and not in Pending (G4, S7-D5); a task created and one deleted during the refetch are right after the replay; an event-added card below the loaded part is reconciled (kept if the server has it, removed if not); a card with a move in flight keeps its displayed column; each chain stops at the first row at or older than the board's frontier or at `next_cursor = null`; a first-load reply that arrives after the first resync completed changes nothing (G2: a task deleted before the subscription does not come back), and the same holds after a reconnect; a resync of an older epoch is discarded; a "Load more" reply of an older epoch or an old cursor is ignored; every discarded request clears its column's loading flag.
- **C7 Loop.** With a fake `EventSource`, a fake ticket function, an injected `sleep` and `random`: an error closes the source and opens one new ticket and one new source after the back-off; `auth.revoked` reconnects without signing out; a `401` on the ticket request calls `onUnauthorized` and stops; an invalid frame is ignored; a source that neither opens nor errors is replaced after the open timeout; a stream that opens and closes ten times in a row gets growing delays (the counter does not reset before 30 s open); `reconnect()` waits the back-off; `stop()` leaves no open source and no timer.
- **C8 Board.** From stubbed pages the board shows three columns in order; an incoming `task.created` adds a card at the top of Pending with the arrival mark; `task.updated` arriving out of order keeps the newer status; `task.deleted` removes the card and a later event for that id is dropped; "Load more" appends the next page; at start exactly one ticket, one `EventSource`, three first-load requests and, after `onopen`, three resync requests are made (in StrictMode); a killed stream then leads to exactly one new ticket and one resync (three chains); when the stream never opens the board still shows the first load.
- **C9 Actions.** The forward and backward buttons move the card at once and send one `PATCH`; a `503` moves the card back with "Couldn't change the status. Try again."; a `404` removes the card; a second click while the first `PATCH` is in flight sends no request until the first answers, then one `PATCH` with the newest target, and the card ends on the second click's status; a drop of a dragged card on another column sends the same `PATCH`, and dropped plain text or a drop on the card's own column sends nothing; delete asks for confirmation, "Cancel" keeps the card and sends nothing, "Delete" keeps the card disabled until the server answers, removes it on `204` or `404`, and on `503` keeps it with "Couldn't delete the task. Try again."
- **C10 Text and access.** A description longer than three lines (measured sizes stubbed) shows "Show more", which expands to the whole text and turns into "Show less"; a short one shows no toggle; the "Source message" block holds the `source_text`; every control is found by role and accessible name; at a narrow width the tabs switch the visible column and no card is draggable.
- **C11 Seed mode.** `--keep-token` for a user with a link creates the task, publishes only `task.created` (no `auth.revoked`), leaves `token_version` unchanged and prints no token; for a user without a link it exits 2, creates no task and publishes nothing; the existing `test_seed.py` and `test_seed_cli.py` cases pass unchanged.
- **C12 Live check and replay.** On the dev servers (`yarn infra:up`, `yarn db:migrate`, `yarn dev:api`, `yarn dev:web`) in Orca's browser, rows V1–V10 of section 7 behave as the "After S7" column says; screenshots at 360 px and 1280 px are kept in the stage diary. `yarn workspace frontend vitest run src/app/replay.test.tsx` builds V1–V7 and V10 from the S9 rows as the heuristic tasks of S6's rows W1–W3 (a committed JSON fixture of the eight `TaskView` values, generated once by a backend script the stage names), prints each row with the outcome the implemented code gives, asserts each against the "After S7" column and fails naming any row that differs. The reviewer runs it on the reviewed commit and compares it with the accepted table. No wire shape is restated by hand in `src/` outside `api/generated` (no `z.object(` and no object type that repeats `TaskView`'s or an event's fields).
- **C13 States and labels.** The loading skeletons, the three empty-column texts, "Couldn't load tasks." with a working "Retry", the header's "Connecting…", "Live" and "Reconnecting…", and both signed-out texts each render in a component test; every visible string of the interface is English; the forward button of each status has its target's colour class.

## 7. Owner acceptance

Census, 2026-10-26, read-only: the development database `taskbot` has no tables (never migrated); there are no stored users or tasks. The production-shaped rows are S9's owner rows A1–A8 (`backend/tests/acceptance/s9_rows.json`; 7 to 2,276 characters, Russian and English); as tasks they are S6's rows W1–W3 (heuristic extractor). Their text is not repeated here. Rows are named V1–V10 so they do not collide with S2's and S3's R rows and S6's W rows.

Setup: one user seeded with A1–A8 in order (A8 newest), the dashboard opened with that user's link at 1280 px; a second tab with the same link.

| Row | Action | Today | After S7 |
|---|---|---|---|
| V1 | open the link | a page with the heading "AI Task Bot" and nothing else; the token stays in the address bar | the board: eight cards in Pending, A8 at the top; In Progress and Completed empty; the address bar has no token; header "Live" |
| V2 | read the A1 card | — | title = the whole text; no description; "Source message" holds the text; no "Show more" |
| V3 | the A8 card | — | title of 118 characters ending in "…"; description in three lines with "Show more", which shows all 2,276 characters and becomes "Show less"; "Source message" holds the text |
| V4 | A6 card, forward button "In Progress" | — | the card moves to In Progress at once; the second tab shows it there within about a second |
| V5 | drag the A3 card to Completed | — | the card moves to Completed; the second tab follows |
| V6 | A5 card, delete, then "Delete" | — | a confirmation dialog first ("Cancel" keeps the card); after "Delete" the card disappears in both tabs |
| V7 | `yarn --silent dev:seed --keep-token <id> "<text>"` while the board is open | the seed rotates the token: an open dashboard would be signed out | a new card appears at the top of Pending with a fading amber edge, no reload, and the board stays signed in |
| V8 | stop `yarn dev:api` for 10 s, then start it | — | header "Reconnecting…", then "Live"; the cards are the same, none doubled |
| V9 | the same board at 360 px | — | tabs "Pending 6", "In Progress 1", "Completed 1" (after V4–V7), one column at a time; buttons usable by touch; no drag |
| V10 | `yarn --silent dev:seed <id>` (rotating) while the board is open | — | the board shows "This link no longer works. Send /dashboard to the bot for a new link" |

Owner's acceptance, 2026-10-26, asked in Russian with the same ten rows in plain words: «Да».

## 8. Change profile

New behaviour plus one change to shipped code. Touches: **public API consumption and serialized shapes** (the dashboard reads the generated `TaskView`, `TaskPage`, `ServerEvent` and `RealtimeTicket` and depends on the event order and the `401` rule; no backend contract changes); **authorization on the client** (the token from the fragment, its storage, cross-tab replacement, the board keyed by token so two users' data never mix, the compare-and-clear sign-out; the token never reaches an error object, the console or a URL other than the fragment it arrived in); **concurrency** (events against page loads, a resync against events and moves, optimistic moves against responses and other tabs, StrictMode's double effects, one stream per tab); **a development tool's behaviour** (the seed's new mode; its default is unchanged); **rendering** (the board, its states and the look). No new process, store or external service: the font is self-hosted, so the architecture map holds. No migration, money or deletion logic beyond calling S6's `DELETE`. Earns: the board-rule, resync and loop tests C5–C7, the token-switch test in C4, the StrictMode counts in C8, the seed tests C11, the replay C12.

## 9. Departures from the umbrella and S6

| # | Was | Is | Why | Binds |
|---|---|---|---|---|
| K1 | S6 section 9: a task event for an unloaded task outside the loaded range is "ignored" | the task is kept and shown at the bottom of its column; a later page sorts it into place | S7-D5; "ignored" also leaves a stale card when a load-more is in flight (executed, exploration B 2.4) | — (S6 guidance) |
| K2 | lead's S7 goal: S7 does not edit backend source | S7 adds `--keep-token` to the seed entry point (3.8) | S7-D2: the umbrella's own acceptance (a seeded task appears without reload) needs it while S4 is not built | lead |
| K3 | umbrella section 12: whether `func-style` and `eslint-plugin-jsx-a11y` enforce the guide's rules | they do (3.10) | S7-D4 | — |
| K4 | umbrella section 8: "One dashboard view (decided in S7)" | a Kanban board; on a phone one column per tab | S7-D1 | lead (section 8) |
| K5 | umbrella 3.6 and Z2 (S6-D2): after a reconnect the dashboard reloads "as many tasks as it showed" | every column reloads every task down to the oldest card the board showed (at least the shown ones; at least 50 per column at the first open), merged and pruned rather than replaced, also on the first `open` | a count stops short when tasks were created offline and leaves a deleted card on the board (gate G1); per-column limits lose a card moved offline (gate G4); the first load runs before the subscription; a replacement drops cards the user sees (3.6). The intent of S6-D2 (reload what was shown) is unchanged | —
| K6 | umbrella S7 scope, S9-Y7, README line 310: the source text "in a tooltip on each task card" | a collapsible "Source message" block on each card | S7-D1: chosen on the mockup; a tooltip has no hover on touch screens | lead (umbrella S7 scope, section 15 Z1 wording) |

## 10. Verification and risks

Transitive importers: nothing outside `frontend/` imports the frontend. The seed module is imported by `entrypoints/seed/__main__.py`, `tests/integration/test_seed.py` and `tests/unit/test_seed_cli.py` (grep at the base; premise lens). Runs: the frontend suite on every step; the two seed test files on base and head with outputs compared; `yarn api:check` (no schema change expected).

Risks carried:

- **Drag-and-drop in jsdom**: the tests fire the handlers with a stub `dataTransfer`; the real drag is checked in the browser (V5).
- **Clamp measurement**: the toggle depends on layout; tests stub `scrollHeight`, `clientHeight` and `ResizeObserver`; the browser check (V3) is the real proof.
- **jsx-a11y and ESLint 10**: no release declares ESLint 10; a future ESLint API removal could break the plugin; it ran on 40+ constructs without a crash (exploration C 1.1).
- **Half-open connections**: the page cannot see S6's `: ping` comments, so a connection that stays open but delivers nothing keeps "Live" until the browser notices; the `online` event and the next error recover it.
- **Unbounded resync**: every column reloads all its tasks newer than the board's oldest card after every reconnect, so an old Pending card beside thousands of newer Completed tasks reloads them all and Completed then shows them; no cap was imposed (an arbitrary cap is the owner's call). At assessment scale this is a few pages.
- **Memory**: `byId` keeps every task met by an event for the board's life.
- **Cards shifting under the pointer**: a live `task.created` or a sorted-in page can move cards while the user aims; deletion is protected by the dialog naming the task; a misplaced move is visible and reversible.
- **One stream per tab**: each open tab holds one SSE connection and one Redis connection on the API (S6 risk "open-stream resources").
- **Token in localStorage without expiry**: an assessment trade-off already recorded (umbrella section 8).
- **Seed upsert**: `--keep-token` for an unknown Telegram id creates the user row (no task, exit 2), as the plain seed's upsert would.

## 11. Pre-handoff review record

Three lens agents ran on round 1 (Sonnet 5.5; reports in `.tasks/artifacts/2026-10-26-s7/lens-{coherence,premise,adversarial}.md`).

- Adversarial: 2 HIGH, 6 MED, 6 LOW. HIGH folded: a token change in another tab without a `401` could mix two users' tasks in one board → the board is keyed by its token and every request uses the mount's token (3.3, C4); a failed resync had no way to ask for a new connection → `reconnect()` with the back-off (3.4, C7). MED folded: the counter reset on resync defeated the floor → reset after 30 s open (3.4, C7); the rebuild from pages dropped event-added, loaded-more and moving cards → merge, prune, replay, and "Load more" disabled during a resync (3.6, C6); two quick moves could end on the first → one `PATCH` in flight per card, newest target wins (3.7, C9); the first load was specified twice → first load independent of the stream plus a resync on every `onopen`, counted in C8; no timeouts → 10 s ticket and 15 s open timeouts, `online` reconnects (3.2, 3.4); late pages could move a cursor back → cursor check and the flag always cleared (3.6, C6). LOW folded: typed drag payload (3.7, C9); no token in errors (3.2, C2); the seed's no-link case (3.8, C11); `storage` with `key === null` and blocked storage (3.3, C4); memoised capture (3.3, C4). Accepted as a risk: cards shifting under the pointer (10).
- Premise: 0 HIGH, 5 MED, 9 LOW. Folded: `test_seed_cli.py` and strict mypy (3.8: a separate kept-token function and result, `run()` unchanged); the seed's user id and no-link check (3.8); K6 for the tooltip; jsdom stubs for `matchMedia` and `ResizeObserver` and one rendering per layout (3.7, 3.11); the signed-out text (3.3); `App.test.tsx` rewritten (3.1, S7.3); README and `backend/AGENTS.md` lines (3.8); text contrast (3.9); the font family name and one import place (3.1, 3.9); SVG chevrons (3.9); no shadow for the arrival mark and full Tailwind class names (3.9); the C12 grep. Confirmed without defect: the generated schemas, the layer facts, the lint rules, the S6 facts, the timestamp output, the Radix values, the font package, user-event, StrictMode configuration and the deferred start, the census and the V-row counts.
- Coherence: 0 HIGH, 9 MED, 7 LOW. Folded: the first load and resync counts (3.6, C8); `reconnect()`, attempt and generation defined (3.4); who restarts after a token change (3.3); the signed-out text (3.3); K6; the session wiring and the replay test's place (3.1, 3.3); move error and busy state in the state and in C9; a states-and-labels criterion (C13); the layout mechanism (3.7); C12's grep; `loaded` dropped and `BoardAction` and "shown" defined (3.5); the seed lookup and `backend/AGENTS.md` (3.8); `index.html` and `App.test.tsx` (3.1); the arrival trigger (3.5); the mockup options written out (2); C1 as a committed test.

Design gate round 1 (Codex, blocked): G1 [HIGH|confirmed] the count-based resync left an offline-deleted card when new tasks shifted the refetched window → per-column frontiers covering every shown card, chains to the frontier, prune within it (3.6, C6, K5). G2 [HIGH|confirmed] a delayed first-load reply could restore a task deleted before the subscription → one request epoch for first load, "Load more" and resync; a resync start aborts and invalidates the others; busy flags always clear (3.5, 3.6, C6). G3 [MED|confirmed] the lint test sat outside Vitest's discovery and the TypeScript projects → `src/test/eslintRules.test.ts` (3.1, C1). The gate's C9 note folded: queued clicks are serial `PATCH` requests. Owner decisions S7-D1 to S7-D7 and rows V1–V10 are unchanged.

Design gate round 2 (Codex, blocked): G1–G3 closed. G4 [HIGH|confirmed] per-column frontiers dropped a card moved offline below its destination's frontier, against S7-D5 → one board frontier for every column's chain; prune only tasks absent from all pages (3.5, 3.6, C6, K5); the larger reload is recorded in section 10. Owner decisions and V1–V10 unchanged.
