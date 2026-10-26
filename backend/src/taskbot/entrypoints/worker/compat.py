"""Worker-only compatibility for Celery 5.6 / Billiard stdin ownership."""

import sys

from billiard import current_process
from billiard.util import register_after_fork
from celery import Celery


def _preserve_beat_stdin(app: Celery) -> None:
    if current_process().name == "Beat":
        # Billiard replaces stdin before this callback. Beat's descriptor closer
        # protects __stdin__, so it must refer to that live owning wrapper.
        # Typeshed marks __stdin__ Final. This boundary deliberately repairs the
        # owner identity after Billiard changes it; runtime permits that repair.
        sys.__stdin__ = sys.stdin  # type: ignore[misc, assignment]


def register_beat_stdin(app: Celery) -> None:
    # Celery serializes fixups when reconstructing its app in a spawned child.
    # The public callback runs before Beat.run, unlike the later beat_init signal.
    register_after_fork(app, _preserve_beat_stdin)
