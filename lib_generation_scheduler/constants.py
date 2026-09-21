from __future__ import annotations

EXTENSION_NAME = "Generation Scheduler"
SECTION = ("generation_scheduler", EXTENSION_NAME)

API_PREFIX = "/gsched/v1"

# Tabs that get a Queue button: tab name -> elem_id of the Generate button.
GENERATE_BUTTON_IDS = {
    "txt2img": "txt2img_generate",
    "img2img": "img2img_generate",
}

# Job kinds double as tab names.
KINDS = tuple(GENERATE_BUTTON_IDS)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
INTERRUPTED = "interrupted"

FINISHED_STATUSES = (DONE, FAILED, INTERRUPTED)
ALL_STATUSES = (PENDING, RUNNING, *FINISHED_STATUSES)

DB_FILENAME = "queue.sqlite3"

OPT_SHOW_QUEUE_BUTTON = "gsched_show_queue_button"
OPT_OVERRIDE_CTRL_ENTER = "gsched_override_ctrl_enter"
OPT_AUTOSTART_ON_LAUNCH = "gsched_autostart_on_launch"
OPT_PAUSE_ON_INTERRUPT = "gsched_pause_on_interrupt"
OPT_HISTORY_LIMIT = "gsched_history_limit"
OPT_SNAPSHOT_OPTIONS = "gsched_snapshot_options"
OPT_DATA_DIR = "gsched_data_dir"
