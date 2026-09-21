"""What happens when someone presses a Queue button (no Gradio types here)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lib_generation_scheduler import codec
from lib_generation_scheduler.service import Enqueued, Scheduler


class EnqueueError(Exception):
    """Raised with a message that is safe to show to the user."""


def enqueue_click(
    scheduler: Scheduler,
    tab: str,
    args: Sequence[Any],
    *,
    roles: dict[str, int],
    context: dict[str, Any],
    user: str | None,
) -> Enqueued:
    try:
        return scheduler.enqueue(tab, args, roles=roles, context=context, user=user)
    except codec.UnserializableArgument as exc:
        raise EnqueueError(
            f"Can't queue this job: {exc} (an extension passes a value the queue can't save)."
        ) from exc


def queued_message(result: Enqueued, *, paused: bool) -> str:
    text = f"Queued #{result.job.id}"
    if result.ahead == 0 and not paused:
        text += " — starting next"
    elif result.ahead:
        text += f" — {result.ahead} ahead"
    if paused:
        text += " (queue is paused)"
    return text
