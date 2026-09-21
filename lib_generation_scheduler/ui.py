"""The Queue tab. The page itself is rendered client-side by javascript/gsched_queue.js."""

from __future__ import annotations

import gradio as gr

TAB_LABEL = "Queue"
TAB_ID = "gsched_queue"

_PLACEHOLDER = """
<div id="gsched-root" class="gsched-root">
  <p class="gsched-loading">Loading queue…</p>
</div>
"""


def create_queue_tab() -> gr.Blocks:
    with gr.Blocks(analytics_enabled=False) as blocks:
        gr.HTML(_PLACEHOLDER, elem_id="gsched_root_host")
    return blocks
