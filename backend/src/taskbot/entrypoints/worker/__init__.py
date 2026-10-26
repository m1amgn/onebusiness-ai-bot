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

# Signal registration follows app construction; neither step opens a connection.
from taskbot.entrypoints.worker import signals as signals  # noqa: E402
