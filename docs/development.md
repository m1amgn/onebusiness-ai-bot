# Development

The reviewer setup is in [README.md](../README.md). Development commands and local
infrastructure are documented there until the delivery documentation is complete.

## Load-sensitive test quarantine

This list explains failures under machine load; it does not skip tests. Every
listed test still runs in the final `yarn verify`. The owner makes each listed
test robust. After an unrelated load-sensitive failure, rerun that test alone at
the same commit under low load and on the base commit. Record the command, both
commits and both results in the verification receipt. A repeated failure on head,
or a failure only on head, is a defect to investigate.

| Test | Observed failure | Owner |
|---|---|---|
| `backend/tests/integration/test_bot_lock.py` | Sub-second lock leases can expire while the machine is busy. | Repository owner |

The whisper launcher fixtures wait for marker files for up to 30 seconds.
Worker runtime effect waits use the 120-second cold-start budget. The ETA restart
test observes the worker's scheduled list before stopping it. The dev proxy test
allows 10 seconds for the interrupted stream to close, and still fails when the
proxy keeps it open.
