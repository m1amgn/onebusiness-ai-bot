"""Export a deterministic OpenAPI document from the application factory."""

import json
import sys
from pathlib import Path

from taskbot.entrypoints.api.app import create_app


def export_schema(output: Path) -> None:
    output.write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m taskbot.entrypoints.api.export_schema <output-path>")
    export_schema(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
