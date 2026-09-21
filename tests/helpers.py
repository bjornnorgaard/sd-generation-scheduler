"""Shared test helpers (stdlib only)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

EXTENSION_ROOT = Path(__file__).resolve().parents[1]
if str(EXTENSION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXTENSION_ROOT))

from lib_generation_scheduler import constants  # noqa: E402
from lib_generation_scheduler.runner import Outcome  # noqa: E402


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


class FakeExecutor:
    """Records runs; behaviour per call is scripted through ``outcomes``."""

    def __init__(self) -> None:
        self.runs: list[tuple[int, list[Any]]] = []
        self.outcomes: list[Any] = []
        self.interrupted = 0
        self.gate: threading.Event | None = None
        self.started = threading.Event()

    def run(self, job, args):
        self.runs.append((job.id, args))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(5), "test gate never released"
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return Outcome(constants.DONE, result={"outputs": []})

    def interrupt(self) -> None:
        self.interrupted += 1
        if self.gate is not None:
            self.gate.set()

    def progress(self):
        return {"step": 3, "steps": 20, "job_no": 0, "job_count": 1}
