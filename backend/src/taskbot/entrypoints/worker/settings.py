import os
import sys

from taskbot.entrypoints.settings import load_settings
from taskbot.infrastructure.settings.worker import TranscribeWorkerSettings, WorkerSettings


def load_worker_settings() -> WorkerSettings:
    # Only the process chooses a role; a dotenv file must not start a worker implicitly.
    role = os.environ.get("WORKER_ROLE")
    if role not in ("transcribe", "notify"):
        print("Invalid configuration: WORKER_ROLE: choose transcribe or notify", file=sys.stderr)
        raise SystemExit(2)
    model = TranscribeWorkerSettings if role == "transcribe" else WorkerSettings
    return load_settings(model)
