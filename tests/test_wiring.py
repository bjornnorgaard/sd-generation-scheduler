"""Wiring against the real Gradio (skipped where Gradio isn't installed, e.g. system python).

These build a miniature of Forge's tab layout — a ``Blocks`` with a Generate button whose
click is wired only at the very end — and check the Queue button ends up next to it and
receives the same inputs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import FakeClock, FakeExecutor

try:
    import gradio as gr
    from gradio.context import Context
    from gradio.helpers import special_args
except ImportError:  # pragma: no cover
    gr = None

if gr is not None:
    from lib_generation_scheduler import constants
    from lib_generation_scheduler import wiring as wiring_module
    from lib_generation_scheduler.service import Scheduler


def build_tab(wiring, tab="txt2img", wire_generate=True, decoys=False, gallery=True):
    """A stand-in for Forge's create_ui(): the hook fires as each component is created."""
    original = gr.Button.__init__

    def hooked(self, *args, **kwargs):
        original(self, *args, **kwargs)
        wiring.on_after_component(self, **kwargs)

    gr.Button.__init__ = hooked
    try:
        with gr.Blocks() as blocks:
            with gr.Row(elem_id=f"{tab}_generate_box"):
                generate = gr.Button("Generate", elem_id=f"{tab}_generate")
            task = gr.Textbox(visible=False)
            prompt = gr.Textbox(elem_id=f"{tab}_prompt")
            width = gr.Slider(elem_id=f"{tab}_width")
            steps = gr.Slider(elem_id=f"{tab}_steps")
            output = gr.Textbox(elem_id=f"{tab}_gallery" if gallery else None)
            if decoys:
                # Like ControlNet: extra handlers on the same click, registered first.
                for _ in range(2):
                    generate.click(fn=lambda a, b: None, inputs=[prompt, width], outputs=[gr.Textbox()])
            if wire_generate:
                generate.click(
                    fn=lambda *a: "generated",
                    inputs=[task, prompt, width, steps],
                    outputs=[output],
                    js="submit",
                )
                generate.click(fn=lambda: None)  # like Forge's .then(cleanup)
    finally:
        gr.Button.__init__ = original
    return blocks, generate, [task, prompt, width, steps]


def find_path(node, target, path=()):
    if node["id"] == target:
        return path
    for child in node.get("children") or []:
        found = find_path(child, target, path + (node["id"],))
        if found is not None:
            return found
    return None


@unittest.skipIf(gr is None, "gradio not installed")
class WiringTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scheduler = Scheduler(Path(tmp.name), FakeExecutor(), clock=FakeClock(), poll_seconds=0.05)
        self.addCleanup(self.scheduler.store.close)
        self.context = {"model": "m.safetensors"}
        self.enabled = True
        self.wiring = wiring_module.Wiring(
            lambda: self.scheduler, lambda: self.context, is_enabled=lambda: self.enabled
        )

    def render(self, *blocks_list):
        with gr.Blocks() as demo:
            with gr.Tabs():
                for blocks in blocks_list:
                    with gr.Tab("t"):
                        blocks.render()
        return demo

    def queue_dependency(self, demo, queue_button):
        config = demo.get_config_file()
        return [d for d in config["dependencies"]
                if any(t[0] == queue_button._id for t in d["targets"])]


