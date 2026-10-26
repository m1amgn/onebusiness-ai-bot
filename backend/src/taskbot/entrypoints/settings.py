import sys

from pydantic import ValidationError
from pydantic_settings import BaseSettings


def load_settings[T: BaseSettings](model: type[T]) -> T:
    try:
        return model()
    except ValidationError as error:
        for detail in error.errors(include_input=False, include_context=False, include_url=False):
            variable = ".".join(str(part).upper() for part in detail["loc"])
            print(f"Invalid configuration: {variable}: {detail['msg']}", file=sys.stderr)
        raise SystemExit(2) from None
