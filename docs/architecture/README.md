# Architecture diagrams

These diagrams describe commit `48af3d82208626cfc7b877e59d9ae57e54a5608e`. They were built once with repo-map, archify, Chrome and pdftoppm.

| Diagram | What it answers | PDF | Interactive HTML | Summary |
|---|---|---|---|---|
| System overview | Which services run, how they connect, and which providers are optional | [PDF](system-overview.pdf) | [HTML](system-overview.html) | [Summary](system-overview.md) |
| Voice message | How acceptance, transcription, storage and the Telegram reply are ordered | [PDF](voice-message.pdf) | [HTML](voice-message.html) | [Summary](voice-message.md) |
| Dashboard live updates | How the dashboard signs in, opens its event stream and reconnects | [PDF](live-updates.pdf) | [HTML](live-updates.html) | [Summary](live-updates.md) |
| Voice job | How claims, retries, failures, notification and recovery work | [PDF](voice-job.pdf) | [HTML](voice-job.html) | [Summary](voice-job.md) |

GitHub displays the PDFs. Open the HTML pages in a browser from a clone to use search, focus, guided views and theme switching.

The overview has twelve components. Its two Telegram boxes represent the same Bot API. Its transcription box represents the selected provider: OpenAI or the optional local whisper.cpp container.

Only the overview stores source citations in its specification and supports the automatic `FRESH` check. That check detects changes to cited files; it does not prove the topology. The three flow diagrams cite their code in the tables below, checked when they were drawn. Their schemas do not support automatic source freshness. The generated summaries are tool output; their status line describes the author's tooling.

## Voice message sources

The dashed refusal and failure messages are alternatives to the successful path. A worker failure stores a failure kind and sends a notification without a task event.

