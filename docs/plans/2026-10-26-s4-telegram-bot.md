# S4 — Telegram bot

> **STATUS 2026-10-26: ACTIVE — stage design, round 2 (the pre-handoff lens findings folded in, section 10; section 7 accepted by the owner), for the design gate.**

Stage S4 of the umbrella design `docs/plans/2026-10-23-umbrella-assessment.md` (umbrella section 5, S4), read with its amendments 1–3 (umbrella sections 12–14), the S2 design `docs/plans/2026-10-24-s2-domain-core.md`, the S3 design `docs/plans/2026-10-24-s3-redis-adapters.md` and the S9 design `docs/plans/2026-10-26-s9-claude-extraction.md`. The umbrella fixes the scope; this document fixes how S4 delivers it, records the owner's decisions for this stage, and lists where S4 departs from the umbrella's text (section 9, rows Z1–Z13). "Umbrella x.y" names a section of the umbrella design; a bare number names a section of this document. The implementation steps are S4.1–S4.4. The owner's acceptance rows are B1–B19, and the acceptance criteria are C1–C24.

## 1. Problem

The application has its domain core, persistence, queues, events and the Claude extractor, but no way for a person to use them: `yarn dev:bot` exits with "built in stage S4". S4 builds the Telegram bot that turns a private text message into one task and replies with its title, accepts a voice message (checks its limits, answers within a second, records a voice job and queues its transcription), issues the personal dashboard link, and names the active providers on `/start`. S4 also builds the two Telegram adapters the worker (S5) uses later through the ports: `UserNotifier` (send a reply, edit a message, with the error mapping of umbrella 3.6) and `VoiceFileSource` (download a voice file with a size cap).

S4 does not transcribe or notify (S5), does not change the API (S6) or the dashboard (S7), and does not edit `docker-compose.yml` or the Dockerfiles (S8).

## 2. Decisions

The owner's decisions in the S4 grill (2026-10-26). The owner's words are quoted in the original language; the designer's diary (`.tasks/2026-10-26-s4-bot-design.md`) holds each question as asked.

