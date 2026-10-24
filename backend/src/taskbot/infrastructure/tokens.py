import hashlib
import secrets


class SecretsTokenGenerator:
    def generate(self) -> str:
        return secrets.token_urlsafe(32)


class Sha256TokenHasher:
    def hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()
