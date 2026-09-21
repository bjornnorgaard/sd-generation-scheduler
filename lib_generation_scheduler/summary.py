"""Human-readable job summaries.

Forge's Generate button feeds ``fn`` a positional list that differs per tab and per
Forge version, and the interesting values (steps, sampler, seed) come from scripts.
Rather than hard-code indices we resolve them from the ``elem_id`` of the Gradio
components wired to the button, once, when the tab is built.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# role -> elem_id template (``{tab}`` is the tab name)
ROLE_ELEM_IDS: dict[str, str] = {
    "prompt": "{tab}_prompt",
    "negative_prompt": "{tab}_neg_prompt",
    "width": "{tab}_width",
    "height": "{tab}_height",
    "steps": "{tab}_steps",
    "sampler": "{tab}_sampling",
    "scheduler": "{tab}_scheduler",
    "cfg_scale": "{tab}_cfg_scale",
    "seed": "{tab}_seed",
    "batch_count": "{tab}_batch_count",
    "batch_size": "{tab}_batch_size",
    "denoising_strength": "{tab}_denoising_strength",
    "hires": "{tab}_hr",
}

# Roles only worth showing on some tabs.
_TAB_ONLY = {"denoising_strength": ("img2img",), "hires": ("txt2img",)}

PROMPT_LIMIT = 400

# The Queue button's script puts a browser-generated token in the (otherwise unused) task-id
# slot, so the page can match a job to the UI snapshot it took when the button was pressed.
CLIENT_TOKEN_PREFIX = "gsched-"


def resolve_roles(tab: str, elem_ids: Sequence[str | None]) -> dict[str, int]:
    """Map summary roles to positions in the argument list, by component ``elem_id``."""
    wanted = {}
    for role, template in ROLE_ELEM_IDS.items():
        if role in _TAB_ONLY and tab not in _TAB_ONLY[role]:
            continue
        wanted[template.format(tab=tab)] = role
    roles: dict[str, int] = {}
    for index, elem_id in enumerate(elem_ids):
        role = wanted.get(elem_id or "")
        if role is not None and role not in roles:
            roles[role] = index
    return roles


def _clip(text: Any, limit: int = PROMPT_LIMIT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_summary(tab: str, args: Sequence[Any], roles: dict[str, int]) -> dict[str, Any]:
    """Pull a compact, JSON-safe description of the job out of its arguments."""

    def get(role: str) -> Any:
        index = roles.get(role)
        if index is None or index >= len(args):
            return None
        value = args[index]
        return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)

    summary: dict[str, Any] = {"tab": tab}
    token = args[0] if args else None
    if isinstance(token, str) and token.startswith(CLIENT_TOKEN_PREFIX) and len(token) <= 64:
        summary["client_token"] = token
    for role in ("prompt", "negative_prompt"):
        value = get(role)
        if value is not None:
            summary[role] = _clip(value)
    for role in (
        "width",
        "height",
        "steps",
        "sampler",
        "scheduler",
        "cfg_scale",
        "seed",
        "batch_count",
        "batch_size",
        "denoising_strength",
        "hires",
    ):
        value = get(role)
        if value not in (None, ""):
            summary[role] = value
    return summary
