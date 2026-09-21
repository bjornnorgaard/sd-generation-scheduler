"""Runs queued jobs through Forge Neo's own txt2img / img2img entry points.

Everything that touches WebUI internals lives here and imports them lazily, so the rest
of the package (and its tests) work without a WebUI process. Tests exercise this module
with fake ``modules.*`` packages.

Two things are deliberately replayed exactly like the Generate button does them:

* the call goes through ``call_queue.wrap_gradio_gpu_call`` so it shares the WebUI's
  queue lock, progress tracking, and cleanup with manual generations;
* model selection (checkpoint, VAE / text encoders, dtype) is *not* part of the Generate
  arguments — it lives in global settings — so it is captured when a job is queued and
  re-applied around the run, then restored, using the same ``main_entry`` helpers the
  quick-settings dropdowns use.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from types import SimpleNamespace
from typing import Any

from lib_generation_scheduler import constants
from lib_generation_scheduler.runner import Outcome
from lib_generation_scheduler.store import Job

logger = logging.getLogger("generation_scheduler")

CHECKPOINT_KEY = "sd_model_checkpoint"
MODULES_KEY = "forge_additional_modules"
DTYPE_KEY = "forge_unet_storage_dtype"


class QueuedRequest(SimpleNamespace):
    """Minimal stand-in for ``gr.Request``; only ``username`` is read by the entry points."""

    def __init__(self, username: str | None = None) -> None:
        super().__init__(
            username=username, headers={}, cookies={}, query_params={}, client=None, session_hash=None
        )


def parse_option_names(text: str) -> list[str]:
    return [name.strip() for name in str(text or "").replace("\n", ",").split(",") if name.strip()]


def capture_context(extra_options: Iterable[str] = ()) -> dict[str, Any]:
    """Snapshot the global state a Generate click implicitly depends on."""
    from modules import shared  # noqa: PLC0415

    opts = shared.opts
    context: dict[str, Any] = {}
    checkpoint = getattr(opts, CHECKPOINT_KEY, None)
    if checkpoint:
        context[CHECKPOINT_KEY] = checkpoint
        context["model"] = os.path.basename(str(checkpoint)).rsplit(" [", 1)[0]
    if hasattr(opts, MODULES_KEY):
        context[MODULES_KEY] = list(getattr(opts, MODULES_KEY) or [])
    if hasattr(opts, DTYPE_KEY):
        context[DTYPE_KEY] = getattr(opts, DTYPE_KEY)

    data = getattr(opts, "data", {})
    extras = {name: data[name] for name in extra_options if name in data}
    if extras:
        context["opts"] = extras
    return context


def apply_context(context: dict[str, Any]) -> Callable[[], None]:
    """Switch the WebUI to ``context``; returns a callable that switches everything back."""
    if not context:
        return lambda: None

    from modules import shared  # noqa: PLC0415

    opts = shared.opts
    undo: list[Callable[[], None]] = []

    def guarded(step: str, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:  # noqa: BLE001 - a stale option must not fail the whole job
            logger.exception("Generation Scheduler: could not apply %s", step)

    def swap(step: str, current: Any, wanted: Any, change: Callable[[Any], Any]) -> None:
        """Change ``current`` -> ``wanted`` now, and register the way back."""
        if current == wanted:
            return
        guarded(step, lambda: change(wanted))
        undo.append(lambda: guarded(f"{step} (restore)", lambda: change(current)))

    try:
        from modules_forge import main_entry  # noqa: PLC0415
    except ImportError:
        main_entry = None

    if main_entry is not None:
        if CHECKPOINT_KEY in context:
            swap(
                "checkpoint",
                getattr(opts, CHECKPOINT_KEY, None),
                context[CHECKPOINT_KEY],
                lambda value: main_entry.checkpoint_change(value, None, save=False, refresh=True),
            )
        if MODULES_KEY in context:
            swap(
                "VAE / text encoders",
                list(getattr(opts, MODULES_KEY, []) or []),
                list(context[MODULES_KEY] or []),
                lambda value: main_entry.modules_change(value, None, save=False, refresh=True),
            )
        if DTYPE_KEY in context:
            swap(
                "UNet dtype",
                getattr(opts, DTYPE_KEY, None),
                context[DTYPE_KEY],
                lambda value: main_entry.dtype_change(value, None, save=False, refresh=True),
            )

    for name, wanted in (context.get("opts") or {}).items():
        swap(f"option {name}", opts.data.get(name), wanted, lambda value, n=name: opts.set(n, value))

    def restore() -> None:
        for action in reversed(undo):
            action()

    return restore


def extract_result(res: Any, elapsed: float) -> dict[str, Any]:
    """Pull saved file paths and infotext out of a txt2img / img2img return tuple."""
    result: dict[str, Any] = {"outputs": [], "elapsed": round(elapsed, 2)}
    try:
        gallery = res[0]
        images = gallery.get("value") if isinstance(gallery, dict) else gallery
        for image in images or []:
            if isinstance(image, (tuple, list)):
                image = image[0]
            saved = getattr(image, "already_saved_as", None)
            if saved:
                saved = str(saved).rsplit("?", 1)[0]
                if os.path.isfile(saved):
                    result["outputs"].append(saved)
    except Exception:  # noqa: BLE001 - result details are a nicety
        logger.debug("Generation Scheduler: could not read gallery from result", exc_info=True)

    try:
        info = json.loads(res[2])
        infotexts = info.get("infotexts") or []
        if infotexts:
            result["infotext"] = infotexts[0]
        if "seed" in info:
            result["seed"] = info["seed"]
    except Exception:  # noqa: BLE001
        logger.debug("Generation Scheduler: could not read generation info", exc_info=True)
    return result


class ForgeExecutor:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    def _entry_point(self, kind: str) -> Callable[..., Any]:
        if kind == "txt2img":
            from modules import txt2img  # noqa: PLC0415

            return txt2img.txt2img
        if kind == "img2img":
            from modules import img2img  # noqa: PLC0415

            return img2img.img2img
        raise ValueError(f"Unknown job kind: {kind!r}")

    def run(self, job: Job, args: list[Any]) -> Outcome:
        from modules import call_queue, progress, shared  # noqa: PLC0415

        try:
            from modules_forge import main_thread  # noqa: PLC0415
        except ImportError:
            main_thread = None

        entry = self._entry_point(job.kind)
        id_task = progress.create_task_id(job.kind)
        request = QueuedRequest(job.user)
        captured: dict[str, Any] = {}

        def inner(id_task: str, request: QueuedRequest, *rest: Any) -> Any:
            restore = apply_context(job.context)
            try:
                try:
                    res = entry(id_task, request, *rest)
                except BaseException as exc:
                    captured["error"] = f"{type(exc).__name__}: {exc}"
                    raise
                # Read before the wrapper's cleanup resets the flag.
                captured["interrupted"] = bool(shared.state.interrupted)
                if res is None:
                    last = getattr(main_thread, "last_exception", None)
                    captured["error"] = last or "Generation returned no result."
                captured["res"] = res
                return res
            finally:
                restore()

        wrapped = call_queue.wrap_gradio_gpu_call(inner, extra_outputs=[None, None, "", ""])
        started = self._clock()
        wrapped(id_task, request, *args[1:])
        elapsed = self._clock() - started

        if captured.get("error"):
            return Outcome(constants.FAILED, error=captured["error"])
        if "res" not in captured:
            return Outcome(constants.FAILED, error="Generation did not run.")
        result = extract_result(captured["res"], elapsed)
        status = constants.INTERRUPTED if captured.get("interrupted") else constants.DONE
        return Outcome(status, result=result)

    def interrupt(self) -> None:
        from modules import shared  # noqa: PLC0415

        shared.state.interrupt()

    def progress(self) -> dict[str, Any] | None:
        from modules import shared  # noqa: PLC0415

        state = shared.state
        return {
            "step": int(getattr(state, "sampling_step", 0) or 0),
            "steps": int(getattr(state, "sampling_steps", 0) or 0),
            "job_no": int(getattr(state, "job_no", 0) or 0),
            "job_count": int(getattr(state, "job_count", 0) or 0),
        }
