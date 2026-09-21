"""Small cached previews for the Queue tab. Falls back to the original when Pillow is absent."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("generation_scheduler")

THUMB_SIZE = 256


def thumbnail_for(source: Path, cache_dir: Path, name: str, size: int = THUMB_SIZE) -> Path:
    """Path of a ≤ ``size`` px JPEG preview of ``source`` (created on first use)."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return source

    target = cache_dir / f"{name}-{size}.jpg"
    try:
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
        cache_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.thumbnail((size, size))
            image.convert("RGB").save(target, format="JPEG", quality=82)
        return target
    except Exception:  # noqa: BLE001 - a broken preview must never break the tab
        logger.debug("Generation Scheduler: thumbnail failed for %s", source, exc_info=True)
        return source
