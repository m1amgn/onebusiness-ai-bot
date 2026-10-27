"""Process-layout receipt imported only by the mounted Docker acceptance probe."""

import json
import logging
import os
from pathlib import Path

from celery import signals


def record_process_layout(**kwargs: object) -> None:
    # Celery's process titles can relocate environ; read the variable in the child.
    receipt = {
        "pid": os.getpid(),
        "parent": os.getppid(),
        "recorded_main": int(os.environ["TASKBOT_WORKER_MAIN_PID"]),
        "init": Path("/proc/1/comm").read_text().strip(),
        "uid": os.getuid(),
    }
    logging.getLogger(__name__).info("STACK_PROCESS_LAYOUT %s", json.dumps(receipt))


signals.worker_process_init.connect(record_process_layout, weak=False)
