from __future__ import annotations

import logging
from pathlib import Path

from lib_generation_scheduler import constants

logger = logging.getLogger("generation_scheduler")

EXTENSION_ROOT = Path(__file__).resolve().parents[1]

OPT_DEFAULTS: dict[str, object] = {
    constants.OPT_SHOW_QUEUE_BUTTON: True,
    constants.OPT_OVERRIDE_CTRL_ENTER: True,
    constants.OPT_AUTOSTART_ON_LAUNCH: False,
    constants.OPT_PAUSE_ON_INTERRUPT: True,
    constants.OPT_HISTORY_LIMIT: 100,
    constants.OPT_SNAPSHOT_OPTIONS: "CLIP_stop_at_last_layers",
    constants.OPT_DATA_DIR: "",
}


def safe_opt(key: str):
    """Read an option, falling back to its default when the WebUI (or the key) is absent."""
    default = OPT_DEFAULTS[key]
    try:
        from modules import shared  # noqa: PLC0415

        return getattr(shared.opts, key, default)
    except Exception:  # noqa: BLE001
        return default


def history_limit() -> int:
    try:
        return max(int(safe_opt(constants.OPT_HISTORY_LIMIT)), 0)
    except (TypeError, ValueError):
        return int(OPT_DEFAULTS[constants.OPT_HISTORY_LIMIT])


def resolve_data_dir(configured: str | None, root: Path = EXTENSION_ROOT) -> Path:
    """Where the queue database and saved inputs live. Empty means ``<extension>/data``."""
    configured = (configured or "").strip()
    if not configured:
        return root / "data"
    path = Path(configured).expanduser()
    return path if path.is_absolute() else root / path


def on_ui_settings() -> None:
    from modules import shared  # noqa: PLC0415

    section = constants.SECTION

    def add(key: str, label: str, info: str | None = None, **kwargs) -> None:
        option = shared.OptionInfo(OPT_DEFAULTS[key], label, section=section, **kwargs)
        if info:
            option = option.info(info)
        shared.opts.add_option(key, option)

    add(
        constants.OPT_SHOW_QUEUE_BUTTON,
        "Show a Queue button next to Generate",
        "Reload the UI after changing this.",
    )
    add(
        constants.OPT_OVERRIDE_CTRL_ENTER,
        "Ctrl+Enter adds to the queue instead of generating",
        "An idle queue starts the job immediately. Interrupt and Skip stay on their buttons, Esc and "
        "Alt+Enter. When off, Forge's default applies. Applies after a page reload.",
    )
    add(
        constants.OPT_AUTOSTART_ON_LAUNCH,
        "Resume the queue automatically when the WebUI starts",
        "When off, jobs left over from last session wait until you press Start on the Queue tab.",
    )
    add(
        constants.OPT_PAUSE_ON_INTERRUPT,
        "Pause the queue when a running job is interrupted",
        "When off, Interrupt only stops the current job and the queue moves on to the next one.",
    )
    add(
        constants.OPT_HISTORY_LIMIT,
        "Finished jobs to keep",
        "Older finished jobs (and their saved input images) are deleted automatically.",
    )
    add(
        constants.OPT_SNAPSHOT_OPTIONS,
        "Extra settings to remember per job",
        "Comma-separated setting keys captured when you press Queue and applied while that job "
        "runs, e.g. CLIP_stop_at_last_layers. The checkpoint, VAE / text encoders and UNet dtype "
        "are always remembered.",
    )
    add(
        constants.OPT_DATA_DIR,
        "Queue data directory",
        f"Holds the queue database and saved input images. Empty = {EXTENSION_ROOT / 'data'}. "
        "Restart the WebUI after changing this.",
    )
