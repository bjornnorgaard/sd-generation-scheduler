"""Background worker that drains the queue one job at a time."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from lib_generation_scheduler import codec, constants
from lib_generation_scheduler.store import Job, JobStore

logger = logging.getLogger("generation_scheduler")

PAUSED_META_KEY = "paused"


@dataclass
class Outcome:
    status: str  # done | failed | interrupted
    result: dict[str, Any] | None = None
    error: str | None = None


class Executor(Protocol):
    def run(self, job: Job, args: list[Any]) -> Outcome: ...

    def interrupt(self) -> None: ...

    def progress(self) -> dict[str, Any] | None: ...


class Runner:
    def __init__(
        self,
        store: JobStore,
        executor: Executor,
        *,
        pause_on_interrupt: Callable[[], bool] = lambda: True,
        on_finished: Callable[[Job], None] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._store = store
        self._executor = executor
        self._pause_on_interrupt = pause_on_interrupt
        self._on_finished = on_finished
        self._poll = poll_seconds
        self._cond = threading.Condition()
        self._stop = False
        self._busy = False
        self._thread: threading.Thread | None = None
        self._current: Job | None = None
        self._paused = store.get_meta(PAUSED_META_KEY, "0") == "1"

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(
                target=self._loop, name="generation-scheduler", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float | None = 5.0) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # -- pause / resume --------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        with self._cond:
            self._paused = paused
            self._store.set_meta(PAUSED_META_KEY, "1" if paused else "0")
            self._cond.notify_all()

    def pause(self) -> None:
        self.set_paused(True)

    def resume(self) -> None:
        self.set_paused(False)

    # -- introspection ---------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def current(self) -> Job | None:
        return self._current

    def progress(self) -> dict[str, Any] | None:
        if not self._busy:
            return None
        try:
            return self._executor.progress()
        except Exception:  # noqa: BLE001 - progress is best effort
            logger.exception("Generation Scheduler: progress lookup failed")
            return None

    def interrupt_current(self) -> bool:
        if not self._busy:
            return False
        self._executor.interrupt()
        return True

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until nothing is running and nothing is runnable (used by tests)."""
        with self._cond:
            return self._cond.wait_for(
                lambda: not self._busy and (self._paused or not self._store.has_pending()),
                timeout,
            )

    # -- worker ----------------------------------------------------------

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._stop and (self._paused or not self._store.has_pending()):
                    self._cond.wait(self._poll)
                if self._stop:
                    return
                job = self._store.claim_next()
                if job is None:
                    continue
                self._busy = True
                self._current = job
            self._execute(job)

    def _execute(self, job: Job) -> None:
        try:
            outcome = self._run(job)
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the worker
            logger.exception("Generation Scheduler: job %s crashed", job.id)
            outcome = Outcome(constants.FAILED, error=f"{type(exc).__name__}: {exc}")

        finished = self._store.finish(
            job.id, outcome.status, error=outcome.error, result=outcome.result
        )
        if outcome.status == constants.INTERRUPTED and self._pause_on_interrupt():
            self.pause()

        if self._on_finished is not None:
            try:
                self._on_finished(finished)
            except Exception:  # noqa: BLE001
                logger.exception("Generation Scheduler: on_finished hook failed")

        with self._cond:
            self._busy = False
            self._current = None
            self._cond.notify_all()

    def _run(self, job: Job) -> Outcome:
        args = codec.decode_args(self._store.get_args(job.id), job.inputs_dir or ".")
        return self._executor.run(job, args)