| # | Decision | Owner's words |
|---|---|---|
| S4-D1 | Updates are handled **concurrently** (aiogram's `handle_as_tasks=True`). A normal stop (SIGINT, SIGTERM) waits for the handlers already running, up to 15 s, before the bot closes the database, Redis, the extractor and the Telegram session. An update is lost on a hard kill (SIGKILL, out of memory) while its handler runs; Z1 lists every case and the README states them. Rejected: sequential handling, which makes Telegram redeliver after any crash but lets one slow extraction (up to 8 s) delay every other user's voice acknowledgement. | «A» |
| S4-D2 | Tasks come **only from private chats**. Outside a private chat every command gets the one line "I work only in a private chat."; text and voice are ignored without a reply. Reasons: the dashboard is personal (D-05), a reply in a group would show the task title to everyone, and Telegram's privacy mode delivers only part of a group's messages to a bot. | «A» |
| S4-D3 | Bot replies are **English only**: acknowledgements, refusals, `/start`, and the voice result and failure texts that S5 sends. Task titles and descriptions keep the language of the message (S9). Rejected: Russian only; per-user language from Telegram's `language_code`. | «A» |
| S4-D4 | In a private chat an unknown command gets "Unknown command…" with the usage line, and a photo, sticker, video note, audio file or document gets "I understand text and voice messages only." Neither creates a row. Stated with this question and not objected to (rows B15–B17 show them to the owner): a caption on a voice message is ignored (the transcript is the only text); a forwarded message is the forwarder's own; an edited message is ignored (the bot does not subscribe to `edited_message`; tasks are never edited). | «A» |
| S4-D5 | The reply texts of section 3.6 are accepted as written; replies are plain text with no parse mode; the reply to a text shows the title only. The owner asked whether a local whisper server works when `/start` says "openai (no API key…)"; the answer: one variable selects one provider, with no automatic fallback (umbrella D-09); that line appears only for `openai` with an empty key; the README, not the chat, says how to switch. | «Да» |
| S4-D6 | No per-user rate limit; the README states it as an assumption of the assessment's scale. | «А» |
| S4-D7 | Every text goes to the configured extractor; no length threshold sends short text to the heuristic. | «А» |
| S4-D8 | A live run with the owner's test bot at S4's review (section 3.10). The owner types the token into this worktree's untracked `.env` (umbrella L5). The voice stops at the acknowledgement until S5 exists. The README records the result as verified live. | «А» |
| S4-D9 | Architecture: thin aiogram handlers map a Telegram message to a command and call the existing use cases and a new `AcceptVoiceMessage`; every reply goes through the `UserNotifier` port. Rejected: one dispatcher use case (it duplicates aiogram's routing and grows branches); replies through aiogram's `message.answer` (two send paths, and every handler test needs a fake Telegram session). | «А» |

Designer decisions, announced to the owner before S4-D9 and checked by the gate: an acknowledgement that fails to send does not stop the enqueue (3.4, Z2); a duplicate voice update resumes only a `queued` job without a stored acknowledgement (3.4, Z3); a held polling lock exits at once (3.7, Z5); a normal stop waits up to 15 s (3.8). Round 2 changed one announced value: "10 MB" is 10,000,000 bytes, not 10 MiB, so the accepted refusal text "larger than 10 MB" is exact (Z8).

## 3. Design

### 3.1 Layout

```
backend/src/taskbot/
  domain/limits.py                 + MAX_VOICE_SECONDS = 120, MAX_VOICE_BYTES = 10_000_000          (S4.1)
  domain/errors.py                 + NOTIFIER_ERRORS (tuple of the four notifier errors)             (S4.1)
  application/replies.py           every user-facing bot text of 3.6; S5 adds its texts here         (S4.1)
  application/accept_voice.py      AcceptVoiceMessage, its command and outcomes                      (S4.1)
  application/dashboard_token.py   + dashboard_link(base_url, token)                                 (S4.1)
  infrastructure/telegram/
    notifier.py                    AiogramUserNotifier (UserNotifier)                                (S4.2)
    voice_files.py                 AiogramVoiceFileSource (VoiceFileSource)                          (S4.2)
  infrastructure/redis/bot_lock.py PollingLock, LockKeeper, the key constants                        (S4.3)
  infrastructure/settings/
    web.py                         WebSettingsMixin (web_base_url, moved from SeedSettings)          (S4.3)
    transcription.py               TranscriptionSettingsMixin (provider name, OpenAI key presence)   (S4.3)
    bot.py                         BotSettings                                                       (S4.3)
  entrypoints/bot/
    __init__.py                    BotPorts, build_bot_ports, start_text(settings)                   (S4.4)
    handlers.py                    create_dispatcher(ports, in_flight): router, filters, error hook  (S4.4)
    runtime.py                     InFlightUpdates (drain), PollingWatch, TokenRedactionFilter       (S4.4)
    __main__.py                    the composition root and the run                                  (S4.4)
backend/tests/
  fakes/telegram.py                FakeUserNotifier, FakeVoiceFileSource                             (S4.1)
  fakes/telegram_session.py        FakeTelegramSession (an aiogram BaseSession that records calls)   (S4.2)
  fakes/messaging.py               + FakeJobQueue.fail                                               (S4.1)
  fakes/persistence.py             + a context variable "inside a unit of work" (3.10)               (S4.1)
  acceptance/replay_s4.py          the owner-row replay of section 7                                 (S4.4)
  unit/test_replay_s4.py           runs the replay and checks its rows                               (S4.4)
```

aiogram stays out of `domain` and `application` (import-linter's forbidden contract already lists it). `application/replies.py` holds plain strings, so the bot and S5's notification use case read one copy (Z9). No source file approaches 700 lines; `handlers.py`, the largest, is planned at about 200.

### 3.2 Routing and the handlers (S4.4)

`create_dispatcher(ports: BotPorts, in_flight: InFlightUpdates) -> Dispatcher` builds one router. `Dispatcher(disable_fsm=True)` (no conversation state), and `ports` reaches every handler by name through the dispatcher's workflow data. Handlers are `async def`, read only the Telegram fields they need, and map them to a use-case command. The first matching row wins:

| # | Filter | Handler |
|---|---|---|
| 1 | `from_user` is missing or is a bot | ignored, no reply |
| 2 | chat type is not `private` | a text starting with `/` gets `NOT_PRIVATE`; anything else is ignored (S4-D2) |
| 3 | `Command("start") & F.text` | reply `start_text(settings)` (3.5) |
| 4 | `Command("dashboard") & F.text` | `IssueDashboardToken` → publish → reply with the link (3.5) |
| 5 | text starting with `/` | `UNKNOWN_COMMAND` (S4-D4) |
| 6 | text | the text path (3.3) |
| 7 | voice | the voice path (3.4) |
| 8 | content type in {photo, video, animation, sticker, video note, audio, document, location, contact, poll, venue, dice} | `UNSUPPORTED_MESSAGE` (S4-D4) |
| — | anything else (service messages such as a pinned message or an auto-delete timer change) | ignored |

Rows 1 and 2 come first so that `/dashboard` in a group never reaches rows 4 or 6 (the exploration ran aiogram 3.31.0: without them a group `/dashboard` and any unknown `/command` fall through to the text handler and become tasks). Rows 3 and 4 add `F.text`, because aiogram's `Command` also matches a caption, and a voice with the caption `/dashboard` would otherwise rotate the token instead of being accepted as a voice (lens finding, executed). Only `message` handlers are registered, so aiogram requests `allowed_updates=["message"]` and an `edited_message` never arrives (S4-D4). A caption is never read. `TelegramIdentity(from_user.id, from_user.full_name)` names the user; `MessageRef(chat.id, message_id)` names the message; a forwarded message is the forwarder's.

Every reply goes through `ports.notifier.send_reply(chat_id, text, reply_to_message_id=message_id)`. A notifier error from a reply (`NOTIFIER_ERRORS`) is logged with its class name and swallowed: the work is already committed. The text path's typing action is the one direct aiogram call (`bot.send_chat_action(chat_id, "typing", request_timeout=2)`, every aiogram error suppressed); it stays off the `UserNotifier` port because only the bot uses it (S2's rule: an abstraction earns its place by a second real use).

**The error hook** (`router.errors`) catches any unexpected exception from a handler. Its whole body is one `try … except Exception` that logs only the exception's class name and the update id (never the text, never `exc_info`, because a SQLAlchemy error prints its bound parameters, which hold the message text), and, in a private chat, tries one `SOMETHING_WENT_WRONG` reply. The hook never raises: a raising hook makes aiogram log the whole exception chain (lens finding, executed). Work that already committed never reaches the hook (3.3, 3.4), so "Please try again" is never sent for stored work.

### 3.3 The text path (S4.4)

```
typing action (2 s) ─► CreateTasksFromMessage.execute(identity, ref, kind=text, text)
                         (extract with the 8 s deadline, then one unit of work, then commit; S2)
   EmptyMessageError ─► reply EMPTY_TEXT; no row, no extraction
   DuplicateMessage  ─► nothing (no reply, no event)
   TasksCreated      ─► reply TASK_CREATED (+ DEGRADED_LINE when message.degraded)
                     ─► publish_events(created.events)
```

**Reply, then publish** (Z12). Umbrella 3.6 publishes before it replies; S4 replies first, because the person waits on the reply and a publication against an unreachable Redis can take a second (S3's one-second bound); S3 section 8 leaves this order to S4. The dashboard refetches on every reconnect (D-08), so a late event costs nothing.

No unit of work is open during the extraction or the reply: `CreateTasksFromMessage.execute` extracts before it opens one and closes it before it returns (L2). With `TASK_EXTRACTOR=claude` and no key, the extractor is the heuristic marked degraded (S9), so every text reply carries `DEGRADED_LINE`; `/start` names that state.

**The crash window** (umbrella 3.6, changed by Z1). A crash after the commit and before the reply leaves the task stored, sends no reply and publishes no event; the dashboard shows the task on its next fetch. Under S4-D1 Telegram does not redeliver that update. If the same update does arrive again (a second poller, a manual replay), the unique key returns `DuplicateMessage` and nothing is sent. C7 tests that duplicate-key rule; it does not model the crash.

### 3.4 The voice path: `AcceptVoiceMessage` (S4.1)

```python
@dataclass(frozen=True, slots=True)
class AcceptVoiceCommand:
    identity: TelegramIdentity
    ref: MessageRef
    file_id: str
    file_unique_id: str
    duration_seconds: int
    file_size: int | None

VoiceRefused(reason: Literal["too_long", "too_large"])
VoiceAccepted(job_id: int, acknowledged: bool, enqueued: bool)
VoiceAlreadyAccepted(job_id: int)

class AcceptVoiceMessage:
    def __init__(self, uows, register: RegisterOrGetUser, notifier: UserNotifier,
                 queue: JobQueue, clock: Clock) -> None: ...
    async def execute(self, command: AcceptVoiceCommand) -> VoiceRefused | VoiceAccepted | VoiceAlreadyAccepted
```

The steps, in the order of umbrella 3.6, with no unit of work open across the Telegram call or the broker call (L2):

```
0  refuse   duration_seconds > MAX_VOICE_SECONDS            -> VoiceRefused("too_long")
            file_size is not None and > MAX_VOICE_BYTES      -> VoiceRefused("too_large")      no I/O, no row
1  UoW A    register.within(identity); voice_jobs.add_or_get(NewVoiceJob); commit
            created = False and (status != queued or ack_message_id is set) -> VoiceAlreadyAccepted
2  ack      notifier.send_reply(chat, VOICE_ACK, reply_to_message_id=message_id) -> ack id
            NOTIFIER_ERRORS: log the class name; acknowledged = False; go to step 4
3  UoW B    voice_jobs.set_ack_message_id(job, ack, now); commit
            False (another copy stored one first): log; acknowledged = True; go on
            any other Exception: log the class name; go on        (the row is already committed)
4  enqueue  queue.enqueue(TranscriptionJob(voice_job_id=job))
            EnqueueError: log; return VoiceAccepted(enqueued=False); the row stays queued
5  UoW C    voice_jobs.mark_enqueued(job, now); commit
            any Exception: log the class name; return VoiceAccepted (the job is queued)
```

- **Limits.** `MAX_VOICE_SECONDS = 120` and `MAX_VOICE_BYTES = 10_000_000` join `domain/limits.py`, the umbrella's "code constants" (umbrella 3.7). S5 passes the same `MAX_VOICE_BYTES` to `VoiceFileSource.fetch`. A voice of exactly 120 s or exactly 10,000,000 bytes passes; a voice without `file_size` passes, and S5 checks the real bytes.
- **After step 1 nothing escapes.** Steps 3 and 5 catch `Exception` (never `BaseException`, so cancellation propagates) because the job is already stored; an error there must not reach the error hook, which would tell the user to try again and so create a second job (lens finding). `ClientDecodeError` and other aiogram errors never reach step 2's caller: the notifier maps them (3.9).
- **Duplicates** (umbrella 3.5). `add_or_get` returns the stored row on a conflict. The job resumes from step 2 only when it is still `queued` and has no acknowledgement; otherwise the update stops, because a job that is `processing`, `done` or `failed` is already in S5's hands and a second "Transcribing…" would contradict it (Z3). Two copies of one update running at the same moment can both see "queued, no acknowledgement"; both then send an acknowledgement and enqueue; S5's claim drops the second job, and one acknowledgement stays unedited. This is the umbrella's bounded duplication, accepted. A `queued` job with an acknowledgement but no successful enqueue (a crash between steps 3 and 4) is left to S5's sweep, which re-enqueues a `queued` row whose `last_enqueued_at` is older than 460 s.
- **A failed acknowledgement** (Z2). The job is still queued; the user gets the result as a new reply, because S5's notification falls back to a reply when `ack_message_id` is null (umbrella 3.6). Stopping instead would leave the job to the sweep for 460 s. A later duplicate of that update meets "queued, no acknowledgement" and resumes: a second enqueue, bounded as above.
- **A failed enqueue** (umbrella 3.6). The row stays `queued` with its acknowledgement; S5's sweep repairs it. S4 proves its half: after the failure, `stamp_stale_queued` at `now + 461 s` returns the job (C10).
- **The provider is not checked here.** With `TRANSCRIPTION_PROVIDER=openai` and no key the bot still acknowledges; S5 answers `provider_unavailable` (umbrella 3.6), and `/start` already says so.
- **No events** on this path; tasks are created by S5.

The voice handler maps `VoiceRefused` to `VOICE_TOO_LONG` or `VOICE_TOO_LARGE` and sends nothing for the other outcomes (the acknowledgement was the reply).

### 3.5 `/dashboard` and `/start` (S4.4)

`/dashboard` in a private chat runs `IssueDashboardToken.execute(identity)` (it registers a first-time user and rotates the token; S2), publishes its events (`auth.revoked`) and then replies `DASHBOARD` with `dashboard_link(web_base_url, token)`. The publication goes first here, as umbrella 3.6 writes it, so the open dashboard streams of that user close before the new link arrives. `dashboard_link` (`application/dashboard_token.py`) returns `f"{base_url}/#token={token}"`; the seed switches to it, so the link format has one implementation (Z10). The token and the reply text are never logged.

`/start` needs no database. `start_text(settings)` composes the `START` text of 3.6: `<provider>` is the `TRANSCRIPTION_PROVIDER` value (`whisper_server`, `openai` or `fake`), or `openai (no API key, voice messages are unavailable)` for `openai` without a key; `<extractor>` is `heuristic`, `claude` (with a key), or `heuristic (no Anthropic API key)` for `claude` without one. "Without a key" means `has_anthropic_key(settings)` (S9) and `has_openai_key(settings)` (3.7) are false; both treat a blank key as missing. The root logs one warning at start when the provider is `openai` without a key, as `open_extractor` already does for Claude (umbrella 3.7: "logged once at start and named by `/start`").

### 3.6 Reply texts (S4-D5, S4-D3)

All in `application/replies.py`, plain text, English:

| Name | Text |
|---|---|
| `START` | `Send me a text or a voice message and I will turn it into a task.` / `/dashboard – a private link to your task board.` / `Transcription: <provider>. Task extraction: <extractor>.` |
| provider without key | `openai (no API key, voice messages are unavailable)` |
| extractor without key | `heuristic (no Anthropic API key)` |
| `TASK_CREATED` | `Task created: <title>` |
| `DEGRADED_LINE` | `The language model was unavailable, so the title is taken from your text.` |
| `EMPTY_TEXT` | `There is no text to make a task from.` |
| `VOICE_ACK` | `Transcribing your voice message…` |
| `VOICE_TOO_LONG` | `This voice message is longer than 2 minutes. Please send a shorter one.` |
| `VOICE_TOO_LARGE` | `This voice message is larger than 10 MB. Please send a shorter one.` |
| `DASHBOARD` | `Your task board: <link>` / `Keep this link private. A new /dashboard link signs out other devices.` |
| `NOT_PRIVATE` | `I work only in a private chat.` |
| `UNKNOWN_COMMAND` | `Unknown command. Send a text or a voice message to create a task; /dashboard opens your tasks.` |
| `UNSUPPORTED_MESSAGE` | `I understand text and voice messages only.` |
| `SOMETHING_WENT_WRONG` | `Something went wrong. Please try again.` |

The longest reply is far below Telegram's 4,096-character limit (a title is at most 120 characters).

### 3.7 Settings, the polling lock and the heartbeat (S4.3)

**Settings.** `BotSettings(BaseAppSettings, DatabaseSettingsMixin, RedisSettingsMixin, ExtractorSettingsMixin, WebSettingsMixin, TranscriptionSettingsMixin)` plus `telegram_bot_token: SecretStr`, required. A field validator calls aiogram's `validate_token` and turns its `TokenValidationError` (which is not a `ValueError`) into `ValueError("malformed token") from None`, so `load_settings` prints `Invalid configuration: TELEGRAM_BOT_TOKEN: Value error, malformed token` and exits 2 without echoing the value. Two mixins are new, both `BaseModel` (the module guide's rule):

- `WebSettingsMixin.web_base_url`, moved with its validator from `SeedSettings`, which now includes the mixin; the seed's settings test keeps passing.
- `TranscriptionSettingsMixin`: `transcription_provider: TranscriptionProvider = TranscriptionProvider.WHISPER_SERVER` (the domain enum is the one list of names; the default matches `.env.example`), `openai_api_key: SecretStr | None = None`, and `has_openai_key(settings)` (stripped, non-empty), mirroring S9's `has_anthropic_key`. S5's transcribe-only model includes the same mixin and adds its own fields (Z7). An unknown provider name stops the bot at start, as umbrella 3.7 asks of every entry point.

`.env.example` gains no key. `tests/unit/test_env_example.py` adds `BotSettings`, built with `telegram_bot_token=SecretStr("123456:synthetic")` (the example's empty token is dropped by `env_ignore_empty`, and the field is required), and adds `TRANSCRIPTION_PROVIDER` to the keys that must equal their defaults.

**The polling lock** (umbrella 3.1, S3-Y8). `PollingLock` wraps redis-py's asyncio `Lock` on `namespaced(namespace, POLL_LOCK_KEY)` with `timeout=LOCK_TTL_SECONDS (30)`, `blocking=False` and `thread_local=False`: the value is a random owner token, renewal (`reacquire`) and release are compare-and-set scripts, and an expired or foreign lock raises `LockNotOwnedError` instead of being extended or deleted. Every Redis call of the lock and the heartbeat runs under `asyncio.timeout(REDIS_CALL_TIMEOUT_SECONDS = 4)`, because one call against a silent Redis otherwise blocks about 20 s (the client's socket timeout times its retries; lens finding, executed).

- **At start**, before the database check, the Telegram session and the extractor: `acquire()`. If another process holds the lock, the bot prints `Another bot instance holds the polling lock <key>; it expires in <n> s.` and exits 1 (umbrella 3.1; Z5). It does not wait: a live holder keeps renewing, and a crashed holder's key expires within 30 s. Right after `acquire()` the heartbeat is written once.
- **While polling**, `LockKeeper` renews every `LOCK_RENEW_SECONDS (10)`. After each successful renewal it writes the heartbeat `namespaced(namespace, HEARTBEAT_KEY)` with the current time and a 30 s TTL, but only while the polling watch (3.8) reports Telegram healthy. It stops the bot (exit 1) when the lock is lost (`LockNotOwnedError`) or when two renewals in a row fail (a renewal that times out counts as failed): the last success is then at most `2 × 10 + 4 = 24 s` old, inside the 30 s TTL, so no second poller can take the lock while this one still polls.
- **At exit**, after the drain, the keeper stops, the heartbeat key is deleted and the lock is released; each call is bounded by the same timeout, and a release that fails (`LockError`, a timeout) is logged and ignored, because the key then expires by itself.
- `POLL_LOCK_KEY = "bot:poll"`, `HEARTBEAT_KEY = "heartbeat:bot"`, `LOCK_TTL_SECONDS`, `LOCK_RENEW_SECONDS` and `REDIS_CALL_TIMEOUT_SECONDS` are module constants; `PollingLock` and `LockKeeper` take the timings as constructor arguments so tests use short ones. S8's probe reads `HEARTBEAT_KEY`.

### 3.8 The composition root and the run (S4.4)

`python -m taskbot.entrypoints.bot` (`yarn dev:bot`). The root follows the seed's pattern: each resource is registered in one `AsyncExitStack` right after it is built, so a failed start closes what exists (L1). The bot owns its signals: `start_polling(handle_signals=False)`, and the root installs SIGINT and SIGTERM handlers before it takes the lock, so a signal during start-up closes the stack and releases the lock instead of ending the process with Python's default action.

```
load_settings(BotSettings); logging with TokenRedactionFilter
install signal handlers (first signal: stop; second signal: skip the drain)
redis      = create_redis_client(redis_url, client_name="bot")        stack: aclose
lock       = PollingLock(redis, namespace); acquire or exit 1          stack: release, delete heartbeat
engine     = create_engine_and_sessions(database_url)  (pool 5)       stack: dispose
             check: SELECT 1 FROM inbound_messages LIMIT 0, else exit 1 "Database is unavailable or not migrated"
app        = create_celery_app(broker_url, namespace)                 stack: app.close
queue      = CeleryJobQueue(app)                                      stack: queue.close()   (drains producer threads)
extractor  = open_extractor(settings, deadline_seconds=8)             stack: enter context
bot        = Bot(token)  (default session, plain text)                stack: session.close
             await bot.me()  (Unauthorized -> exit 1 "Telegram rejected TELEGRAM_BOT_TOKEN.";
                              other Telegram error -> exit 1 "Cannot reach api.telegram.org.")
             await bot.delete_webhook(drop_pending_updates=False)
ports      = build_bot_ports(settings, uows, extractor, AiogramUserNotifier(bot), queue, publisher)
dispatcher = create_dispatcher(ports, in_flight)
keeper     = LockKeeper(lock, watch, on_stop=...) started; any end other than cancellation stops polling with exit 1
await dispatcher.start_polling(bot, handle_signals=False, close_bot_session=False,
                               tasks_concurrency_limit=BOT_CONCURRENCY)
await in_flight.drain(DRAIN_SECONDS); cancel and await what is left
stop the keeper; leave the stack (LIFO: session, extractor, queue, app, engine, lock, Redis)
exit 0, or 1 when the keeper or the watch stopped the bot
```

- **One client of each kind** (L1): one engine, one Redis client named `bot`, one extractor with the 8 s deadline (`BOT_EXTRACTION_DEADLINE_SECONDS = 8`, a root constant like the seed's), one Celery producer, one Telegram session; all handlers share them. The bot does not import `taskbot.entrypoints.worker`, whose import loads worker settings.
- **Bounded concurrency.** `BOT_CONCURRENCY = 10` handlers at most. Without a bound, a backlog of 100 updates after downtime starts 100 extractions and 100 replies at once; Telegram's flood control then answers 429, the swallowed replies make users resend, and each resend is a second task (lens finding). Updates beyond the bound wait unconfirmed.
- **The drain** (S4-D1). `InFlightUpdates` is a `BaseMiddleware` subclass registered as an outer middleware on `dispatcher.update`. It adds the running handler task to a set and removes it with `task.add_done_callback`, not in `finally`, so the error hook, which aiogram runs outside this middleware, is drained too (lens finding, executed). `drain(timeout)` waits for the set; the root passes `DRAIN_SECONDS = 15`, and tests pass a short timeout. `close_bot_session=False`, because a draining handler still needs the session to reply; the stack closes it afterwards. A second SIGINT or SIGTERM skips the rest of the drain.
- **The database check** runs before polling starts, so a stopped or unmigrated database never makes the bot consume and lose the Telegram backlog (lens finding).
- **The polling watch** (`PollingWatch`) is a request middleware on the bot session (aiogram's `BaseRequestMiddleware`). It records the time of the last successful `getUpdates`. aiogram retries a failing `getUpdates` forever (a revoked token, a webhook, a second poller with the same token, a network outage) and only logs; so when no `getUpdates` has succeeded for `POLLING_FAILURE_SECONDS = 60`, the keeper stops writing the heartbeat and stops the bot with exit 1 and the line `Telegram polling has failed for 60 s (<exception class>).` The `delete_webhook` call at start removes the commonest cause. A healthy long poll returns at least every 10 s (`polling_timeout`).
- **Logs never carry the token.** aiohttp's error text includes the request URL, and the Bot API puts the token in every URL (`/bot<token>/…`, `/file/bot<token>/…`; executed). aiogram logs such errors itself while it retries `getUpdates`. `TokenRedactionFilter`, installed on the root log handler, replaces the configured token value with `***` in every formatted record, the exception text included. The adapters additionally raise their domain errors `from None` (3.9).
- **Shutdown budget**: `DRAIN_SECONDS` plus at most 5 s of closes (three bounded Redis calls, aiogram's 0.25 s session close, the producer pool); S8 sets the bot's `stop_grace_period` to at least 25 s (Z1).

### 3.9 The Telegram adapters (S4.2)

**`AiogramUserNotifier(bot)`** implements `UserNotifier`. `send_reply` calls `send_message(chat_id, text, reply_parameters=ReplyParameters(message_id=…, allow_sending_without_reply=True), request_timeout=10)` and returns the new `message_id`; `edit_message` calls `edit_message_text(text=…, chat_id=…, message_id=…, request_timeout=10)`. No parse mode. The mapping (umbrella 3.6) matches Telegram's own descriptions, which aiogram passes through unchanged; the wording is checked against Telegram only in the live run (S4-D8):

| Telegram outcome | Result |
|---|---|
| `TelegramBadRequest` whose description contains `message is not modified` (edit) | normal return |
| `TelegramBadRequest` containing `message to edit not found` or `message can't be edited` (edit) | `MessageGoneError` |
| `TelegramRetryAfter` | `RetryAfterError(retry_after)` |
| `TelegramForbiddenError` (blocked, kicked, deactivated) | `RecipientBlockedError` |
| any other `AiogramError` (network, server, other bad request, `ClientDecodeError` for an HTML or malformed body), `TimeoutError`, aiohttp `ClientError` | `NotificationError` |

`ClientDecodeError` is an `AiogramError` but not a `TelegramAPIError`, and its text holds the whole response body; mapping the base class keeps it inside the port (lens finding, executed). Descriptions are matched case-insensitively in one table in the adapter. `asyncio.CancelledError` always propagates. Every mapped error is raised `from None` with the original class name as its message.

**`AiogramVoiceFileSource(bot)`** implements `VoiceFileSource.fetch(file_id, *, max_bytes) -> bytes` inside `asyncio.timeout(30)` (umbrella 3.6: download ≤ 30 s): `get_file(file_id, request_timeout=10)`; a declared `file_size` above `max_bytes` raises `VoiceFileTooLargeError` before any download; `download_file(file_path, destination=CappedBuffer(max_bytes))`, where `CappedBuffer` is an `io.BytesIO` subclass whose `write` raises `VoiceFileTooLargeError` once the bytes written exceed the cap (aiogram has no size cap, the declared size can be missing, and `download_file` also calls `flush` and `seek`). A `TelegramBadRequest` saying the file is too big maps to `VoiceFileTooLargeError`; any other `AiogramError`, an aiohttp error, a `TimeoutError` or a missing `file_path` maps to `VoiceFileUnavailableError`, raised `from None`. aiohttp's download errors carry the token-bearing URL in their text (executed), so the adapter never logs them and never chains them.

### 3.10 Tests, the replay and the live run

Unit tests run on fakes; no test calls Telegram.

- **Fakes.** `FakeUnitOfWork` sets a context variable "inside a unit of work" in `__aenter__` and clears it in `__aexit__`. `FakeUserNotifier` records `notifier.send_reply` and `notifier.edit_message` in the shared `Calls`, returns increasing message ids, can raise a given error on its n-th call, and asserts the context variable is clear on every call, so a Telegram call inside a unit of work fails the test where it happens (L2); because the variable is per task, concurrent handlers do not trip each other (the store's single lock would). `FakeVoiceFileSource` returns fixture bytes and makes the same check. `FakeJobQueue` gains `fail: bool` (raises `EnqueueError`). `FakeTelegramSession` subclasses aiogram's `BaseSession`, answers `getMe`, records each method and its arguments, can hold a call open, and produces real aiogram exceptions through `check_response` with scripted status codes and bodies.
- **Handler tests** build `create_dispatcher(ports, in_flight)` on fakes and a `Bot` on `FakeTelegramSession`, and feed `Update` objects through `Dispatcher.feed_update`. The drain test (C13) runs through `start_polling` with a scripted `getUpdates` batch, because a direct `feed_update` makes the test's own task the handler task.
- **Root tests** monkeypatch the factories the root imports (`create_engine_and_sessions`, `create_redis_client`, `open_extractor`, the Bot session), as the seed's CLI tests do.
- **Lock tests** run on real Redis with `redis_session.child()` namespaces (R-ISO) and short timings; the stalled-Redis case uses a loopback server that accepts and never answers.
- **Subprocess tests** start `python -m taskbot.entrypoints.bot` with `TASKBOT_ENV_FILE=/dev/null`, a synthetic token, real Redis in a child namespace, and either the lock already held (C18), a closed Redis port (C19) or a closed database port (C20); each expects exit 1, its one-line message, no traceback, and no request to Telegram (the lock, Redis and database checks run before the Telegram session exists). They use `tests.processes.run_process` with `os.environ | {…}`.
- **The owner-row replay** `backend/tests/acceptance/replay_s4.py` drives `create_dispatcher` with fakes and the fake session through rows B1–B19 of section 7 and prints, per row, the replies sent, the rows written, the jobs enqueued and the events published. It is offline, writes nothing outside its in-memory fakes, and inserts `BACKEND_ROOT` into `sys.path` before importing `tests.*`, as `replay_s3.py` does. Command: `uv run --locked --directory backend python tests/acceptance/replay_s4.py`. `tests/unit/test_replay_s4.py` runs the replay's row function and compares each row's facts (reply texts, rows, jobs, events) with a frozen expected list, as data.
- **The live run** (S4-D8), at review, on the owner's word: the owner types a test bot's token into this worktree's untracked `.env`; the agent runs `yarn infra:up`, `yarn db:migrate` (the shared development head; recorded in the diary) and `yarn dev:bot` with `REDIS_NAMESPACE=s4live:`, so the lock, the heartbeat and the queued transcription message stay under one prefix. The owner sends `/start`, a text, a voice, `/dashboard`, a sticker and, if a group is at hand, `/dashboard` there; the agent compares replies and rows with section 7, records the reply latencies (umbrella 3.6: under 1 s for the voice acknowledgement, under 1 s for a heuristic text), and records the result and the date in the README. Ctrl-C then checks the drain and the release (the next `yarn dev:bot` starts at once). Cleanup, recorded in the diary: the agent deletes the `s4live:*` keys in both Redis databases and the live run's `voice_jobs` row; the user row and the text tasks stay as development data. No worker runs against that queue (S3's module guide forbids an unfinished worker there).

### 3.11 Documentation (S4.4)

- `backend/AGENTS.md` gains a "Bot runtime" section: the entry point, handler order and the private-chat rule, the voice path's units of work and what each step catches, the drain, the lock, heartbeat and polling watch, the adapters' error mapping and the token rules, the fakes and the context variable, and the replay command.
- `README.md`: the intro sentence about later stages (lines 7–8); the `yarn dev:bot` row (line 54: needs `TELEGRAM_BOT_TOKEN`; one instance per token); the worker-token sentence (line 188) kept apart from the bot's required token; a "Bot" section (what text, voice, `/dashboard` and `/start` do; private chats only and Telegram's privacy mode; English replies; ignored edits and captions; no rate limit; the cases of Z1 in which an update is lost; how to switch `TRANSCRIPTION_PROVIDER`); the live-verification sentence (lines 266–267) replaced by the live run's result, or by "Telegram: not verified live" if the run does not happen.
- `package.json`: `dev:bot` becomes `uv run --locked --directory backend python -m taskbot.entrypoints.bot`. `scripts/not-built.mjs` and its test in `scripts/tooling.test.mjs` are deleted, because no command uses the stub any more (Z11).

## 4. Implementation steps

Each step is one commit and is verified before the next. S6 runs in parallel; when S6 integrates into `umbrella/assessment`, the implementer merges the umbrella tip before the next implementation commit and records it.

| Step | Scope | Verification |
|---|---|---|
| S4.1 | voice limits, `NOTIFIER_ERRORS`, `application/replies.py`; `AcceptVoiceMessage`; `dashboard_link` (the seed uses it; its link test still passes); the fakes of 3.10 except the session; tests C8–C11 and a `dashboard_link` test | ruff, ruff format, mypy, lint-imports, `pytest tests/unit` |
| S4.2 | `infrastructure/telegram/` adapters; `FakeTelegramSession`; tests C14, C15 | same |
| S4.3 | `WebSettingsMixin`, `TranscriptionSettingsMixin`, `BotSettings`, the env-example test; `PollingLock`, `LockKeeper`; tests C16, C17 | same, plus `pytest -m integration` for the lock |
| S4.4 | `entrypoints/bot/`; `yarn dev:bot`; stub removal; `replay_s4.py` and its test; README and `backend/AGENTS.md`; tests C1–C7, C12, C13, C18–C23 | `yarn verify` (with `yarn infra:up`); the replay |

## 5. Acceptance criteria

Every criterion is a test in `yarn verify` unless it says otherwise.

| # | Criterion |
|---|---|
| C1 | A private text creates one message row and one task; the reply is `Task created: <title>`; one `task.created` event is published after the reply. A one-word text reaches the configured extractor (a counting fake extractor sees it; S4-D7). |
| C2 | With a degraded extraction the reply carries `DEGRADED_LINE` as its second line, and the row records `degraded`. |
| C3 | A text without a letter or digit gets `EMPTY_TEXT`; no row, no extraction. |
| C4 | `/start` with default settings shows `Transcription: whisper_server. Task extraction: heuristic.`; with `openai` and `claude` both without keys (blank keys included) it shows both "no key" variants; the root logs one warning for `openai` without a key. |
| C5 | `/dashboard` in a private chat rotates the token, publishes `auth.revoked` before the reply, and replies with `dashboard_link`; `/dashboard` and `/start` in a group get `NOT_PRIVATE`; a group text or voice gets no reply and no row. |
| C6 | An unknown command gets `UNKNOWN_COMMAND`; a sticker gets `UNSUPPORTED_MESSAGE`; a service message (an auto-delete timer change) gets nothing; none writes a row. The dispatcher requests only `message` updates. |
| C7 | The same text update fed twice — the first time with a notifier that raises after the commit — leaves one message and one task and sends no reply the second time (the duplicate-key rule of 3.3). |
| C8 | A fresh voice: the recorded order is `uow.open, users.upsert, voice_jobs.add_or_get, uow.commit, uow.close, notifier.send_reply, uow.open, voice_jobs.set_ack, uow.commit, uow.close, queue.enqueue, uow.open, voice_jobs.mark_enqueued, uow.commit, uow.close`, and the fake notifier's context-variable check passes (L2). |
| C9 | Voice of 121 s → `too_long`; `file_size` of 10,000,001 → `too_large`; 120 s and 10,000,000 bytes exactly, and a missing `file_size`, are accepted. Refusals write no row and enqueue nothing. |
| C10 | An enqueue failure after the acknowledgement returns normally, leaves the row `queued` with its `ack_message_id`, and `stamp_stale_queued(now + 461 s)` returns it. |
| C11 | Duplicates: a stored acknowledgement → `VoiceAlreadyAccepted`, nothing sent, nothing enqueued; a `queued` job without one → the acknowledgement, the stored id and the enqueue (resume); a `processing` or terminal job without one → nothing. A failed acknowledgement still enqueues and leaves `ack_message_id` null; a later duplicate of it resumes (a second enqueue, accepted). A forced failure at `voice_jobs.set_ack` still enqueues; a forced failure at `voice_jobs.mark_enqueued` returns `VoiceAccepted`; neither sends `SOMETHING_WENT_WRONG`. |
| C12 | L1: the root built once serves two updates; each factory (engine, Redis client, extractor, Telegram session) was called once and both updates used the same objects; at exit they close in the order of 3.8. |
| C13 | Drain, through `start_polling` with a scripted batch: a handler still running when the stop signal arrives completes and replies before the session closes; an error-hook reply still sending is drained too; a handler that outlives the drain timeout is cancelled; a second signal skips the drain. |
| C14 | The notifier maps every row of the table in 3.9 on a fake session, including a 502 with an HTML body and a 200 with a malformed body; `send_reply` returns the new message id; no raised error carries the response body. |
| C15 | The file source returns the bytes; a declared or actual size above `max_bytes` raises `VoiceFileTooLargeError`; network, server, stalled-download and missing-path cases raise `VoiceFileUnavailableError` within the 30 s budget (a short budget in the test); no raised error or log record contains the token (a download error whose URL carries a synthetic token). |
| C16 | `BotSettings` rejects a malformed token (exit 2, the variable named, the value not echoed) and an unknown provider; `.env.example` passes the env-example test; the seed keeps its `WEB_BASE_URL` validation. |
| C17 | On real Redis: a second `acquire` is refused; renewal keeps the TTL; a lock deleted or taken by another token is reported lost on the next renewal; two failed renewals against a stalled server stop the keeper within `2 × renew + call timeout`; the heartbeat is written at `acquire`, refreshed only after a successful renewal while the watch is healthy, and deleted at a clean exit; release never deletes another owner's lock and tolerates an expired one. |
| C18 | The subprocess with the lock already held exits 1 with the lock message and no traceback. |
| C19 | The subprocess with Redis unreachable exits 1 with `Redis is unavailable at <host>:<port>` (host and port only) and no traceback. |
| C20 | The subprocess with the database unreachable exits 1 with `Database is unavailable or not migrated` and releases the lock. |
| C21 | Start-up on a fake session: `getMe` 401 exits 1 with `Telegram rejected TELEGRAM_BOT_TOKEN.`; a network error exits 1 with `Cannot reach api.telegram.org.`; neither prints the token. The keeper ending with an unexpected exception stops polling with exit 1. `getUpdates` failing for the watch period (409, a short period in the test) stops writing the heartbeat and exits 1. |
| C22 | Handler edge cases: a message without `from_user` or from a bot gets nothing; the typing action is sent with a 2 s timeout and a hanging or failing typing call does not stop the reply; the error hook replies `SOMETHING_WENT_WRONG` once in a private chat, and when its own reply raises `RuntimeError("<chat text>")` no log record contains that text; a voice with the caption `/dashboard` is accepted as a voice and the token does not rotate. |
| C23 | `TokenRedactionFilter`: a record that logs a `TelegramNetworkError` built from an aiohttp error whose URL carries the synthetic token is written with `***` instead. |
| C24 | `replay_s4.py` prints rows B1–B19, and `test_replay_s4.py` finds every row's facts equal to the frozen expected list. The live run (S4-D8, on the owner's word, outside `yarn verify`) matches section 7 for the rows the owner sends, and the README records it with the measured latencies. |

## 6. Change profile

- **A new process and an external service** (the bot, Telegram's Bot API): there is no `docs/architecture/`; umbrella 3.1 already draws the bot and its Telegram link, so the map holds. S4 adds no store.
- **Authorization and key material**: `/dashboard` issues a personal token, only in a private chat, and the token and link are never logged. `TELEGRAM_BOT_TOKEN` is a `SecretStr`; it appears in every Bot API URL, so the adapters raise without chaining and the root's log filter redacts it (3.8, 3.9, C15, C23). The live run uses the owner's token from the untracked `.env` (L5).
- **Concurrency**: bounded concurrent handlers (S4-D1, 3.8), duplicate updates (the unique keys of S2; C7, C11), the polling lock with an owner token and bounded Redis calls (C17), the drain (C13), and the crash windows of 3.3 and 3.4.
- **Public or serialized shapes**: none change. `TranscriptionJob` is S3's; events are S2's. The `/start` text is not a contract.
- **Shared code**: `SeedSettings` loses its own `web_base_url` field to a mixin, and the seed builds its link through `dashboard_link` (`graphify affected`: the seed root and its tests only); `FakeUnitOfWork` gains a context variable that existing tests do not read. `UserNotifier` and `VoiceFileSource` have no dependents today.
- **Data rows**: the census found none (the development database has no tables, 2026-10-26); the live run writes development rows and removes its voice job (3.10). No schema or migration change. Not touched: money.

## 7. Owner acceptance (D6)

The census of stored rows found none: the shared development database has no tables yet (2026-10-26), and real chat content never enters the repository. The rows are synthetic messages chosen for the cases most likely to surprise. "Today" is the same for every row: `yarn dev:bot` exits with "built in stage S4", so the bot does not answer. The settings are the defaults (`whisper_server`, `heuristic`) unless a row says otherwise. `replay_s4.py` prints these rows from the implemented code (C24). Two failure paths are internal and covered by criteria instead of rows: an acknowledgement that fails to send (C11) and a broker that refuses the enqueue (C10).

| # | The person sends (private chat unless stated) | After S4: reply | After S4: stored and queued |
|---|---|---|---|
| B1 | `Buy milk` | `Task created: Buy milk` | one message, one task; `task.created` published |
| B2 | `Call the bank about the card` while Claude is unavailable | `Task created: Call the bank about the card` + `The language model was unavailable, so the title is taken from your text.` | one task; the message is marked degraded |
| B3 | `🙂👍` | `There is no text to make a task from.` | nothing |
| B4 | a 35 s voice | `Transcribing your voice message…` (as a reply to the voice) | one voice job, queued, with the acknowledgement id; one transcription job queued |
| B5 | a 150 s voice | `This voice message is longer than 2 minutes. Please send a shorter one.` | nothing |
| B6 | a 30 s voice of 12,000,000 bytes | `This voice message is larger than 10 MB. Please send a shorter one.` | nothing |
| B7 | a 30 s voice whose size Telegram does not report | as B4 | as B4 |
| B8 | B4's update delivered a second time | nothing | still one voice job and one queued transcription |
| B9 | B1's update delivered a second time | nothing | still one task |
| B10 | `/dashboard` | `Your task board: http://localhost:5173/#token=…` + `Keep this link private. A new /dashboard link signs out other devices.` | the token rotates; `auth.revoked` published |
| B11 | `/dashboard` in a group | `I work only in a private chat.` | nothing |
| B12 | `Buy milk` in a group | nothing | nothing |
| B13 | a sticker | `I understand text and voice messages only.` | nothing |
| B14 | `/help` | `Unknown command. Send a text or a voice message to create a task; /dashboard opens your tasks.` | nothing |
| B15 | an edit of B1 to `Buy oat milk` | nothing: the bot asks Telegram only for new messages, and the replay finds no handler for an edit | B1's task unchanged |
| B16 | `Buy bread` forwarded from another chat | `Task created: Buy bread` | one task for the person who forwarded it |
| B17 | a 20 s voice with the caption `/dashboard` | `Transcribing your voice message…` | as B4; the caption is not stored and the token does not rotate |
| B18 | `/start` | `Send me a text or a voice message and I will turn it into a task.` / `/dashboard – a private link to your task board.` / `Transcription: whisper_server. Task extraction: heuristic.` | nothing |
| B19 | `/start` with `TRANSCRIPTION_PROVIDER=openai`, no OpenAI key, `TASK_EXTRACTOR=claude`, no Anthropic key | as B18, last line `Transcription: openai (no API key, voice messages are unavailable). Task extraction: heuristic (no Anthropic API key).`; in this setup every text reply also carries the degraded line, and a voice is still acknowledged (S5 then answers that transcription is unavailable) | nothing |

Owner's acceptance, 2026-10-26: «Да», given to this table (rows B1–B19) after the round-2 changes were explained (bounded concurrency, the database check at start, "10 MB" as 10,000,000 bytes, the two `/dashboard` risks of section 8, and the live-run cleanup of 3.10).

## 8. Risks

- **Telegram's wording.** The notifier tells "not modified" from "not found" only by Telegram's description text. The unit tests pin the expected wording; the live run shows the real one for the cases the owner triggers.
- **Lost updates** (S4-D1, Z1): a hard kill, a handler cancelled after the drain, or an unexpected handler error (the user then sees `SOMETHING_WENT_WRONG` or nothing). Accepted and stated in the README.
- **Two `/dashboard` commands at the same moment** (a double tap, two devices): both rotate the token; the commits are ordered by the user row lock, but the two replies are not, so the newer message in the chat can hold the older, dead link. Sending `/dashboard` again fixes it. A failed `/dashboard` reply after the rotation leaves the user signed out with no link; the user sees no reply and sends `/dashboard` again. Both are accepted at this scale rather than adding a per-user lock and a retry.
- **A malformed `getUpdates` body** makes aiogram log a `ClientDecodeError` whose text holds the response, which may hold chat text. The token filter does not hide it. It needs a broken Telegram edge; accepted.
- **Connection budget** (umbrella L3): the bot's pool is 5 with no overflow and at most 10 handlers; each handler holds a session only for its short units of work, so a handler waits for a connection instead of opening more.
- **Group reply storm**: in a group where the bot sees every message (privacy mode off), each `/command` from any member gets `NOT_PRIVATE`. The README names the privacy-mode assumption.
- **An ambiguous `acquire`** (Redis applies `SET NX` but the reply is lost, and the client's retry finds the bot's own key) reports "held" for up to 30 s. Unconfirmed; a restart after the TTL clears it.
- **Merge with S6.** S6 may touch settings (`ApiSettings` gains the Redis mixin) and the README. S4.3's mixins and S6's settings are separate files; the README sections differ. The implementer merges the umbrella tip after S6 integrates (section 4).

## 9. Departures from the umbrella text

| # | What changes | Why | Who it binds |
|---|---|---|---|
| Z1 | Umbrella 3.6's crash window says Telegram redelivers an update after a crash. With concurrent handling (S4-D1) aiogram confirms an update before its handler ends, so an update is lost when the process is killed during its handler, when the drain times out, or when the handler fails unexpectedly; a normal stop drains handlers for up to 15 s. The text-crash test feeds the update twice instead of relying on Telegram. S8 sets the bot's `stop_grace_period` to at least 25 s. | S4-D1; aiogram 3.31.0's offset handling (exploration A 2.3, executed) | S8 |
| Z2 | When the acknowledgement fails to send, the voice job is still enqueued (umbrella 3.6 orders ack before enqueue, and the order holds on success). | stopping would leave the job to the sweep for 460 s; S5's notification already falls back to a new reply when `ack_message_id` is null | S5 |
| Z3 | A duplicate voice update resumes only a `queued` job without an acknowledgement; umbrella 3.6 says "resume when `ack_message_id` is NULL". | a job already `processing` or finished is S5's; a second "Transcribing…" would contradict it | — |
| Z4 | Every command outside a private chat gets one refusal line, and text and voice there are ignored; the umbrella makes only `/dashboard` private. | S4-D2 | S5 (no group notifications arise), S8 |
| Z5 | The polling lock is redis-py's `Lock` with an owner token and bounded calls; a held lock exits 1 at once; a lost lock, two failed renewals, or 60 s without a successful `getUpdates` stop the bot with status 1; the heartbeat is written at `acquire` and after each successful renewal while polling is healthy, and deleted at exit; the key names are constants. | umbrella 3.1 names the keys and timings only; aiogram retries a failing `getUpdates` forever | S8 (probe and restart policy) |
| Z6 | `BotSettings` reads `OPENAI_API_KEY` (presence only), which umbrella 3.7's table lists only for the transcribe worker. | umbrella 3.7's own sentence: `/start` names the `openai`-without-key case | — |
| Z7 | `TranscriptionSettingsMixin` (provider name from the domain enum, OpenAI key, `has_openai_key`) lives in `infrastructure/settings/transcription.py`; S5's transcribe-only model includes it. | one validation of the provider name | S5 |
| Z8 | "10 MB" is 10,000,000 bytes; exactly 120 s and exactly 10,000,000 bytes pass. | the umbrella gives no unit; the decimal value makes the accepted text "larger than 10 MB" exact | S5 (the same constant caps the download) |
| Z9 | All user-facing bot texts are English, plain text, and live in `application/replies.py`; S5 adds its result and failure texts there and reuses `TASK_CREATED` and `DEGRADED_LINE` for its edit. | S4-D3, S4-D5; one copy of each text | S5 |
| Z10 | The dashboard link is built by `dashboard_link` in `application/dashboard_token.py`, used by the seed and the bot. | one implementation of the link format | — |
| Z11 | `scripts/not-built.mjs` and its test are deleted. | `dev:bot` was its last user | — |
| Z12 | The text path replies before it publishes `task.created`; umbrella 3.6 publishes first. `/dashboard` keeps the umbrella's order. | the person waits on the reply; a publication can take up to 1 s; S3 section 8 leaves this order to S4; the dashboard refetches on reconnect | S6, S7 (an event can arrive up to the reply timeout, 10 s, after the commit) |
| Z13 | The bot checks the database at start and exits 1 when it is unavailable or not migrated; the umbrella names no start check. | otherwise a down database makes the bot consume and lose the Telegram backlog | S8 (the bot waits for `migrate`) |

## 10. Pre-handoff review record

Round 1 was reviewed by four agents on `claude-sonnet-5-5` (workflow `wf_fa18de42-332`: coherence, premise for aiogram and libraries, premise for this repository, all at high effort; adversarial at xhigh; 182 tool calls). Reports: `.tasks/artifacts/2026-10-26-s4/lens-{coherence,premise-aiogram,premise-repo,adversarial}.md`.

- **Coherence**: 2 HIGH, 9 MED, 17 LOW. Both HIGHs folded: rows B16 and B17 show the forward and the caption (S4-D4), and Z12 records the reply-before-publish order. MEDs folded: the crash window now says it is changed by Z1 and what C7 proves; C1 checks S4-D7; Z9 gives S5's texts one home (`application/replies.py`); the lock-loss mechanism, the renewal bound and the stop path are specified (3.7, 3.8) with criteria C17 and C21; the error hook, the bot-sender rule and the typing action have C22; C24 names its test file; B18 shows an ordinary `/start`. LOWs folded: 10 MB is decimal (Z8); the claude-without-key and openai-without-key behaviour (3.3, 3.4, B19); the recorded name `notifier.edit_message`; named constants; the heartbeat at `acquire`; the drain carrier `InFlightUpdates`; the step-3 "False" branch; the combined Z2/Z3 duplicate in C11; B15's wording; the latencies in the live run; the `dashboard_link` test in S4.1; the live-run cleanup; the drain timeout as a parameter.
- **Premise (aiogram)**: 0 HIGH, 7 MED, 10 LOW, most library claims executed and confirmed. MEDs folded: the drain uses `add_done_callback` so the error hook is drained; `Command(...) & F.text`; `validate_token` wrapped as `ValueError`; the bot owns its signals (`handle_signals=False`); the polling watch and `delete_webhook` for a deaf bot; `ClientDecodeError` mapped through the `AiogramError` base; Redis calls bounded at 4 s. LOWs folded: the typing timeout; `CappedBuffer` as a `BytesIO`; the description wording; the middleware as a `BaseMiddleware` subclass; `stop_polling` only while polling; the loss cases in Z1; `bot.me()` in the root; the shutdown budget; the 30 s download budget; the test traps (getMe, the drain through `start_polling`); a concurrency bound.
- **Premise (repository)**: 0 HIGH, 3 MED, 10 LOW; every named port, use case, repository statement, fake name, settings rule, import-linter contract and fixture confirmed. MEDs folded: a context variable instead of the store's lock for the L2 check; the Redis start message prints host and port only, so S3's private helper is not needed; Z12. LOWs folded: `SecretStr` in the env test; the replay's `sys.path` bootstrap and its test; README lines 188 and 266; `app.close` after `queue.close`; the hook never raises; `has_openai_key` and the start warning.
- **Adversarial**: 0 HIGH, 11 MED, 11 LOW. MEDs folded: `BOT_CONCURRENCY = 10`; steps 3 and 5 contain their own errors; the database check at start (Z13); `ClientDecodeError`; the token redaction filter (C23); the hook body; the polling watch; the keeper supervised (C21); the typing timeout. Two MEDs are carried as risks rather than code (section 8): concurrent `/dashboard` replies and a failed `/dashboard` reply after the rotation, both recovered by sending `/dashboard` again. LOWs folded: signals before the lock; bounded Redis calls; the concurrent duplicate in 3.4; the loss cases; row 8 restricted to user content types; the heartbeat at `acquire`; start-up network errors; the live-run cleanup. Carried: the group reply storm and the ambiguous `acquire` (section 8).
