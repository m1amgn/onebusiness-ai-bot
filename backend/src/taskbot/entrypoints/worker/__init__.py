from taskbot.domain.jobs import SweepJob
from taskbot.entrypoints.settings import load_settings
from taskbot.entrypoints.worker.compat import register_beat_stdin
from taskbot.infrastructure.celery.app import create_celery_app
from taskbot.infrastructure.settings.worker import BrokerSettings

broker_settings = load_settings(BrokerSettings)
app = create_celery_app(
    broker_url=broker_settings.broker_url, namespace=broker_settings.redis_namespace
)

# Retain the compatibility callback in forked and reconstructed spawned apps.
app.fixups.add("taskbot.entrypoints.worker.compat:register_beat_stdin")
register_beat_stdin(app)
app.conf.beat_schedule = {
    "recover-stuck-voice-jobs": {
        "task": SweepJob.name,
        "schedule": 60,
        "options": {"expires": 60},
    }
}

# Signal registration follows app construction; neither step opens a connection.
from taskbot.entrypoints.worker import signals as signals  # noqa: E402

# Tasks register after the app and its signals, including in spawned children.
# isort: split
from taskbot.entrypoints.worker import notification as notification  # noqa: E402
from taskbot.entrypoints.worker import transcription as transcription  # noqa: E402
