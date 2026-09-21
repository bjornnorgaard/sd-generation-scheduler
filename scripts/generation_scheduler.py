from __future__ import annotations

import logging
import threading

from modules import script_callbacks

from lib_generation_scheduler import constants, executor, routes, settings, ui
from lib_generation_scheduler.service import Scheduler
from lib_generation_scheduler.wiring import Wiring

logger = logging.getLogger("generation_scheduler")

_scheduler: Scheduler | None = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> Scheduler:
    """The process-wide queue, created on first use (it survives UI reloads)."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = Scheduler(
                settings.resolve_data_dir(settings.safe_opt(constants.OPT_DATA_DIR)),
                executor.ForgeExecutor(),
                history_limit=settings.history_limit,
                pause_on_interrupt=lambda: bool(settings.safe_opt(constants.OPT_PAUSE_ON_INTERRUPT)),
            )
        return _scheduler


def _capture_context() -> dict:
    names = executor.parse_option_names(settings.safe_opt(constants.OPT_SNAPSHOT_OPTIONS))
    return executor.capture_context(names)


wiring = Wiring(
    get_scheduler,
    _capture_context,
    is_enabled=lambda: bool(settings.safe_opt(constants.OPT_SHOW_QUEUE_BUTTON)),
)


def _on_ui_tabs():
    wiring.connect_all()
    return [(ui.create_queue_tab(), ui.TAB_LABEL, ui.TAB_ID)]


def _on_app_started(_demo, app):
    scheduler = get_scheduler()
    routes.register_routes(app, get_scheduler)
    scheduler.start(autostart=bool(settings.safe_opt(constants.OPT_AUTOSTART_ON_LAUNCH)))


script_callbacks.on_ui_settings(settings.on_ui_settings)
script_callbacks.on_before_ui(wiring.reset)
script_callbacks.on_after_component(wiring.on_after_component)
script_callbacks.on_ui_tabs(_on_ui_tabs)
script_callbacks.on_app_started(_on_app_started)
