"""Public interfaces for models, environments and episode results."""

from dataclasses import asdict, dataclass, field
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Completion:
    text: str
    usage: dict[str, int] = field(default_factory=dict)


class Model(Protocol):
    def generate(self, prompt: str, *, stop: Sequence[str]) -> Completion | str: ...


@dataclass(frozen=True)
class Observation:
    text: str
    done: bool = False
    answer: str | None = None
    reward: float | None = None


class Environment(Protocol):
    @property
    def instructions(self) -> str: ...
    def reset(self) -> None: ...
    def step(self, name: str, argument: str) -> Observation: ...


@dataclass(frozen=True)
class Event:
    kind: str
    step: int
    text: str
    tool: str | None = None


@dataclass
class Result:
    status: str
    answer: str | None
    steps: int
    model_calls: int
    usage: dict[str, int]
    events: list[Event]
    elapsed_seconds: float
    error: str | None = None
    reward: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)
