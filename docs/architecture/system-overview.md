# AI Task Bot — System overview

> **STATUS 2026-10-29: ACTIVE.** Generated from `system-overview.architecture.json` by repo-map; pinned to revision `48af3d82208626cfc7b877e59d9ae57e54a5608e` of `https://github.com/m1amgn/onebusiness-ai-bot.git`; 19 source citations verified by archify at that revision.
> Run `repo-map check` before trusting this map; re-pin when it still holds, re-author when a boundary moved.

Type: `architecture` · quality: `showcase` · nodes: 12 · connections: 21

## Components

| id | type | label | detail | tag | sources |
| --- | --- | --- | --- | --- | --- |
| redis | messagebus | Redis | DB 0 events · DB 1 jobs |  | `backend/src/taskbot/infrastructure/settings/redis.py:32`; `backend/src/taskbot/domain/jobs.py:11`; `backend/src/taskbot/infrastructure/redis/client.py:25` |
| web | frontend | web | nginx · static + proxy |  | `docker/nginx.conf:20`; `docker-compose.yml:211` |
| api | backend | api | FastAPI · REST + SSE |  | `backend/src/taskbot/entrypoints/api/app.py:61`; `backend/src/taskbot/entrypoints/api/realtime.py:50` |
| browser | external | Browser | React dashboard |  |  |
| bot | backend | bot | aiogram · long polling |  | `backend/src/taskbot/entrypoints/bot/__main__.py:224`; `backend/src/taskbot/entrypoints/bot/handlers.py:156` |
| tg_bot | external | Telegram | updates · replies |  |  |
| anthropic | cloud | Claude Haiku | optional extraction |  | `backend/src/taskbot/infrastructure/extraction/factory.py:55` |
| transcribe | backend | Transcribe | worker-transcribe |  | `backend/src/taskbot/entrypoints/worker/launcher.py:15`; `backend/src/taskbot/application/transcribe_voice.py:122` |
| provider | cloud | Transcription | OpenAI or whisper |  | `backend/src/taskbot/infrastructure/transcription/openai.py:36`; `docker-compose.yml:224`; `backend/src/taskbot/infrastructure/transcription/whisper_server.py:70` |
| tg_workers | external | Telegram | files · edits · replies |  |  |
| notify | backend | worker-notify | replies + beat (60 s) |  | `backend/src/taskbot/entrypoints/worker/__init__.py:15`; `backend/src/taskbot/application/notify_user.py:73` |
| postgres | database | PostgreSQL | users / tasks / voice_jobs |  | `backend/src/taskbot/infrastructure/sqlalchemy/tables.py:70`; `docker-compose.yml:59` |

## Connections

- browser → web — HTTP
- web → api — /api, SSE
- api → postgres — tasks/auth
- api → redis — ticket/pub
- tg_bot → bot — updates
- bot → postgres — tasks/jobs
- bot → redis — jobs/event
- bot → anthropic — extract (dashed)
- redis → transcribe — transcribe
- transcribe → postgres — task/state
- transcribe → tg_workers — file/edit
- transcribe → provider — audio
- transcribe → anthropic — extract (dashed)
- redis → notify — notify
- notify → tg_workers — edit/reply
- notify → postgres — state
- bot → tg_bot — poll/reply
- transcribe → redis — job/event
- notify → redis — sweep
- redis → api — events
- postgres → api — rows

## Guided views

- **Messages in** (tg_bot, bot, postgres, redis, anthropic)
- **Voice through the queues** (redis, transcribe, notify, provider, tg_workers)
- **Live dashboard** (browser, web, api, redis, postgres)

## Cards

### One backend image, six services

- migrate, api, bot, worker-transcribe, worker-notify, seed. Migrate: alembic upgrade head, exit 0 before apps. Only web publishes 127.0.0.1:WEB_PORT (8080).

### One service, two boxes or roles

- Telegram repeats one Bot API. Redis DB 1: transcription, notification. DB 0: events:user:{id}, tickets, bot lock, heartbeat. PostgreSQL: users, inbound_messages, tasks, voice_jobs.

### What is optional

- OpenAI is default; no key means voice unavailable. TRANSCRIPTION_PROVIDER selects OpenAI or CPU whisper.cpp (COMPOSE_PROFILES=whisper). Claude: TASK_EXTRACTOR=claude + key, in bot/worker/seed. seed: tools profile.

