"""FastAPI layer for :mod:`lib_generation_scheduler.api`.

Deliberately *without* ``from __future__ import annotations``: FastAPI resolves the route
parameter annotations (``Request``) from this module's globals.

Every mutating route requires the ``X-Gsched`` header. A browser will not send a custom
header cross-origin without a CORS preflight (which the WebUI does not grant), so a web
page you happen to have open cannot clear your queue by posting to localhost.
"""

from typing import Any, Callable

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from lib_generation_scheduler import constants
from lib_generation_scheduler.api import QueueApi
from lib_generation_scheduler.service import Scheduler

CSRF_HEADER = "X-Gsched"


def _reply(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - empty / malformed body is treated as {}
        return {}
    return body if isinstance(body, dict) else {}


def register_routes(app, get_scheduler: Callable[[], Scheduler]) -> None:
    """Attach the queue routes to the WebUI's FastAPI app."""
    prefix = constants.API_PREFIX

    def api() -> QueueApi:
        return QueueApi(get_scheduler())

    def refused(request: Request):
        if request.headers.get(CSRF_HEADER) != "1":
            return _reply(403, {"error": f"Missing {CSRF_HEADER} header."})
        return None

    @app.get(f"{prefix}/state")
    async def state():
        return _reply(*api().state())

    @app.post(f"{prefix}/queue/pause")
    async def pause(request: Request):
        return refused(request) or _reply(*api().pause())

    @app.post(f"{prefix}/queue/resume")
    async def resume(request: Request):
        return refused(request) or _reply(*api().resume())

    @app.post(f"{prefix}/queue/clear")
    async def clear(request: Request):
        return refused(request) or _reply(*api().clear((await _json_body(request)).get("scope")))

    @app.post(f"{prefix}/interrupt")
    async def interrupt(request: Request):
        return refused(request) or _reply(*api().interrupt())

    @app.delete(prefix + "/jobs/{job_id}")
    async def remove(job_id: int, request: Request):
        return refused(request) or _reply(*api().remove(job_id))

    @app.post(prefix + "/jobs/{job_id}/move")
    async def move(job_id: int, request: Request):
        return refused(request) or _reply(*api().move(job_id, (await _json_body(request)).get("to")))

    @app.post(prefix + "/jobs/{job_id}/requeue")
    async def requeue(job_id: int, request: Request):
        return refused(request) or _reply(*api().requeue(job_id))

    def output_route(thumbnail: bool):
        async def route(job_id: int, index: int):
            status, payload = api().output_file(job_id, index, thumbnail=thumbnail)
            if status != 200:
                return _reply(status, payload)
            return FileResponse(payload, headers={"Cache-Control": "private, max-age=3600"})

        return route

    app.get(prefix + "/jobs/{job_id}/outputs/{index}/thumbnail")(output_route(True))
    app.get(prefix + "/jobs/{job_id}/outputs/{index}")(output_route(False))
