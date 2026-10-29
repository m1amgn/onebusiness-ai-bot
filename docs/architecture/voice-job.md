# Voice job — States, retries and recovery

> **STATUS 2026-10-29: ACTIVE.** Generated from `voice-job.lifecycle.json` by repo-map; not source-backed.
> Run `repo-map check` before trusting this map; re-pin when it still holds, re-author when a boundary moved.

Type: `lifecycle` · quality: `showcase` · nodes: 4 · connections: 6

## Components

| id | type | label | detail | tag |
| --- | --- | --- | --- | --- |
| queued | start | queued |  |  |
| processing | active | processing |  |  |
| done | success | done |  |  |
| failed | failure | failed |  |  |

## Connections

- queued → processing — claim +1
- processing → done — commit
- processing → processing — reclaim
- processing → queued — retry (dashed)
- processing → failed — fail/stuck
- queued → failed — stuck (dashed)

## Guided views

- **Main path** (queued, processing, done)
- **Retries and lease** (queued, processing)
- **Failure and notification** (processing, done, failed)

## Cards

### Claim and retry

- 450 s lease; five attempts. Transient retries: 2/4/8/16 s. Fifth transient failure: transient_exhausted.

### Notification keeps status

- Set notified_at + notification_outcome. Five attempts. edited/replied: delivered. blocked/given_up: undelivered.

### Recovery sweep — 60 s

- Re-enqueue stale/unnotified rows. After five attempts: queued or lease-expired processing becomes failed(stuck). Permanent errors and soft timeout also fail.

