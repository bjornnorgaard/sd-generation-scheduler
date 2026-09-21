"""Round trip through a real Gradio server (queue enabled) — skipped without gradio_client.

This is the closest we get to pressing the button without launching the WebUI: the click
travels the same queue path Forge uses, so ``gr.Info`` gets an event id and the request
object is injected.
"""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from tests.helpers import FakeClock, FakeExecutor
from tests.test_wiring import build_tab

try:
    import gradio as gr
    from gradio_client import Client
except ImportError:  # pragma: no cover
    gr = None

if gr is not None:
    from lib_generation_scheduler import wiring as wiring_module
    from lib_generation_scheduler.service import Scheduler


@unittest.skipIf(gr is None, "gradio / gradio_client not installed")
class LiveQueueClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warnings.simplefilter("ignore")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.scheduler = Scheduler(Path(cls._tmp.name), FakeExecutor(), clock=FakeClock(), poll_seconds=0.05)
        wiring = wiring_module.Wiring(lambda: cls.scheduler, lambda: {"model": "live.safetensors"})
        blocks, _generate, _inputs = build_tab(wiring)
        wiring.connect_all()
        with gr.Blocks() as demo:
            blocks.render()
        demo.queue(default_concurrency_limit=32)
        cls.demo = demo
        _app, url, _share = demo.launch(prevent_thread_lock=True, server_name="127.0.0.1", quiet=True)
        cls.client = Client(url, verbose=False)
        (cls.queue_fn,) = [d["id"] for d in cls.client.config["dependencies"]
                           if d.get("api_name") == "on_queue_click"]

    @classmethod
    def tearDownClass(cls):
        cls.demo.close()
        cls.scheduler.store.close()
        cls._tmp.cleanup()

    def test_click_over_http_queues_the_job(self):
        before = len(self.scheduler.store.pending())
        self.client.predict("", "a live cat", 704, 30, fn_index=self.queue_fn)
        pending = self.scheduler.store.pending()
        self.assertEqual(len(pending), before + 1)
        job = pending[-1]
        self.assertEqual(job.summary["prompt"], "a live cat")
        self.assertEqual(job.summary["width"], 704)
        self.assertEqual(job.summary["steps"], 30)
        self.assertEqual(job.context, {"model": "live.safetensors"})
        self.assertIsNone(job.user)  # no auth on the test server

    def test_a_running_generation_does_not_block_queueing(self):
        # The Queue click has no concurrency limit; two clicks in a row both land.
        before = len(self.scheduler.store.pending())
        job_a = self.client.submit("", "first", 512, 20, fn_index=self.queue_fn)
        job_b = self.client.submit("", "second", 512, 20, fn_index=self.queue_fn)
        job_a.result(timeout=20)
        job_b.result(timeout=20)
        prompts = [j.summary["prompt"] for j in self.scheduler.store.pending()][before:]
        self.assertEqual(sorted(prompts), ["first", "second"])


if __name__ == "__main__":
    unittest.main()
