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

from lib_generation_scheduler import constants, enqueue, summary

logger = logging.getLogger("generation_scheduler")

# Runs in the browser before the click is sent. The first argument is a placeholder for the
# task id the Generate button fills in; the executor assigns a real one. ``arguments`` holds
# only inputs (this event has no outputs), so nothing needs trimming.
TOOLTIP = "Add these settings to the generation queue instead of generating now"

CAPTURE_JS = "function() { return Array.from(arguments); }"


@dataclass
class TabWiring:
    tab: str
    blocks: gr.Blocks
    generate_button: gr.Button
    queue_button: gr.Button


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
        self.tabs[tab] = TabWiring(tab, Context.root_block, component, queue_button)

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
                js=CAPTURE_JS,
                # Must go through Gradio's queue: gr.Info / gr.Error toasts need an event id.
                # No concurrency limit so it never waits behind a running generation.
                concurrency_limit=None,
                show_progress="hidden",
            )
