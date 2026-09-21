from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeClock, FakeExecutor

from lib_generation_scheduler import enqueue
from lib_generation_scheduler.service import Scheduler


class EnqueueTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.scheduler = Scheduler(Path(tmp.name), FakeExecutor(), clock=FakeClock(), poll_seconds=0.05)
        self.addCleanup(self.scheduler.store.close)
        self.addCleanup(self.scheduler.stop)

    def click(self, *args):
        return enqueue.enqueue_click(
            self.scheduler, "txt2img", args or ["", "p"], roles={"prompt": 1}, context={}, user=None
        )


class EnqueueClickTests(EnqueueTestCase):
    def test_success(self):
        result = self.click("", "hello")
        self.assertEqual(result.job.summary["prompt"], "hello")

    def test_unserializable_becomes_a_user_facing_error(self):
        with self.assertRaises(enqueue.EnqueueError) as ctx:
            self.click("", object())
        message = str(ctx.exception)
        self.assertIn("Can't queue", message)
        self.assertIn("args[1]", message)
        self.assertIn("object", message)

    def test_other_errors_propagate(self):
        with self.assertRaises(ValueError):
            enqueue.enqueue_click(self.scheduler, "extras", [], roles={}, context={}, user=None)


class QueuedMessageTests(EnqueueTestCase):
    def test_first_job_starts_next(self):
        self.assertEqual(enqueue.queued_message(self.click(), paused=False), "Queued #1 — starting next")

    def test_jobs_ahead(self):
        self.click()
        self.assertEqual(enqueue.queued_message(self.click(), paused=False), "Queued #2 — 1 ahead")

    def test_paused_is_called_out(self):
        self.assertEqual(enqueue.queued_message(self.click(), paused=True), "Queued #1 (queue is paused)")
        self.assertEqual(enqueue.queued_message(self.click(), paused=True), "Queued #2 — 1 ahead (queue is paused)")


if __name__ == "__main__":
    unittest.main()
