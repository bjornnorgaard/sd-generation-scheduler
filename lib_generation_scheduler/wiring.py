"""Adds a Queue button beside each Generate button and connects it to the same inputs.

Forge builds the txt2img / img2img tabs as separate ``gr.Blocks`` and only wires the
Generate button once every input exists, at the very end of the tab. We therefore

1. create the Queue button the moment the Generate button is created (so it lands in the
   same row), remembering the tab's ``Blocks``;
2. from the ``on_ui_tabs`` callback — after the tabs are complete but before they are
   rendered into the main app — look up the inputs of Generate's click handler and register
   the Queue button's click on that same ``Blocks`` with those inputs.

Because the Queue click receives exactly what Generate's click receives, whatever the tab
or any extension puts in the argument list is captured without this extension knowing
about it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import gradio as gr
from gradio.context import Context

from lib_generation_scheduler import constants, enqueue, executor, summary

logger = logging.getLogger("generation_scheduler")

TOOLTIP = "Add these settings to the generation queue instead of generating now (Ctrl+Enter)"

# Runs in the browser before the click is sent. ``arguments`` holds only inputs (this event has
# no outputs), so nothing needs trimming. The first argument is the task-id slot the Generate
# button fills in; the executor assigns a real id, so we use it to carry a token that lets the
# page pair this job with the UI snapshot it takes now (see gsched_queue.js, State Manager).
# Async on purpose: the snapshot must be finished before the request is sent, otherwise it can
# read controls the user has already changed (State Manager's own Generate wrapper awaits too).
CAPTURE_JS_TEMPLATE = (
    "async function() {{ const args = Array.from(arguments); "
    "if (window.gschedNoteQueuePress) args[0] = await window.gschedNoteQueuePress({tab!r}); "
    "return args; }}"
)

# The Queue tab's script (gsched_queue.js) stores the id of the queue job it is showing in
# ``window.gschedRestoreId[tab]`` and clicks the hidden restore button; this hands that id to
# the Python handler as its only input.
RESTORE_JS_TEMPLATE = (
    "function() {{ return [(window.gschedRestoreId && window.gschedRestoreId[{tab!r}]) || \"\"]; }}"
)

# Runs after the restore event has put the finished job's images into the gallery.
DELIVERED_JS_TEMPLATE = (
    "function() {{ if (window.gschedDelivered) return window.gschedDelivered({tab!r}); }}"
)


@dataclass
class TabWiring:
    tab: str
    blocks: gr.Blocks
    generate_button: gr.Button
    queue_button: gr.Button
    restore_button: gr.Button


def find_generate_function(blocks: gr.Blocks, button: gr.Button, tab: str | None = None):
    """The backend function Generate's click runs (its ``inputs`` are what we reuse).

    Extensions may bind their own handlers to the same click (ControlNet does, ahead of
    Forge's), so "the first one" is wrong. Forge's handler is the one that fills the tab's
    gallery; failing that, the one taking the most inputs.
    """
    candidates = [
        function
        for function in blocks.fns.values()
        if function.fn is not None and (button._id, "click") in function.targets
    ]
    if not candidates:
        return None
    gallery_id = f"{tab}_gallery" if tab else None
    for function in candidates:
        if any(getattr(output, "elem_id", None) == gallery_id for output in function.outputs):
            return function
    return max(candidates, key=lambda function: len(function.inputs))


@contextmanager
def attached_to(blocks: gr.Blocks):
    """Temporarily make ``blocks`` the target for newly registered events."""
    previous = Context.root_block
    Context.root_block = blocks
    try:
        yield
    finally:
        Context.root_block = previous


def make_restore_handler(
    output_count: int, wait_for_result: Callable[[str], Any] = executor.wait_for_result
):
    """Fill the tab's gallery with a queue job's images once that job has finished.

    The recorded result is the very tuple Generate would have returned, so it maps 1:1 onto
    Generate's outputs. Anything else (never recorded, discarded, wrong shape) leaves the
    outputs alone.
    """

    def on_restore(id_task: str):
        result = wait_for_result(str(id_task or "")) if id_task else None
        if not isinstance(result, (tuple, list)) or len(result) != output_count:
            return tuple(gr.skip() for _ in range(output_count))
        return tuple(result)

    return on_restore


def make_click_handler(
    tab: str,
    roles: dict[str, int],
    get_scheduler: Callable[[], Any],
    get_context: Callable[[], dict[str, Any]],
):
    # ``request`` must be the first positional parameter for Gradio to inject it.
    def on_queue_click(request: gr.Request, *args: Any) -> None:
        scheduler = get_scheduler()
        user = getattr(request, "username", None)
        try:
            result = enqueue.enqueue_click(
                scheduler, tab, args, roles=roles, context=get_context(), user=user
            )
        except enqueue.EnqueueError as exc:
            raise gr.Error(str(exc)) from exc
        gr.Info(enqueue.queued_message(result, paused=scheduler.runner.paused))

    return on_queue_click


class Wiring:
    def __init__(
        self,
        get_scheduler: Callable[[], Any],
        get_context: Callable[[], dict[str, Any]],
        is_enabled: Callable[[], bool] = lambda: True,
    ) -> None:
        self._get_scheduler = get_scheduler
        self._get_context = get_context
        self._is_enabled = is_enabled
        self.tabs: dict[str, TabWiring] = {}

    def reset(self) -> None:
        """Forget captured tabs; the UI is rebuilt from scratch on every reload."""
        self.tabs.clear()

    def on_after_component(self, component: gr.components.Component, **kwargs: Any) -> None:
        """``script_callbacks.on_after_component`` hook: add Queue beside Generate."""
        if not isinstance(component, gr.Button) or not self._is_enabled():
            return
        tab = next(
            (t for t, elem_id in constants.GENERATE_BUTTON_IDS.items() if elem_id == kwargs.get("elem_id")),
            None,
        )
        if tab is None or Context.root_block is None:
            return
        queue_button = gr.Button(
            "Queue",
            elem_id=f"{tab}_queue",
            elem_classes=["gsched-queue-button"],
            variant="secondary",
        )
        # Forge reads this attribute for its hover tooltips (its ``tooltip=`` kwarg is a patch
        # that plain Gradio rejects).
        queue_button.webui_tooltip = TOOLTIP
        restore_button = gr.Button(visible=False, elem_id=f"{tab}_gsched_restore")
        self.tabs[tab] = TabWiring(tab, Context.root_block, component, queue_button, restore_button)

    def connect_all(self) -> None:
        """Register Queue clicks. Call after the tabs are built, before they are rendered."""
        for wiring in self.tabs.values():
            try:
                self._connect(wiring)
            except Exception:  # noqa: BLE001 - never take the whole UI down
                logger.exception("Generation Scheduler: could not wire the %s Queue button", wiring.tab)

    def _connect(self, wiring: TabWiring) -> None:
        generate = find_generate_function(wiring.blocks, wiring.generate_button, wiring.tab)
        if generate is None:
            logger.warning(
                "Generation Scheduler: no click handler found for %s; Queue button left inactive.",
                wiring.generate_button.elem_id,
            )
            return
        inputs = list(generate.inputs)
        roles = summary.resolve_roles(wiring.tab, [getattr(c, "elem_id", None) for c in inputs])
        handler = make_click_handler(wiring.tab, roles, self._get_scheduler, self._get_context)
        with attached_to(wiring.blocks):
            wiring.queue_button.click(
                fn=handler,
                inputs=inputs,
                outputs=None,
                js=CAPTURE_JS_TEMPLATE.format(tab=wiring.tab),
                # Must go through Gradio's queue: gr.Info / gr.Error toasts need an event id.
                # No concurrency limit so it never waits behind a running generation.
                concurrency_limit=None,
                show_progress="hidden",
            )
            outputs = list(generate.outputs)
            if inputs and outputs:
                wiring.restore_button.click(
                    fn=make_restore_handler(len(outputs)),
                    inputs=[inputs[0]],
                    outputs=outputs,
                    js=RESTORE_JS_TEMPLATE.format(tab=wiring.tab),
                    concurrency_limit=None,
                    show_progress="hidden",
                ).then(
                    fn=None,
                    js=DELIVERED_JS_TEMPLATE.format(tab=wiring.tab),
                    show_progress="hidden",
                )
