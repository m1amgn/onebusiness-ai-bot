# S11 — Architecture diagrams for the reviewer

> **STATUS 2026-10-29: ACTIVE.** Stage design, round 4: amendment 1 adds the owner-requested stage M4 (section 14); the lens review, gate round 1 and the M1 planarity finding are folded in (section 13); the owner's acceptance and S11-D6 (sections 12, 2).

Stage S11 of the umbrella `docs/plans/2026-10-23-umbrella-assessment.md` (section 5, S11; section 19, amendment 8),
at the tip `48af3d8` of `umbrella/assessment`, after S8's integration. Workflow:
`collab-2026-10-29-s11-architecture-diagrams-for-the-reviewer`.

## 1. Problem

The owner asked on 2026-10-27: «У тебя есть скил нарисования html и pdf схемы архитектур приложений. Надо сделать
подробные схемы и в Readme для ревьювера добавить на них ссылки». A reviewer of this public repository has a README
with a text table of services and no picture of how the parts connect. S11 adds detailed architecture diagrams of the
delivered system, drawn from the code and the Compose stack at `48af3d8`, as interactive HTML and PDF, and a README
section that shows the main diagram and links every diagram.

S11 changes no application code and no Compose file. Amendment 1 (section 14) adds an owner-requested stage M4 that changes
`.env.example`, one unit test and the README and guides.

## 2. Decisions

The owner answered each question on 2026-10-29. The answer is quoted as given.

| # | Question | Decision | Owner's answer |
|---|---|---|---|
| S11-D1 | Which diagrams | Four: the system overview, the voice-message sequence, the dashboard live-update sequence, the voice-job lifecycle. No text-message sequence (its short path is on the overview), no Compose start-up diagram, no data-model diagram (archify has no entity-relationship type). | «4 схемы (Recommended)» |
| S11-D2 | How the three flow diagrams cite the code | `docs/architecture/README.md` (the index) lists every diagram and carries, per flow diagram, a table of steps with `file:line`, verified when authored. Only the overview stores citations in its spec and can be checked `FRESH`; the index says so. | «Таблица в индексе (Recommended)» |
| S11-D3 | How the root README shows the diagrams | A section "Architecture": one light PNG of the overview, made from page 1 of its PDF, and a four-row table (diagram, what it shows, PDF, interactive HTML). The PDF is a headless Chrome print, light theme (section 5.2). | «Одна светлая PNG (Recommended)»; the PDF method and theme were stated in the question and not objected to |
| S11-D4 | Freshness check in `yarn verify` | No. The repo-map check runs once, at S11's acceptance. | «Не включать (Recommended)» |
| S11-D5 | How the diagrams are built | Once, with the repo-map and archify skills, Chrome and `pdftoppm`. No repository script and no rebuild procedure; the index names the tools and the pinned commit in one line. | «Мне надо сейчас один раз это сделать и все» |
| S11-D6 | How the overview avoids crossing lines (asked after M1 proved the first graph non-planar) | Telegram drawn as two boxes, the bot's side and the workers' side, explained on a card as one Bot API; OpenAI and whisper drawn as one transcription-provider box. Every relationship stays on the picture; twelve components; showcase quality. | «Telegram дважды (Recommended)» |
| S11-D7 | Owner's request after checking the packaged stack on `48af3d8` (relayed by the lead, `.tasks/notes/2026-10-29-owner-full-functionality-and-main.md`) | Stage M4 (section 14): `.env.example`, the README quickstart and the guides default to OpenAI transcription and Claude Haiku extraction; the README's verification record is written from the owner's check. | «Я все посмотрел, все ок, доделывайте схему, проверяйте и актуализируйте Readme, инструкции и .env.example сразу делайте на использование максимального функционала, то есть распозновалку на OpenAI api и задачи на Хайку. Пушим все это дело в origin/main и я буду сдавать тестовое» |

Settled without a question:

- The text is English. Committed files carry no personal names, local paths, keys, e-mail addresses or chat content
  (umbrella section 19). The one accepted identity string is the public repository URL in the overview's
  `meta.repository.url` and its source links.
- The diagrams describe code, not plans (umbrella S11 scope). Where a plan and the code differ, the diagram shows the
  code.
- The detail level is the repo-map skill's internal level (component sources, real service, queue and key names),
  because the repository is the team's own and «подробные схемы» asks for detail.
- S11 commits carry no tool-attribution trailer (`AGENTS.md`, "Commits").

## 3. Departures from the umbrella text

- **FRESH for every map** (umbrella S11 acceptance). archify stores `meta.repository` and `components[].sources` only in
  the `architecture` schema; the sequence and lifecycle schemas reject both (`additionalProperties: false` at root,
  `meta` and item level). The skill's `check.mjs` prints "not source-backed" for those three specs, never `FRESH`, and
  does not count them as stale. S11-D2 replaces the criterion for the three flow diagrams with the index's verified
  citation tables (A4). The overview stays `FRESH` (A2).
