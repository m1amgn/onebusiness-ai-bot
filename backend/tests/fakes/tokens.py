from dataclasses import dataclass


@dataclass
class FakeTokenGenerator:
    count: int = 0

    def generate(self) -> str:
        self.count += 1
        return f"token-{self.count}"


class FakeTokenHasher:
    def hash(self, token: str) -> str:
        return f"hash:{token}"