class QueueButtonTests(WiringTestCase):
    def test_queue_button_sits_next_to_generate_and_reuses_its_inputs(self):
        blocks, generate, inputs = build_tab(self.wiring)
        tab = self.wiring.tabs["txt2img"]
        self.assertIs(tab.blocks, blocks)
        self.assertEqual(tab.queue_button.elem_id, "txt2img_queue")

        self.wiring.connect_all()
        demo = self.render(blocks)
        config = demo.get_config_file()
        (dep,) = self.queue_dependency(demo, tab.queue_button)
        self.assertEqual(dep["inputs"], [c._id for c in inputs])
        self.assertEqual(dep["outputs"], [])
        self.assertTrue(dep["queue"])
        self.assertIn("Array.from(arguments)", dep["js"])
        self.assertEqual(
            find_path(config["layout"], tab.queue_button._id),
            find_path(config["layout"], generate._id),
        )

    def test_generate_wiring_is_left_untouched(self):
        blocks, generate, inputs = build_tab(self.wiring)
        self.wiring.connect_all()
        demo = self.render(blocks)
        generate_deps = self.queue_dependency(demo, generate)
        self.assertEqual(len(generate_deps), 2)
        self.assertEqual(generate_deps[0]["js"], "submit")

    def test_both_tabs_are_wired_independently(self):
        t2i, _, t2i_inputs = build_tab(self.wiring, "txt2img")
        i2i, _, i2i_inputs = build_tab(self.wiring, "img2img")
        self.wiring.connect_all()
        demo = self.render(t2i, i2i)
        for tab, inputs in (("txt2img", t2i_inputs), ("img2img", i2i_inputs)):
            (dep,) = self.queue_dependency(demo, self.wiring.tabs[tab].queue_button)
            self.assertEqual(dep["inputs"], [c._id for c in inputs])

    def test_disabled_option_adds_no_button(self):
        self.enabled = False
        build_tab(self.wiring)
        self.assertEqual(self.wiring.tabs, {})

    def test_unrelated_buttons_are_ignored(self):
        with gr.Blocks():
            gr.Button("Other", elem_id="something_else")
            self.wiring.on_after_component(gr.Button("x", elem_id="txt2img_interrupt"), elem_id="txt2img_interrupt")
        self.assertEqual(self.wiring.tabs, {})

    def test_reset_forgets_tabs(self):
        build_tab(self.wiring)
        self.wiring.reset()
        self.assertEqual(self.wiring.tabs, {})

    def test_missing_generate_handler_leaves_button_inert_without_crashing(self):
        blocks, _, _ = build_tab(self.wiring, wire_generate=False)
        with self.assertLogs("generation_scheduler", "WARNING"):
            self.wiring.connect_all()
        demo = self.render(blocks)
        self.assertEqual(self.queue_dependency(demo, self.wiring.tabs["txt2img"].queue_button), [])

    def test_context_is_restored_after_connecting(self):
        blocks, _, _ = build_tab(self.wiring)
        before = Context.root_block
        self.wiring.connect_all()
        self.assertIs(Context.root_block, before)

    def test_extension_handlers_on_the_same_click_are_not_mistaken_for_generate(self):
        blocks, _, inputs = build_tab(self.wiring, decoys=True)
        self.wiring.connect_all()
        demo = self.render(blocks)
        (dep,) = self.queue_dependency(demo, self.wiring.tabs["txt2img"].queue_button)
        self.assertEqual(dep["inputs"], [c._id for c in inputs])

    def test_without_a_gallery_output_the_widest_handler_wins(self):
        blocks, generate, inputs = build_tab(self.wiring, decoys=True, gallery=False)
        function = wiring_module.find_generate_function(blocks, generate, "txt2img")
        self.assertEqual([c._id for c in function.inputs], [c._id for c in inputs])

    def test_find_generate_function(self):
        blocks, generate, inputs = build_tab(self.wiring)
        function = wiring_module.find_generate_function(blocks, generate)
        self.assertEqual([c._id for c in function.inputs], [c._id for c in inputs])
        self.assertIsNone(wiring_module.find_generate_function(blocks, gr.Button("x")))


class ClickHandlerTests(WiringTestCase):
    def handler(self, roles=None):
        return wiring_module.make_click_handler(
            "txt2img", roles or {"prompt": 1, "width": 2}, lambda: self.scheduler, lambda: self.context
        )

    def test_gradio_injects_the_request_first(self):
        handler = self.handler()
        request = object()
        args = special_args(handler, [1, 2, 3], request)[0]
        self.assertIs(args[0], request)
        self.assertEqual(args[1:], [1, 2, 3])

    def test_click_queues_a_job_with_context_user_and_summary(self):
        request = SimpleNamespace(username="bo")
        self.handler()(request, "", "a cat", 640, 20)
        (job,) = self.scheduler.store.pending()
        self.assertEqual(job.kind, "txt2img")
        self.assertEqual(job.user, "bo")
        self.assertEqual(job.context, {"model": "m.safetensors"})
        self.assertEqual(job.summary["prompt"], "a cat")
        self.assertEqual(job.summary["width"], 640)
        self.assertEqual(self.scheduler.store.get_args(job.id), '["", "a cat", 640, 20]')

    def test_unserializable_input_becomes_a_gradio_error(self):
        with self.assertRaises(gr.Error) as ctx:
            self.handler()(SimpleNamespace(username=None), "", object())
        self.assertIn("Can't queue", str(ctx.exception))
        self.assertEqual(self.scheduler.store.pending(), [])


if __name__ == "__main__":
    unittest.main()
