"""Exercise the real Beat descriptor closer without starting a scheduler or worker."""

import base64
import os
import pickle
import sys
from pathlib import Path

import pytest

from taskbot.entrypoints.worker import app
from taskbot.infrastructure.celery.app import create_celery_app
from tests.processes import run_process

BACKEND = Path(__file__).resolve().parents[2]
PROBE = """
import base64
import os
import pickle
import sys
from billiard import current_process
from billiard.util import _run_after_forkers
from celery import beat
# This fresh interpreter imports worker compatibility only through the serialized
# fixup path. No parent's in-memory after-fork registration can mask its loss.
restored = pickle.loads(base64.b64decode(os.environ['S3_BEAT_APP']))
# Mirror Billiard._bootstrap: it closes/replaces stdin before after-fork callbacks.
sys.stdin.close()
sys.stdin = open(os.devnull)
current_process().name = os.environ['S3_BEAT_PROCESS']
original = sys.__stdin__
_run_after_forkers()
if current_process().name != 'Beat':
    assert sys.__stdin__ is original
    os.fstat(sys.stdin.fileno())
    sys.stdin.close()
    print('pool stdin unchanged')
    raise SystemExit(0)

class App:
    def set_default(self):
        pass
    def set_current(self):
        pass

class Service:
    def start(self, *, embedded_process):
        assert embedded_process
        assert not sys.stdin.closed
        os.fstat(sys.stdin.fileno())
        sys.stdin.close()
        assert sys.stdin.closed
        print('beat stdin ownership intact')

process = object.__new__(beat._Process)
process.app = App()
process.service = Service()
process.run()
"""


@pytest.mark.parametrize("process_name", ["Beat", "SpawnPoolWorker-1"])
async def test_beat_preserves_stdin_without_changing_pool_children(process_name: str) -> None:
    source = create_celery_app(broker_url="redis://127.0.0.1:1/1", namespace="test:")
    source.fixups = app.fixups.copy()
    try:
        payload = base64.b64encode(pickle.dumps(source)).decode()
    finally:
        source.close()
    result = await run_process(
        sys.executable,
        "-c",
        PROBE,
        cwd=BACKEND,
        env={
            **os.environ,
            "TASKBOT_ENV_FILE": "/dev/null",
            "CELERY_BROKER_URL": "redis://127.0.0.1:1/1",
            "S3_BEAT_APP": payload,
            "S3_BEAT_PROCESS": process_name,
        },
        timeout_seconds=30,
    )
    assert result.returncode == 0, result.stderr
    expected = "beat stdin ownership intact" if process_name == "Beat" else "pool stdin unchanged"
    assert result.stdout.strip() == expected
    assert "Bad file descriptor" not in result.stderr
