"""Logic behind the Queue tab's HTTP API, as plain methods returning ``(status, body)``.

Kept free of FastAPI so it is testable anywhere; ``routes.py`` is the thin HTTP layer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from lib_generation_scheduler import constants, thumbnails
from lib_generation_scheduler.service import Scheduler
from lib_generation_scheduler.store import CLEAR_SCOPES, MOVES, InvalidJobState, JobNotFound

logger = logging.getLogger("generation_scheduler")

Result = tuple[int, dict[str, Any]]


def _error(status: int, message: str) -> Result:
    return status, {"error": message}


class QueueApi:
    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler

    def _guard(self, action: Callable[[], Result]) -> Result:
        try:
            return action()
        except JobNotFound:
            return _error(404, "No such job.")
        except InvalidJobState as exc:
            return _error(409, str(exc))
        except (ValueError, KeyError) as exc:
            return _error(400, str(exc))

    def state(self) -> Result:
        return 200, self.scheduler.state()

    def pause(self) -> Result:
        self.scheduler.pause()
        return 200, {"paused": True}

    def resume(self) -> Result:
        self.scheduler.resume()
        return 200, {"paused": False}

    def interrupt(self) -> Result:
        return 200, {"interrupted": self.scheduler.interrupt()}

    def clear(self, scope: Any) -> Result:
        if scope not in CLEAR_SCOPES:
            return _error(400, f"scope must be one of {', '.join(CLEAR_SCOPES)}")
        return 200, {"removed": self.scheduler.clear(scope)}

    def remove(self, job_id: int) -> Result:
        def action() -> Result:
            self.scheduler.remove(job_id)
            return 200, {"removed": 1}

        return self._guard(action)

    def move(self, job_id: int, where: Any) -> Result:
        if where not in MOVES:
            return _error(400, f"to must be one of {', '.join(MOVES)}")

        def action() -> Result:
            return 200, {"moved": self.scheduler.move(job_id, where)}

        return self._guard(action)

    def requeue(self, job_id: int) -> Result:
        def action() -> Result:
            job = self.scheduler.requeue(job_id)
            return 200, {"job": job.public()}

        return self._guard(action)

    def output_file(self, job_id: int, index: int, *, thumbnail: bool) -> tuple[int, Any]:
        """``(200, Path)`` for an image the job recorded, otherwise ``(404, error body)``."""
        path = self.scheduler.output_path(job_id, index)
        if path is None:
            return 404, {"error": "No such output."}
        if thumbnail:
            path = thumbnails.thumbnail_for(path, self.scheduler.thumbs_root, f"{job_id}-{index}")
        return 200, path
