import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from governed_agent.runtime import AgentRuntime


class FakeClock:
    """Deterministic, manually advanced clock for timeout tests."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def runtime(clock) -> AgentRuntime:
    return AgentRuntime(approval_ttl_seconds=600.0, clock=clock)
