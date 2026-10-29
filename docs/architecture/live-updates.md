# Dashboard — Sign in and receive live updates

> **STATUS 2026-10-29: ACTIVE.** Generated from `live-updates.sequence.json` by repo-map; not source-backed.
> Run `repo-map check` before trusting this map; re-pin when it still holds, re-author when a boundary moved.

Type: `sequence` · quality: `showcase` · nodes: 7 · connections: 17

## Components

| id | type | label | detail | tag |
| --- | --- | --- | --- | --- |
| user | external | Telegram user |  |  |
| bot | backend | bot |  |  |
| browser | frontend | Browser |  |  |
| nginx | frontend | nginx |  |  |
| api | backend | api |  |  |
| redis | messagebus | Redis |  |  |
| postgres | database | PostgreSQL |  |  |

## Connections

- user → bot — /dashboard (emphasis)
- bot → postgres — rotate + commit
- bot → redis — auth.revoked (security)
- bot → user — /#token=<token> (return)
- browser → nginx — ticket + bearer (security)
- nginx → api — POST ticket (security)
- api → redis — SET NX EX 30 (security)
- api → browser — 201 + ticket (return)
- browser → nginx — GET events
- nginx → api — ticket in query
- api → redis — GETDEL; subscribe (security)
- api → postgres — check version (security)
- bot → redis — publish event
- redis → api — user event (return)
- api → browser — data: frame (return)
- api → postgres — recheck: 20 s (security)
- browser → api — fresh ticket; retry (dashed)

## Guided views

- **Sign in** (user, bot, postgres, browser)
- **Open the stream** (browser, nginx, api, redis, postgres)
- **Live events and reconnect** (bot, browser, api, redis)

## Cards

### Private /dashboard link

- Capture #token= in localStorage. POST /api/realtime/tickets uses the bearer token; 30 s ticket is consumed once with GETDEL.

### Subscribe before checking

- Redis DB 0: subscribe to events:user:{id}, then check token_version. Recheck every 20 s. /dashboard revokes old streams. Publishers: bot, workers and API.

### Resync and reconnect

- Each open reloads all columns. Errors use a fresh ticket after jittered backoff. Same-origin nginx: /api/events unbuffered, 1 h read timeout. Events: task.created, task.updated, task.deleted.