- **A source-cited markdown summary per diagram** (umbrella S11 scope). The generated summaries of the three flow
  diagrams carry no citations, for the same reason; their citations live in the index tables.
- **"Every README link resolves on GitHub"** (umbrella S11 acceptance). S11 integrates into `umbrella/assessment`,
  which is not on GitHub's default branch until the umbrella's own delivery. A7 checks that every relative link
  targets a committed file; the GitHub check happens at the umbrella's delivery, not in S11.
- **"How the PDF is made" and "the theme"** (umbrella S11 open questions) are settled by S11-D3 and section 5.2.
- **Owner-requested addition: full-functionality defaults and the live verification record** (beyond the umbrella's
  S11 scope, which changes documentation only). The owner, 2026-10-29, after checking the packaged stack on `48af3d8`:
  «Я все посмотрел, все ок, доделывайте схему, проверяйте и актуализируйте Readme, инструкции и .env.example сразу делайте на использование максимального функционала, то есть распозновалку на OpenAI api и задачи на Хайку. Пушим все это дело в origin/main и я буду сдавать тестовое». S11 therefore also changes `.env.example`, `backend/tests/unit/test_env_example.py`, the README outside
  the "Architecture" section, `docs/development.md` and `docs/deployment.md` (stage M4, section 14). The decomposition
  does not change, so the umbrella is not amended (the lead's ledger note of 2026-10-29). The push to `origin/main`
  in the owner's words is the lead's act after S11 and the umbrella's final review, not part of S11.

## 4. Approaches (D4)

1. **Chosen — build once with the skills, commit the artifacts.** No new code; the specs stay in the repository for a
   later author. Matches S11-D5.
2. A repository script `scripts/build-diagrams.mjs` with a test. Lost: the script needs the archify skill outside the
   repository, so a reviewer cannot run it, and the owner wants a one-time build.
3. Mermaid in Markdown, which GitHub renders inline. Lost: not the owner's requested skill, no interactive HTML, no
   source citations.

## 5. What is delivered, and how it is made

### 5.1 Files

Exactly these eighteen files under `docs/architecture/`, plus the root `README.md` section and this design:

| File | Count | Made by |
|---|---|---|
| `<name>.<type>.json` | 4 | the author |
| `<name>.html` | 4 | repo-map `build.mjs` → archify `deliver` |
| `<name>.md` | 4 | repo-map `build.mjs` → `summarize.mjs` |
| `<name>.pdf` | 4 | section 5.2 |
| `system-overview.png` | 1 | section 5.3 |
| `README.md` (the index) | 1 | the author (section 7) |

Stems and types: `system-overview.architecture.json`, `voice-message.sequence.json`, `live-updates.sequence.json`,
`voice-job.lifecycle.json`.

The build runs outside the repository: `build.mjs <type> <scratch>/<name>.<type>.json --out <scratch>/<name>.html`
in a scratch directory, and the author copies only the deliverables into `docs/architecture/`. archify's
`*.visual-check.*` screenshots, sidecar HTML and JSON receipts (they hold absolute local paths), print copies, the
Chrome profile and every other render stay in the scratch directory and are removed when the stage ends.

Every spec: `meta.quality_profile: "showcase"`, no `meta.animation`, no `meta.visual_preset`, at most twelve primary
nodes, states or participants, three `views`. The overview also carries `meta.repository` with `url` exactly
`https://github.com/m1amgn/onebusiness-ai-bot.git` (the output of `git remote get-url origin`), `provider: "github"`,
`revision` `48af3d82208626cfc7b877e59d9ae57e54a5608e`, and `sources` on every component backed by code in this
repository; for a cloud component the source is its adapter here.

Every path in a spec's `sources[].path` and in the index tables is relative to the repository root. Section 6
abbreviates `backend/src/taskbot/` away; the author writes the full path.

### 5.2 PDF

Measured 2026-10-29 on archify's own examples with Chrome 156: a plain `--print-to-pdf` uses archify's print stylesheet
(`@page { size: landscape; margin: 1.5cm }`, Letter) and split a 820 × 920 sequence diagram over four pages with
nothing on page 1 but the title; a copy with a page sized to the diagram printed the whole diagram on page 1, light.
So:

1. Copy the delivered `<name>.html` to `<scratch>/<name>.print.html` and insert before `</head>`:
   `<style>@page{size:<W>px <H>px;margin:0}@media print{.source-evidence-beacon{display:none!important}}</style>`.
   `<W>` and `<H>` are chosen so the title and the whole diagram panel fit on page 1; the legend and cards may follow
   on later pages. The second rule hides the viewer-only "SRC" source badges that the runtime adds to cited nodes.
2. Print: `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu
   --no-pdf-header-footer --user-data-dir=<scratch>/chrome-profile --virtual-time-budget=5000
   --print-to-pdf=<scratch>/<name>.pdf file://<scratch>/<name>.print.html`.
3. The committed `<name>.html` is the `deliver` output, byte for byte (its SHA-256 equals the delivery receipt's).
4. After the last print: remove the profile; `pgrep -fl 'Google Chrome.*headless'` prints nothing.

### 5.3 README image

`pdftoppm -png -r 200 -f 1 -l 1 -singlefile -x <X> -y <Y> -W <W> -H <H> system-overview.pdf system-overview`, cropped
to the diagram panel (no title margin). On GitHub a README image shows at about 880 px, so node text is small; the
image links to the PDF, and the README says so.

## 6. The four diagrams

Each fact was read at `48af3d8`; the exploration record is in the designer's diary. Labels follow the skill's limits
(label ≤ 14 characters, sublabel ≤ 25, edge label ≤ 10); the wording below is content, the author shortens it and keeps
every name and number.

### 6.1 System overview (`architecture`, the main diagram)

Components (twelve). Telegram is drawn twice and the two transcription providers once, by S11-D6:

| Id | Type | Shows | Sources |
|---|---|---|---|
| `tg_bot` | external | Telegram Bot API on the bot's side: updates and replies | — |
| `tg_workers` | external | the same Telegram Bot API on the workers' side: file download, edits and replies | — |
| `browser` | external | the reviewer's browser running the dashboard | — |
| `web` | frontend | Compose `web`: nginx, static build, `/api` and SSE proxy, the only published port `127.0.0.1:WEB_PORT` (default 8080) | `docker/nginx.conf:20`, `docker-compose.yml:211` |
| `api` | backend | Compose `api`: FastAPI REST, tickets, SSE | `entrypoints/api/app.py:61`, `entrypoints/api/realtime.py:50` |
| `bot` | backend | Compose `bot`: aiogram long polling, text tasks, voice acceptance | `entrypoints/bot/__main__.py:224`, `entrypoints/bot/handlers.py:156` |
| `transcribe` | backend | Compose `worker-transcribe`: Celery, one child, download, transcribe, extract | `entrypoints/worker/launcher.py:15`, `application/transcribe_voice.py:122` |
| `notify` | backend | Compose `worker-notify`: Celery, replies, beat sweep every 60 s | `entrypoints/worker/__init__.py:15`, `application/notify_user.py:73` |
| `redis` | messagebus | one container, two roles: DB 1 Celery queues `transcription` and `notification`; DB 0 pub/sub `events:user:{id}`, tickets, bot lock and heartbeat | `infrastructure/settings/redis.py:32`, `domain/jobs.py:11`, `infrastructure/redis/client.py:25` |
| `postgres` | database | users, inbound messages, tasks, voice jobs | `infrastructure/sqlalchemy/tables.py:70`, `docker-compose.yml:59` |
| `provider` | cloud | the transcription provider chosen by `TRANSCRIPTION_PROVIDER`: OpenAI (default, needs a key) or the Compose `whisper` container (profile `whisper`, whisper.cpp on CPU) | `infrastructure/transcription/openai.py:36`, `infrastructure/transcription/whisper_server.py:70`, `docker-compose.yml:224` |
| `anthropic` | cloud, `dashed` | Claude Haiku extraction, optional (`TASK_EXTRACTOR=claude` and a key) | `infrastructure/extraction/factory.py:55` |

Relationships (the facts; the author may drop a label only by the archify rule of fully implied endpoints):

- `browser → web` HTTP; `web → api` `/api` and SSE.
- `tg_bot ↔ bot` long polling and replies.
- `bot → postgres` tasks and voice jobs; `bot → redis` enqueue on `transcription` and publish events;
  `bot → anthropic` extract (dashed).
- `redis → transcribe` `transcription` queue; `transcribe → tg_workers` file download and the retry edit;
  `transcribe → provider` audio; `transcribe → anthropic` extract (dashed);
  `transcribe → postgres` task and job state; `transcribe → redis` `notification` job and event.
- `redis → notify` `notification` queue; `notify → tg_workers` edit or reply; `notify → postgres` notification state;
  `notify → redis` sweep re-enqueue.
- `api ↔ postgres` tasks, tokens; `api ↔ redis` tickets, subscribe, publish.

Views: "Messages in" (tg_bot, bot, postgres, redis, anthropic); "Voice through the queues" (redis, transcribe,
notify, provider, tg_workers); "Live dashboard" (browser, web, api, redis, postgres).

The graph of section 6.1 as first written contained K3,3 (bot, worker-transcribe and worker-notify each linked to
Telegram, PostgreSQL and Redis), which no layout draws without a crossing, and showcase rejects every crossing
(implementation note 2026-10-29, `.tasks/artifacts/2026-10-29-s11/check-overview-planarity.py`). With Telegram split
and the providers merged the graph is planar (`overview-variants-planarity.py`); an archify candidate with this
component set validated with zero crossings, leaving four label-clearance errors for the author
(`overview-variant-a.architecture.json`, a starting point, not a deliverable).

Cards: "One backend image, six services" (`migrate`, `api`, `bot`, `worker-transcribe`, `worker-notify` and `seed` run
one image; `migrate` runs `alembic upgrade head` once and exits 0 before the others start); "One service, two boxes or roles"
(Redis: DB 1 broker, DB 0 events, tickets, lock, heartbeat; the two Telegram boxes are one Bot API, split so that no
line crosses another); "What is optional" (`whisper` only with
`COMPOSE_PROFILES=whisper`; Claude only with a key, used by the bot, worker-transcribe and `seed`; without an OpenAI
key a voice message gets the "transcription unavailable" reply; `seed` behind profile `tools`).

### 6.2 Voice message (`sequence`)

Participants: Telegram, bot, PostgreSQL, Redis queues, worker-transcribe, transcription provider (OpenAI or
whisper-server), worker-notify. Extraction is a step label on worker-transcribe, not a participant.

Messages in order; each is a row of the index table with its citation:

1. Telegram → bot: voice update — `entrypoints/bot/handlers.py:104`.
2. bot: over 120 s or 10,000,000 bytes → refusal reply, nothing stored — `application/accept_voice.py:65`, `entrypoints/bot/handlers.py:118`.
3. bot → PostgreSQL: insert voice job `queued`; a duplicate update stops here (no acknowledgement, no enqueue) unless
   the row is still `queued` without an acknowledgement — `application/accept_voice.py:71`, `application/accept_voice.py:87`.
4. bot → Telegram: "Transcribing…" acknowledgement, its id stored — `application/accept_voice.py:95`.
5. bot → Redis: `transcribe_voice_job` on `transcription` — `application/accept_voice.py:112`.
6. Redis → worker-transcribe: deliver — `entrypoints/worker/transcription.py:12`.
7. worker-transcribe → PostgreSQL: claim, `processing`, attempt + 1, 450 s lease — `application/transcribe_voice.py:124`.
8. worker-transcribe → Telegram: download the file — `infrastructure/telegram/voice_files.py:30`.
9. worker-transcribe → provider: audio; provider → worker: transcript — `application/transcribe_voice.py:180`.
10. worker-transcribe: extract title and description — `application/transcribe_voice.py:184`.
11. worker-transcribe → PostgreSQL: one transaction, task and job `done` — `application/transcribe_voice.py:188`.
12. worker-transcribe → Redis: publish `task.created`, then `notify_user` on `notification` — `application/transcribe_voice.py:208`.
13. Redis → worker-notify → Telegram: edit the acknowledgement into "Task created: …" — `application/notify_user.py:121`.
14. (dashed) a permanent failure stores `failed` with its kind and enqueues only the notification, no event —
    `application/transcribe_voice.py:212`.

Segments: "Accept in the bot" (1–5), "Transcribe in the worker" (6–12, 14), "Notify" (13). Views follow the segments.

### 6.3 Dashboard live updates (`sequence`)

Participants: Telegram user, bot, browser, nginx, api, Redis, PostgreSQL.

1. user → bot: `/dashboard`; bot rotates the token in PostgreSQL, publishes `auth.revoked`, then replies with
   `<base>/#token=<token>` — `entrypoints/bot/handlers.py:62`, `application/dashboard_token.py:39`.
2. browser: stores the token from the fragment in local storage — `frontend/src/features/session/sessionStore.ts:54`.
3. browser → nginx → api: `POST /api/realtime/tickets` with the bearer token — `entrypoints/api/realtime.py:34`.
4. api → Redis: ticket `SET NX EX 30`; api → browser: 201 with the ticket — `infrastructure/redis/tickets.py:34`.
5. browser → nginx → api: `GET /api/events?ticket=…` (unbuffered, 1 h read timeout) — `frontend/src/api/eventStream.ts:139`, `docker/nginx.conf:20`.
6. api → Redis: `GETDEL` the ticket and subscribe to `events:user:{id}`; then api → PostgreSQL: check the token
   version — `infrastructure/redis/tickets.py:45`, `application/stream_events.py:129`, `application/stream_events.py:133`.
7. browser: on open, reload every column (resync) — `frontend/src/features/tasks/useTaskBoard.ts:55`.
8. bot → Redis: publish, labelled "publish (bot, workers, API)"; Redis → api → browser: `data:` frame (`task.created`,
   `task.updated`, `task.deleted`) — `application/publishing.py:10`, `entrypoints/api/realtime.py:62`.
9. api: every 20 s rechecks the token version; a new `/dashboard` link ends the stream with `auth.revoked` —
   `application/stream_events.py:19`.
10. browser: on error, reconnect with a new ticket after jittered backoff, then resync — `frontend/src/api/eventStream.ts:167`, `frontend/src/lib/backoff.ts:1`.

Segments: "Sign in" (1–2), "Open the stream" (3–7), "Live events and reconnect" (8–10). Views follow the segments.

### 6.4 Voice job (`lifecycle`)

States — the four statuses of `VoiceJobStatus` (`domain/voice_jobs.py:11`), with their archify type:

| State | Type | Meaning |
|---|---|---|
| `queued` | `start` | accepted, waiting for a worker |
| `processing` | `active` | claimed by a worker, 450 s lease |
| `done` | `success` | terminal; task stored |
| `failed` | `failure` | terminal; carries a kind, among them `transient_exhausted` (fifth transient failure, reported by the worker) and `stuck` (five attempts used without an answer, after crashes or expired leases) |

There is no fifth status. `stuck` is a failure kind, not a state. The sweep and the notification are not states: they
change no status and appear as cards.

Transitions — `domain/voice_jobs.py` rules mirrored by guarded SQL in `infrastructure/sqlalchemy/voice_jobs.py`:

- (bot) → `queued` — `infrastructure/sqlalchemy/voice_jobs.py:76`.
- `queued → processing`: claim, attempt + 1 — `domain/voice_jobs.py:103`.
- `processing → processing`: claim again after the 450 s lease expired — `infrastructure/sqlalchemy/voice_jobs.py:43`.
- `processing → queued`: transient failure, attempts < 5, retry after 2, 4, 8 or 16 s — `domain/voice_jobs.py:122`, `domain/limits.py:15`.
- `processing → done`: task stored in the same transaction — `domain/voice_jobs.py:128`.
- `processing → failed`: permanent kind, soft time limit, or the fifth transient failure (`transient_exhausted`) — `domain/voice_jobs.py:141`.
- `queued | processing (lease expired) → failed`: kind `stuck`, five attempts used — `domain/voice_jobs.py:151`.

Card "Notification, after `done` or `failed`": worker-notify tells the user in Telegram, at most five attempts, and
then sets `notified_at` and `notification_outcome` on the same row; the status stays `done` or `failed`
(`domain/voice_jobs.py:182`, `application/notify_user.py:73`). Outcomes `edited` and `replied` mean the user has the
result. `blocked` (the user blocked the bot, `application/notify_user.py:91`) and `given_up` (five attempts spent,
`application/notify_user.py:102`, `:155`) mean the result was not delivered. It is a row of the index table.

Card "Recovery sweep": every 60 s worker-notify re-enqueues stale `queued` and `processing` rows and unnotified
terminal rows; the claim then decides — `application/recover_voice_jobs.py:17`. It is a row of the index table.

Views: main path; retries and lease; failure and notification.

## 7. The index `docs/architecture/README.md`

1. One line: the diagrams describe commit `48af3d8` and were built once with the repo-map and archify skills, Chrome
   and `pdftoppm` (S11-D5).
2. A table: diagram, what it answers, links to its PDF, its HTML (open from a clone) and its generated summary.
3. One sentence: GitHub shows the PDFs; the HTML pages open in a browser from a clone.
4. The freshness sentence: only the overview carries citations in its spec; the three flow diagrams cite the code in
   the tables below, checked when they were drawn (S11-D2). The generated summaries are tool output; their status
   line refers to the author's tooling.
5. Three citation tables, one per flow diagram, step → `file:line`, every numbered row of sections 6.2–6.4 plus the
   notification and sweep cards.

## 8. The root README section "Architecture"

Inserted after the opening paragraph and before "Run with Docker", so a reviewer meets it first. The edit only
inserts; no existing line changes.

```text
## Architecture

[![System overview](docs/architecture/system-overview.png)](docs/architecture/system-overview.pdf)

The picture links to its PDF. The interactive HTML pages open in a browser from a clone;
docs/architecture/README.md explains how the diagrams cite the code.

| Diagram | Shows | PDF | Interactive (from a clone) |
| System overview | ... | system-overview.pdf | system-overview.html |
| Voice message | ... | ... | ... |
| Dashboard live updates | ... | ... | ... |
| Voice job | ... | ... | ... |
```

The section contains no fenced `sh` block and no inline code that starts with `docker compose `, `yarn ` or
`node scripts/`, because `scripts/lib/readme-commands.mjs` checks those (lines 61 and 68). Links are relative paths.

## 9. Stages and when each criterion is checked

| Id | Scope | Criteria checked before its commit |
|---|---|---|
| M1 | The system overview: spec, HTML, summary, PDF, PNG | A1, A3, A5 for the overview; the PNG half of A6; A8 and A12 on the files so far |
| M2 | Voice message, live updates, voice job: specs, HTML, summaries, PDFs | A1 and A5 for the three diagrams; A8 and A12 on the files so far |
| M3 | The index and the root README section | A4, A7, the README half of A6 |
| M4 | Full-functionality defaults and the live verification record (section 14) | A13–A17; then on the final commit A2, A8, A9, A10, A11, A12, A18 |

M2 follows M1; M3 follows M1 and M2, because the index links every diagram; M4 follows M3, because it edits the README after the inserted section. Before the last implementation commit and before review, the implementer merges the current
`umbrella/assessment` tip and runs `git diff --stat 48af3d8..HEAD -- <every cited path>`. An empty diff keeps the pin.
A non-empty diff means re-pin the overview, redo A3 and A4, and say so in the note.

## 10. Acceptance criteria

- A1: Each of the four specs passes `archify validate --quality showcase` with 9 artifact checks, 0 errors, 0
  warnings; `deliver` exits 0; `visual-check` passes at every viewport it measures. The output is in the note.
- A2: `node ~/.claude/skills/repo-map/scripts/check.mjs .` on S11's final commit prints `FRESH` for
  `system-overview.architecture.json` and "not source-backed" for the three other specs, and exits 0.
- A3: `deliver` for the overview reports `evidence=verified` at revision `48af3d8`.
- A4: Every row of the three index tables names a file and line that exist at `48af3d8` and show the step named; the
  note prints each cited line (`git show 48af3d8:<path> | sed -n '<line>p'`).
- A5: Each PDF opens (`pdfinfo` exits 0); page 1, rendered to PNG, shows the title and the whole diagram panel in the
  light theme, nothing cut off, no "SRC" badges. The reviewer looks at each page-1 image.
- A6: `system-overview.png` is a crop of page 1 of `system-overview.pdf`; the README shows it and links it to the PDF.
- A7: Every relative link in the root README section and in the index targets a file that `git ls-files` lists.
- A8: No committed S11 file carries a local path, an e-mail address, a token or chat content. On the text files:
  `git grep -nE '/Users/|/private/|/var/|/tmp/|/home/|file://|\.claude|@[a-z0-9-]+\.[a-z]{2,}|[0-9]{8,}:[A-Za-z0-9_-]{30,}' -- docs/architecture README.md`
  prints nothing that is not a code identifier, and the reviewer reads every hit. On every PDF: `pdfinfo` and
  `pdftotext` output passes the same pattern; on the PNG: `strings` output passes it.
- A9: The S11 diff touches only `docs/architecture/`, `docs/plans/2026-10-29-s11-architecture-diagrams.md`, the root
  `README.md`, and for M4 `.env.example`, `backend/tests/unit/test_env_example.py`, `docs/development.md` and
  `docs/deployment.md`. `docker-compose.yml` and application code stay unchanged.
- A10: `yarn verify` passes on the final commit (with `yarn infra:up` as its documented prerequisite); it includes
  `scripts/readme-commands.test.mjs`.
- A11: The diagrams show the code at `48af3d8`: the reviewer checks against the cited lines the service names, the
  queue names `transcription` and `notification`, the Redis keys and DB split, the numbers (120 s, 10,000,000 bytes,
  450 s lease, 2/4/8/16 s retries, 5 attempts, 60 s sweep, 30 s ticket, 20 s recheck) and the order of commit, reply,
  publish and enqueue in sections 6.2 and 6.3.
- A12: `git ls-files docs/architecture | sort` lists exactly the files of section 5.1 that exist at that stage, and no
  `*.visual-check.*`, `*.print.html` or profile file is in the worktree.
- A13: `.env.example` sets `TRANSCRIPTION_PROVIDER=openai`, `TASK_EXTRACTOR=claude` and
  `ANTHROPIC_MODEL=claude-haiku-5-5`; `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` stay empty.
- A14: `backend/tests/unit/test_env_example.py` asserts the example's `TASK_EXTRACTOR` is `claude`, keeps every other
  default check and the empty-secret check, and passes; the new assertion fails on the `.env.example` of `48af3d8`
  (the red run is cited in the note).
- A15: The README's "Run with Docker" names the three secrets a reviewer types into `.env` (the test bot token,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) and says what happens without each key: a voice message gets "Transcription
  is unavailable right now. Your message was not lost: please type it as text."; a text message still creates a task,
  titled from the text, and the reply adds "The language model was unavailable, so the title is taken from your text."
  (`backend/src/taskbot/application/replies.py:15`, `:44`). The settings table, `docs/development.md` and
  `docs/deployment.md` state the same defaults; none of them calls `heuristic` the delivered default. The Compose
  fallback for a missing `.env` value stays the keyless `heuristic`, and the README says so in one sentence.
- A16: The README's verification record states the owner's Docker check of 2026-10-29 on `48af3d8` (section 14) and
  nothing beyond it: what was met, the bot memory, and what was not run (voice without a key, the local Whisper
  container); the earlier development and Docker checks stay. No other "verified" claim is added.
- A17: The README section "Architecture" (M3) is unchanged by M4; `scripts/readme-commands.test.mjs` passes.
- A18: `node scripts/stack-acceptance.mjs` (keyless by default) passes once on S11's final commit, with no paid call:
  its environment sets `TASK_EXTRACTOR=heuristic` and empty provider keys (`scripts/lib/stack-runtime.mjs:55`).
  The output and the commit are in the note.

## 11. Change profile

Amendment 1 (M4) adds configuration defaults in `.env.example` (the delivered stack's settings when a reviewer copies
it), one unit test that pins them, and operator documentation. It touches no code path, Compose file, API, stored
data or authorization; a reviewer who copies the example and sets the keys gets the paid providers, and one without
keys sees the replies of A15. Its check: the keyless acceptance replay (A18) and the default-pinning tests.

M1–M3, documentation only: new files under `docs/architecture/`, one README section, this design. No application code, test,
Compose file, public API or stored data changes; no new process, store or external service. Earns: the repository's
conventions (English, no secrets, README command test), prior art, and the architecture-documentation check, which is
this stage's subject. Nothing touches money, authorization, migrations or concurrency. The diff touches more than
three files, so exploration ran at full depth; it changes no symbol, so `graphify affected` has nothing to traverse.

## 12. Owner acceptance (D6)

S11 changes no stored data, so the accepted table is what the reviewer sees, not data rows. Shown to the owner on
2026-10-29 in Russian; the English content:

| Where | Today | After S11 |
|---|---|---|
| Root README | a text table of services, no diagram | section "Architecture" before "Run with Docker": the overview image linked to its PDF, a table of four diagrams with PDF and HTML links |
| System overview | none | twelve components of section 6.1, each code component cited to a file and line (after D6, S11-D6 split Telegram into two boxes and merged the two providers, on the owner's answer) |
| Voice message | none | the fourteen steps of section 6.2, the failure return included |
| Dashboard live updates | none | the ten steps of section 6.3 |
| Voice job | none | the states and transitions of section 6.4; `stuck` drawn as a failure kind; the sweep as a card (gate round 1 also moved the notification from a state to a card, section 13) |
| `docs/architecture/README.md` | none | the index of section 7: the pinned commit, the links, three citation tables |

Owner's answer, 2026-10-29: «Да».

## 13. Pre-handoff review record

Three lenses on round 1 (Workflow `wf_f80c8c31-73e`, Sonnet 5.5: coherence; premise in three groups — overview,
flows, tooling; adversarial). Folded in:

- [HIGH|likely → confirmed by the designer] A plain Chrome print cannot hold a tall diagram on page 1. Reproduced on
  archify's 820 × 920 example (four pages, page 1 title only); the page-sized print copy fixed it. Section 5.2.
- [MED] The print shows the viewer's "SRC" badges: hidden in the print copy (5.2). The README image is a thumbnail:
  cropped at 200 dpi and linked to the PDF (5.3, 8).
- [MED] Tool evidence with absolute paths would land in `docs/architecture/`: the build runs in a scratch directory;
  A12 is an allow-list check.
- [MED] A8 could not see inside PDFs and missed several path forms: extended.
- [MED] The lifecycle used types archify does not have and drew a kind as a state: section 6.4 rewritten.
- [MED] Criteria were not mapped to stages; two departures were unlisted; citation drift after a merge was undetected:
  sections 9 and 3.
- [MED] The card counted five backend services (six); `api ↔ redis` lacked publish: fixed in 6.1.
- [LOW] Ten citations pointed at a `def`, decorator or neighbouring line; corrected to the statement line. Repository
  -root paths, the configurable web port, the publisher label in 6.3, the sweep row, the `-singlefile` PNG name, the
  identity-string and attribution rules: folded in.
- Not taken: a repository copy of the freshness check (S11-D4).

Design gate round 1 (Codex, 2026-10-29, blocked):

- [HIGH|confirmed] docs/plans/2026-10-29-s11-architecture-diagrams.md:224 - The lifecycle invents a fifth job status, `notified`. Fixed in section 6.4: the four statuses of `VoiceJobStatus` stay; the notification is a card that keeps `done`/`failed` and separates delivered outcomes (`edited`, `replied`) from undelivered ones (`blocked`, `given_up`). The D6 row and the index list follow.

Implementation M1, 2026-10-29 (Codex, returned to design):

- [HIGH|confirmed] docs/plans/2026-10-29-s11-architecture-diagrams.md:145 - The overview's required relationships contain K3,3, so they cannot all be drawn between the original twelve nodes without crossings, while A1 requires showcase validation that rejects every proper crossing. Answered by S11-D6 (the owner's choice among three options: Telegram twice, main path only, or standard quality): section 6.1 now draws Telegram twice and one provider box; planarity and a zero-crossing archify candidate are recorded there.

## 14. Amendment 1 (2026-10-29): full-functionality defaults and the live verification record, stage M4

The owner checked the packaged stack on `48af3d8` and asked (S11-D7): «Я все посмотрел, все ок, доделывайте схему, проверяйте и актуализируйте Readme, инструкции и .env.example сразу делайте на использование максимального функционала, то есть распозновалку на OpenAI api и задачи на Хайку. Пушим все это дело в origin/main и я буду сдавать тестовое».

What M4 changes:

1. `.env.example`: `TASK_EXTRACTOR=claude` (was `heuristic`); `TRANSCRIPTION_PROVIDER=openai` and
   `ANTHROPIC_MODEL=claude-haiku-5-5` stay. Secrets stay empty.
2. `backend/tests/unit/test_env_example.py`: `TASK_EXTRACTOR` leaves the "example equals the settings default" set,
   and the test asserts the example's value `claude` (A14). The settings default in code
   (`backend/src/taskbot/infrastructure/settings/extractor.py:7`) stays `heuristic`, so a process without a value, the
   test harnesses and the keyless acceptance stay keyless.
3. README "Run with Docker", the settings table and "Behaviour and limits": three secrets; the keyless outcomes of A15;
   the Compose fallback sentence.
4. README verification record, rewritten from the owner's check (lead's umbrella ledger note, 2026-10-29T04:31Z):
   on `48af3d8` with a test bot and the owner's OpenAI and Anthropic keys, seven services healthy and `migrate`
   exited 0, no restart and no OOM kill; a text message created a task shown live on the board (O1); `/dashboard`
   signed in and the event stream opened (O2); two voice messages transcribed by OpenAI on the first attempt (O4);
   Claude wrote the titles with `TASK_EXTRACTOR=claude` (O6); `/tasks` listed current tasks (O7); a board move showed
   in `/tasks` (O8); after `docker compose restart api` the board reconnected without a reload (O9); on
   `docker compose stop` the bot drained in 0.28 s of its 40 s grace; bot memory 212 MiB of the 512 MiB limit. Not
   run in that check: a voice message without a key (O3) and the local Whisper container (O5). Row names follow
   `docs/plans/2026-10-27-s8-delivery.md` section 7 (rows O1–O9).
5. `docs/development.md` (settings table and the Claude section) and `docs/deployment.md` (any default it states):
   the same defaults.

Decided by the designer:

- **The Compose fallback `${TASK_EXTRACTOR:-heuristic}` stays.** A reviewer who follows the README copies
  `.env.example` and gets Claude; a run without `.env` stays keyless and attempts no paid call; and
  `docker-compose.yml` is cited by the overview, so a change would make it `STALE` and force a re-pin and rebuild of
  M1. The README says the fallback in one sentence (A15).
- **The realtime ticket in the API access log is out of scope.** The owner did not ask for it; it is an application
  change; the logged ticket is single-use, 30 s, and consumed by the very request that logs it
  (`backend/src/taskbot/infrastructure/redis/tickets.py:45`). It stays a LOW observation for the umbrella's final
  review.

D6 for M4 — what a reviewer sees, from the code at `48af3d8`:

| Row | Today | After M4 |
|---|---|---|
| Reviewer copies `.env.example`, sets token, OpenAI and Anthropic keys, sends text | title by the heuristic (`TASK_EXTRACTOR=heuristic`) | title and description by Claude Haiku |
| Same, no Anthropic key | "Task created: <title>" | "Task created: <title>" and the line "The language model was unavailable, so the title is taken from your text." |
| Voice with an OpenAI key / without | transcribed / "Transcription is unavailable right now…" | unchanged |
| README quickstart | names the token and the OpenAI key | names the token, the OpenAI key and the Anthropic key, and the keyless outcomes |
| README verification record | Docker live checks pending on final `main` | the owner's 2026-10-29 Docker check, with O3 and O5 named as not run |

The owner's words above are the acceptance of rows 1, 4 and 5; row 2 is their stated consequence ("what a reviewer
without a key sees", lead's note); row 3 does not change.
