from dataclasses import dataclass, field


@dataclass
class Calls:
    items: list[str] = field(default_factory=list)
