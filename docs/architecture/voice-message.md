# Voice message — Accept, transcribe, notify

> **STATUS 2026-10-29: ACTIVE.** Generated from `voice-message.sequence.json` by repo-map; not source-backed.
> Run `repo-map check` before trusting this map; re-pin when it still holds, re-author when a boundary moved.

Type: `sequence` · quality: `showcase` · nodes: 7 · connections: 16

## Components

| id | type | label | detail | tag |
| --- | --- | --- | --- | --- |
| telegram | external | Telegram |  |  |
| bot | backend | bot |  |  |
| postgres | database | PostgreSQL |  |  |
| redis | messagebus | Redis queues |  |  |
| transcribe | backend | Transcribe |  |  |
| provider | cloud | Provider |  |  |
| notify | backend | worker-notify |  |  |

## Connections

- telegram → bot — voice update (emphasis)
- bot → telegram — IF over cap: stop (dashed)
- bot → postgres — commit queued
- bot → telegram — Transcribing… (return)
- bot → redis — enqueue (emphasis)
- redis → transcribe — deliver (emphasis)
- transcribe → postgres — claim +1
- transcribe → telegram — download file
- transcribe → provider — audio
- provider → transcribe — transcript (return)
- transcribe → postgres — extract; commit task + done (emphasis)
- transcribe → redis — publish; enqueue (emphasis)
- transcribe → postgres — ELSE failed(kind) (dashed)
- transcribe → redis — notify only (dashed)
- redis → notify — notify_user (emphasis)
- notify → telegram — edit ack / reply (return)

## Guided views

- **Accept in the bot** (telegram, bot, postgres, redis)
- **Transcribe in the worker** (redis, transcribe, provider, postgres)
- **Notify** (redis, notify, telegram)

## Cards

### Bot API — Accept once

- Reject >120 s or >10,000,000 bytes. Duplicate stops unless queued without acknowledgement. Store ack id before transcribe_voice_job on transcription.

### worker-transcribe

- OpenAI or whisper-server; 450 s lease. Download, transcribe, extract title + description, then commit task + done. Publish task.created, enqueue notify_user on notification. Dashed failure replaces success: failed(kind), notify only, no event.

### Return to Telegram

- worker-notify edits the acknowledgement to Task created: …. A missing acknowledgement falls back to a reply. Notification retries stop after five attempts.