| Step | Source at the pinned commit |
|---|---|
| 1. Telegram delivers a voice update to the bot. | [backend/src/taskbot/entrypoints/bot/handlers.py:104](../../backend/src/taskbot/entrypoints/bot/handlers.py#L104) |
| 2. Refuse more than 120 seconds or 10,000,000 bytes; store nothing. | [backend/src/taskbot/application/accept_voice.py:65](../../backend/src/taskbot/application/accept_voice.py#L65), [backend/src/taskbot/entrypoints/bot/handlers.py:118](../../backend/src/taskbot/entrypoints/bot/handlers.py#L118) |
| 3. Commit a queued job. A duplicate stops unless it is still queued without an acknowledgement. | [backend/src/taskbot/application/accept_voice.py:71](../../backend/src/taskbot/application/accept_voice.py#L71), [backend/src/taskbot/application/accept_voice.py:87](../../backend/src/taskbot/application/accept_voice.py#L87) |
| 4. Reply “Transcribing…” and store the acknowledgement's message ID. | [backend/src/taskbot/application/accept_voice.py:95](../../backend/src/taskbot/application/accept_voice.py#L95), [backend/src/taskbot/application/accept_voice.py:102](../../backend/src/taskbot/application/accept_voice.py#L102) |
| 5. Enqueue transcribe_voice_job on transcription. | [backend/src/taskbot/application/accept_voice.py:112](../../backend/src/taskbot/application/accept_voice.py#L112) |
| 6. The transcription worker receives the job. | [backend/src/taskbot/entrypoints/worker/transcription.py:12](../../backend/src/taskbot/entrypoints/worker/transcription.py#L12) |
| 7. Claim processing, increment the attempt, and acquire a 450-second lease. | [backend/src/taskbot/application/transcribe_voice.py:124](../../backend/src/taskbot/application/transcribe_voice.py#L124), [backend/src/taskbot/domain/limits.py:7](../../backend/src/taskbot/domain/limits.py#L7) |
| 8. Download the file from Telegram. | [backend/src/taskbot/infrastructure/telegram/voice_files.py:30](../../backend/src/taskbot/infrastructure/telegram/voice_files.py#L30) |
| 9. Send audio to the provider and receive its transcript. | [backend/src/taskbot/application/transcribe_voice.py:180](../../backend/src/taskbot/application/transcribe_voice.py#L180) |
| 10. The worker extracts the title and description. | [backend/src/taskbot/application/transcribe_voice.py:184](../../backend/src/taskbot/application/transcribe_voice.py#L184) |
| 11. Commit the task and done job in one transaction. | [backend/src/taskbot/application/transcribe_voice.py:188](../../backend/src/taskbot/application/transcribe_voice.py#L188), [backend/src/taskbot/application/transcribe_voice.py:207](../../backend/src/taskbot/application/transcribe_voice.py#L207) |
| 12. Publish task.created after commit, then enqueue notify_user on notification. | [backend/src/taskbot/application/transcribe_voice.py:208](../../backend/src/taskbot/application/transcribe_voice.py#L208) |
| 13. The notification worker edits the acknowledgement; a missing message falls back to a reply. | [backend/src/taskbot/application/notify_user.py:121](../../backend/src/taskbot/application/notify_user.py#L121) |
| 14. Alternative failure: commit failed(kind), then enqueue only the notification, without an event. | [backend/src/taskbot/application/transcribe_voice.py:212](../../backend/src/taskbot/application/transcribe_voice.py#L212) |

## Dashboard live-update sources

Server-sent events (SSE) carry task changes to the open dashboard. The browser reloads task state whenever a new stream opens, including after a reconnect.

| Step | Source at the pinned commit |
|---|---|
| 1. /dashboard rotates and commits the token, publishes auth.revoked, then replies with the fragment link. | [backend/src/taskbot/entrypoints/bot/handlers.py:62](../../backend/src/taskbot/entrypoints/bot/handlers.py#L62), [backend/src/taskbot/application/dashboard_token.py:39](../../backend/src/taskbot/application/dashboard_token.py#L39) |
| 2. The browser captures the fragment token in localStorage. | [frontend/src/features/session/sessionStore.ts:63](../../frontend/src/features/session/sessionStore.ts#L63) |
| 3. Request POST /api/realtime/tickets with the bearer token. | [backend/src/taskbot/entrypoints/api/realtime.py:34](../../backend/src/taskbot/entrypoints/api/realtime.py#L34) |
| 4. Issue a single-use ticket with SET NX EX 30; return HTTP 201. | [backend/src/taskbot/infrastructure/redis/tickets.py:34](../../backend/src/taskbot/infrastructure/redis/tickets.py#L34), [backend/src/taskbot/entrypoints/api/realtime.py:34](../../backend/src/taskbot/entrypoints/api/realtime.py#L34) |
| 5. Open GET /api/events?ticket=… through nginx, without buffering and with a one-hour read timeout. | [frontend/src/api/eventStream.ts:139](../../frontend/src/api/eventStream.ts#L139), [docker/nginx.conf:20](../../docker/nginx.conf#L20) |
| 6. GETDEL consumes the ticket. Subscribe to events:user:{id} before checking the token version in PostgreSQL. | [backend/src/taskbot/infrastructure/redis/tickets.py:45](../../backend/src/taskbot/infrastructure/redis/tickets.py#L45), [backend/src/taskbot/application/stream_events.py:129](../../backend/src/taskbot/application/stream_events.py#L129), [backend/src/taskbot/application/stream_events.py:133](../../backend/src/taskbot/application/stream_events.py#L133) |
| 7. On stream open, reload every task column. | [frontend/src/features/tasks/useTaskBoard.ts:55](../../frontend/src/features/tasks/useTaskBoard.ts#L55) |
| 8. Bot, workers and API publish changes. Redis delivers task.created, task.updated and task.deleted as data: frames. | [backend/src/taskbot/application/publishing.py:10](../../backend/src/taskbot/application/publishing.py#L10), [backend/src/taskbot/entrypoints/api/realtime.py:62](../../backend/src/taskbot/entrypoints/api/realtime.py#L62) |
| 9. Recheck the token version every 20 seconds; token rotation ends the old stream with auth.revoked. | [backend/src/taskbot/application/stream_events.py:19](../../backend/src/taskbot/application/stream_events.py#L19), [backend/src/taskbot/application/stream_events.py:74](../../backend/src/taskbot/application/stream_events.py#L74) |
| 10. Reconnect with a new ticket after jittered backoff, then resync. | [frontend/src/api/eventStream.ts:167](../../frontend/src/api/eventStream.ts#L167), [frontend/src/lib/backoff.ts:1](../../frontend/src/lib/backoff.ts#L1) |

## Voice-job sources

The four stored statuses are queued, processing, done and failed. Notification completion changes metadata, not the job status. The sweep re-enqueues work and does not change a status itself.

| Transition or card | Source at the pinned commit |
|---|---|
| The bot inserts queued. | [backend/src/taskbot/infrastructure/sqlalchemy/voice_jobs.py:76](../../backend/src/taskbot/infrastructure/sqlalchemy/voice_jobs.py#L76) |
| queued → processing: claim and increment the attempt. | [backend/src/taskbot/domain/voice_jobs.py:111](../../backend/src/taskbot/domain/voice_jobs.py#L111) |
| processing → processing: reclaim after the 450-second lease expires. | [backend/src/taskbot/infrastructure/sqlalchemy/voice_jobs.py:45](../../backend/src/taskbot/infrastructure/sqlalchemy/voice_jobs.py#L45), [backend/src/taskbot/domain/limits.py:7](../../backend/src/taskbot/domain/limits.py#L7) |
| processing → queued: transient attempts below five retry after 2, 4, 8 or 16 seconds. | [backend/src/taskbot/domain/voice_jobs.py:126](../../backend/src/taskbot/domain/voice_jobs.py#L126), [backend/src/taskbot/domain/limits.py:15](../../backend/src/taskbot/domain/limits.py#L15) |
| processing → done: store the task and job together. | [backend/src/taskbot/domain/voice_jobs.py:136](../../backend/src/taskbot/domain/voice_jobs.py#L136), [backend/src/taskbot/application/transcribe_voice.py:188](../../backend/src/taskbot/application/transcribe_voice.py#L188) |
| processing → failed: permanent failure, soft timeout, or the fifth transient failure (transient_exhausted). | [backend/src/taskbot/domain/voice_jobs.py:148](../../backend/src/taskbot/domain/voice_jobs.py#L148), [backend/src/taskbot/application/transcribe_voice.py:225](../../backend/src/taskbot/application/transcribe_voice.py#L225) |
| queued or lease-expired processing → failed(stuck): five attempts have already been used. | [backend/src/taskbot/domain/voice_jobs.py:158](../../backend/src/taskbot/domain/voice_jobs.py#L158) |
| Notification after done/failed: at most five attempts, set notified_at and notification_outcome, preserve status. edited/replied are delivered; blocked/given_up are undelivered. | [backend/src/taskbot/domain/voice_jobs.py:186](../../backend/src/taskbot/domain/voice_jobs.py#L186), [backend/src/taskbot/application/notify_user.py:73](../../backend/src/taskbot/application/notify_user.py#L73), [backend/src/taskbot/application/notify_user.py:91](../../backend/src/taskbot/application/notify_user.py#L91), [backend/src/taskbot/application/notify_user.py:102](../../backend/src/taskbot/application/notify_user.py#L102), [backend/src/taskbot/application/notify_user.py:155](../../backend/src/taskbot/application/notify_user.py#L155) |
| Recovery sweep: every 60 seconds, re-enqueue stale queued/processing and unnotified terminal rows; the next claim decides the result. | [backend/src/taskbot/application/recover_voice_jobs.py:17](../../backend/src/taskbot/application/recover_voice_jobs.py#L17), [backend/src/taskbot/entrypoints/worker/__init__.py:18](../../backend/src/taskbot/entrypoints/worker/__init__.py#L18) |
