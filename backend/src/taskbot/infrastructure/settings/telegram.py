from pydantic import SecretStr


def validate_bot_token(value: SecretStr) -> SecretStr:
    from aiogram.utils.token import TokenValidationError, validate_token

    try:
        validate_token(value.get_secret_value())
    except TokenValidationError:
        raise ValueError("malformed token") from None
    return value
