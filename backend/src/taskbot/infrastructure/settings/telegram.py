from pydantic import SecretStr


def validate_bot_token(value: SecretStr) -> SecretStr:
    # Match aiogram's shape check without importing its full model graph in both
    # launcher interpreters. The configured child imports and constructs Bot.
    token = value.get_secret_value()
    left, separator, right = token.partition(":")
    if any(character.isspace() for character in token) or not (
        separator and left.isdigit() and right
    ):
        raise ValueError("malformed token") from None
    return value
