"""Queue facade: persistence + worker + on-disk inputs, independent of Gradio/FastAPI."""

from __future__ import annotations

import logging
import shutil
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lib_generation_scheduler import codec, constants, summary
from lib_generation_scheduler.runner import Executor, Runner
from lib_generation_scheduler.store import Job, JobNotFound, JobStore

logger = logging.getLogger("generation_scheduler")

DEFAULT_HISTORY_LIMIT = 100
INPUTS_DIRNAME = "inputs"
THUMBS_DIRNAME = "thumbs"


@dataclass(frozen=True)
class Enqueued:
    job: Job
    ahead: int  # jobs that will run before this one


class Scheduler:
    def __init__(
        self,
        data_dir: str | Path,
        executor: Executor,
        *,
        history_limit: Callable[[], int] = lambda: DEFAULT_HISTORY_LIMIT,
        pause_on_interrupt: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = 1.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._history_limit = history_limit
        self._clock = clock
        self._started = False
        self.store = JobStore(self.data_dir / constants.DB_FILENAME, clock=clock)
        self.runner = Runner(
            self.store,
            executor,
            pause_on_interrupt=pause_on_interrupt,
            on_finished=self._after_job,
            poll_seconds=poll_seconds,
        )

    # -- paths -----------------------------------------------------------

    @property
    def inputs_root(self) -> Path:
        return self.data_dir / INPUTS_DIRNAME

    @property
    def thumbs_root(self) -> Path:
        return self.data_dir / THUMBS_DIRNAME

    def _new_inputs_dir(self) -> Path:
        return self.inputs_root / uuid.uuid4().hex

    def _cleanup(self, job: Job) -> None:
        if job.inputs_dir:
            shutil.rmtree(job.inputs_dir, ignore_errors=True)
        for thumb in self.thumbs_root.glob(f"{job.id}-*"):
            thumb.unlink(missing_ok=True)

    def _after_job(self, _finished: Job) -> None:
        for stale in self.store.prune_finished(self._history_limit()):
            self._cleanup(stale)

    # -- lifecycle -------------------------------------------------------

    def start(self, *, autostart: bool = False) -> None:
        """Recover from a crash, decide the initial pause state, and start the worker.

        The WebUI can rebuild its UI without restarting the process; only the first call does
        anything, so a job that is running right now is never mistaken for a crashed one.
        """
        if self._started:
            return
        self._started = True
        recovered = self.store.recover_interrupted()
        if recovered:
            logger.warning(
                "Generation Scheduler: %d job(s) were running when the WebUI stopped; "
                "marked as interrupted.",
                recovered,
            )
        if self.store.has_pending():
            self.runner.set_paused(not autostart)
        self.runner.start()

    def stop(self) -> None:
        self.runner.stop()

    # -- commands --------------------------------------------------------

    def enqueue(
        self,
        kind: str,
        args: Sequence[Any],
        *,
        roles: dict[str, int] | None = None,
        context: dict[str, Any] | None = None,
        user: str | None = None,
    ) -> Enqueued:
        """Persist a Generate click for later. ``args`` is exactly what Generate would get."""
        if kind not in constants.KINDS:
            raise ValueError(f"Unknown job kind: {kind!r}")
        directory = self._new_inputs_dir()
        try:
            args_json = codec.encode_args(list(args), directory)
            job = self.store.add_job(
                kind,
                args_json,
                summary=summary.build_summary(kind, args, roles or {}),
                context=context,
                user=user,
                inputs_dir=directory if directory.exists() else None,
            )
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.runner.wake()
        return Enqueued(job, self.store.pending_ahead(job.id))

    def remove(self, job_id: int) -> Job:
        job = self.store.remove(job_id)
        self._cleanup(job)
        return job

    def clear(self, scope: str) -> int:
        removed = self.store.clear(scope)
        for job in removed:
            self._cleanup(job)
        return len(removed)

    def move(self, job_id: int, where: str) -> bool:
        return self.store.move(job_id, where)

    def requeue(self, job_id: int) -> Job:
        """Append a new pending copy of a job (usually a finished one)."""
        source = self.store.get(job_id)
        if source is None:
            raise JobNotFound(job_id)
        directory = self._new_inputs_dir()
        try:
            copied = codec.copy_inputs(source.inputs_dir, directory)
            job = self.store.duplicate(job_id, inputs_dir=copied)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        self.runner.wake()
        return job

    def pause(self) -> None:
        self.runner.pause()

    def resume(self) -> None:
        self.runner.resume()

    def interrupt(self) -> bool:
        return self.runner.interrupt_current()

    # -- queries ---------------------------------------------------------

    def state(self) -> dict[str, Any]:
        running = self.store.running()
        return {
            "paused": self.runner.paused,
            "busy": self.runner.busy,
            "running": running.public() if running else None,
            "progress": self.runner.progress() if running else None,
            "pending": [job.public() for job in self.store.pending()],
            "finished": [job.public() for job in self.store.finished(self._history_limit())],
            "counts": self.store.counts(),
            "now": self._clock(),
        }

    def get_job(self, job_id: int) -> Job | None:
        return self.store.get(job_id)

    def output_path(self, job_id: int, index: int) -> Path | None:
        """A recorded output image of a job, or ``None`` (never a caller-chosen path)."""
        job = self.store.get(job_id)
        outputs = (job.result or {}).get("outputs") if job else None
        if not isinstance(outputs, list) or not 0 <= index < len(outputs):
            return None
        path = Path(str(outputs[index]))
        return path if path.is_file() else None
