import asyncio
import shutil
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from tests.processes import run_process

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_empty_revision_template_is_linted_and_formatted(tmp_path: Path) -> None:
    scripts = tmp_path / "alembic"
    (scripts / "versions").mkdir(parents=True)
    shutil.copyfile(BACKEND_ROOT / "alembic/script.py.mako", scripts / "script.py.mako")
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(scripts))
    command.revision(config, message="empty revision", rev_id="probe")
    revisions = list((scripts / "versions").glob("*.py"))
    assert len(revisions) == 1
    for args in (("ruff", "check"), ("ruff", "format", "--check"), ("mypy", "--strict")):
        result = asyncio.run(
            run_process(
                sys.executable,
                "-m",
                *args,
                str(revisions[0]),
                cwd=BACKEND_ROOT,
                timeout_seconds=15,
            )
        )
        assert result.returncode == 0, result.stdout + result.stderr
