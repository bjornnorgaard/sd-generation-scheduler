from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeClock, FakeExecutor

from lib_generation_scheduler import codec, constants
from lib_generation_scheduler.runner import Outcome
from lib_generation_scheduler.service import Scheduler
from lib_generation_scheduler.store import InvalidJobState, JobNotFound

try:
    import PIL.Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None

ROLES = {"prompt": 1, "width": 2}


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.executor = FakeExecutor()
        self.history = 100
        self.scheduler = Scheduler(
            self.tmp / "data",
            self.executor,
            history_limit=lambda: self.history,
            clock=FakeClock(),
            poll_seconds=0.05,
        )
        self.addCleanup(self.scheduler.store.close)
        self.addCleanup(self.scheduler.stop)

    def enqueue(self, prompt="p", *extra, kind="txt2img"):
        return self.scheduler.enqueue(kind, ["", prompt, 512, *extra], roles=ROLES, user="bo")

    def run_all(self):
        self.scheduler.start(autostart=True)
        self.assertTrue(self.scheduler.runner.wait_idle())


class EnqueueTests(SchedulerTestCase):
    def test_enqueue_persists_summary_and_reports_position(self):
        first = self.enqueue("a cat")
        second = self.enqueue("a dog")
        self.assertEqual(first.ahead, 0)
        self.assertEqual(second.ahead, 1)
        self.assertEqual(first.job.summary, {"tab": "txt2img", "prompt": "a cat", "width": 512})
        self.assertEqual(first.job.user, "bo")
        self.assertIsNone(first.job.inputs_dir)  # nothing needed side files

    def test_enqueue_stores_context(self):
        result = self.scheduler.enqueue("txt2img", ["", "p"], context={"model": "x"})
        self.assertEqual(result.job.context, {"model": "x"})

    def test_unserializable_argument_leaves_no_trace(self):
        with self.assertRaises(codec.UnserializableArgument):
            self.scheduler.enqueue("txt2img", ["", object()])
        self.assertEqual(self.scheduler.store.pending(), [])
        self.assertEqual(list(self.scheduler.inputs_root.glob("*")), [])

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.scheduler.enqueue("extras", [])

    def test_enqueued_args_reach_the_executor_verbatim(self):
        self.enqueue("hello", ["style"], (1, 2))
        self.run_all()
        self.assertEqual(self.executor.runs[0][1], ["", "hello", 512, ["style"], (1, 2)])

    @unittest.skipIf(PILImage is None, "Pillow not installed")
    def test_images_are_saved_with_the_job_and_removed_with_it(self):
        image = PILImage.new("RGB", (3, 3), (9, 8, 7))
        result = self.scheduler.enqueue("img2img", ["", "p", image])
        directory = Path(result.job.inputs_dir)
        self.assertTrue(any(directory.iterdir()))
        self.scheduler.remove(result.job.id)
        self.assertFalse(directory.exists())

    @unittest.skipIf(PILImage is None, "Pillow not installed")
    def test_image_arrives_at_the_executor(self):
        image = PILImage.new("RGB", (3, 3), (9, 8, 7))
        self.scheduler.enqueue("img2img", ["", "p", image])
        self.run_all()
        restored = self.executor.runs[0][1][2]
        self.assertEqual(restored.getpixel((0, 0)), (9, 8, 7))


class CommandTests(SchedulerTestCase):
    def test_remove_and_clear(self):
        a, b, c = (self.enqueue(n).job for n in "abc")
        self.scheduler.remove(b.id)
        self.assertEqual([j.id for j in self.scheduler.store.pending()], [a.id, c.id])
        self.assertEqual(self.scheduler.clear("pending"), 2)
        self.assertEqual(self.scheduler.store.pending(), [])

    def test_move(self):
        a, b = self.enqueue("a").job, self.enqueue("b").job
        self.assertTrue(self.scheduler.move(b.id, "top"))
        self.assertEqual([j.id for j in self.scheduler.store.pending()], [b.id, a.id])

    def test_requeue_finished_job_runs_it_again_with_a_copy_of_its_inputs(self):
        if PILImage is None:
            self.skipTest("Pillow not installed")
        image = PILImage.new("RGB", (2, 2), (1, 2, 3))
        original = self.scheduler.enqueue("img2img", ["", "p", image]).job
        self.run_all()
        copy = self.scheduler.requeue(original.id)
        self.assertNotEqual(copy.inputs_dir, original.inputs_dir)
        self.assertTrue(any(Path(copy.inputs_dir).iterdir()))
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertEqual(self.scheduler.store.get(copy.id).status, constants.DONE)
        self.assertEqual(len(self.executor.runs), 2)

    def test_requeue_missing(self):
        with self.assertRaises(JobNotFound):
            self.scheduler.requeue(42)

    def test_remove_running_refused(self):
        import threading

        self.executor.gate = threading.Event()
        job = self.enqueue().job
        self.scheduler.start(autostart=True)
        self.assertTrue(self.executor.started.wait(2))
        with self.assertRaises(InvalidJobState):
            self.scheduler.remove(job.id)
        self.assertTrue(self.scheduler.interrupt())
        self.assertTrue(self.scheduler.runner.wait_idle())

    def test_pause_resume(self):
        self.enqueue()
        self.scheduler.pause()
        self.scheduler.start(autostart=False)
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertEqual(self.executor.runs, [])
        self.scheduler.resume()
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertEqual(len(self.executor.runs), 1)


class StartupTests(SchedulerTestCase):
    def test_default_start_holds_the_backlog(self):
        self.enqueue()
        self.scheduler.start()
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertTrue(self.scheduler.runner.paused)
        self.assertEqual(self.executor.runs, [])

    def test_autostart_resumes_a_persisted_pause(self):
        self.enqueue()
        self.scheduler.pause()
        self.scheduler.start(autostart=True)
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertEqual(len(self.executor.runs), 1)

    def test_empty_queue_start_keeps_running_state(self):
        self.scheduler.start()
        self.assertFalse(self.scheduler.runner.paused)
        self.enqueue()  # first job runs immediately without pressing Start
        self.assertTrue(self.scheduler.runner.wait_idle())
        self.assertEqual(len(self.executor.runs), 1)

    def test_running_job_from_previous_session_becomes_interrupted(self):
        job = self.enqueue().job
        self.scheduler.store.claim_next()
        self.scheduler.start()
        recovered = self.scheduler.get_job(job.id)
        self.assertEqual(recovered.status, constants.INTERRUPTED)

    def test_state_survives_a_new_scheduler_on_the_same_data_dir(self):
        self.enqueue("kept")
        self.scheduler.pause()
        self.scheduler.stop()
        self.scheduler.store.close()
        again = Scheduler(self.tmp / "data", self.executor, clock=FakeClock())
        self.addCleanup(again.store.close)
        self.assertEqual(again.state()["pending"][0]["summary"]["prompt"], "kept")
        self.assertTrue(again.state()["paused"])


class HistoryTests(SchedulerTestCase):
    def test_history_is_pruned_to_the_limit_newest_kept(self):
        self.history = 2
        for n in range(4):
            self.enqueue(str(n))
        self.run_all()
        finished = self.scheduler.state()["finished"]
        self.assertEqual([j["summary"]["prompt"] for j in finished], ["3", "2"])

    def test_state_shape(self):
        self.enqueue("a")
        self.enqueue("b")
        state = self.scheduler.state()
        self.assertEqual(set(state), {"paused", "busy", "running", "progress", "pending",
                                      "finished", "counts", "now"})
        self.assertEqual([j["summary"]["prompt"] for j in state["pending"]], ["a", "b"])
        self.assertEqual(state["counts"][constants.PENDING], 2)
        self.assertIsNone(state["running"])
        self.assertIsNone(state["progress"])

    def test_running_job_and_progress_in_state(self):
        import threading

        self.executor.gate = threading.Event()
        self.enqueue("a")
        self.scheduler.start(autostart=True)
        self.assertTrue(self.executor.started.wait(2))
        state = self.scheduler.state()
        self.assertEqual(state["running"]["summary"]["prompt"], "a")
        self.assertEqual(state["progress"]["step"], 3)
        self.executor.gate.set()
        self.assertTrue(self.scheduler.runner.wait_idle())


class OutputPathTests(SchedulerTestCase):
    def test_only_recorded_outputs_are_served(self):
        image = self.tmp / "out.png"
        image.write_bytes(b"png")
        self.executor.outcomes = [Outcome(constants.DONE, result={"outputs": [str(image), "/etc/passwd-missing"]})]
        job = self.enqueue().job
        self.run_all()
        self.assertEqual(self.scheduler.output_path(job.id, 0), image)
        self.assertIsNone(self.scheduler.output_path(job.id, 1))  # recorded but not a file
        self.assertIsNone(self.scheduler.output_path(job.id, 2))  # out of range
        self.assertIsNone(self.scheduler.output_path(job.id, -1))
        self.assertIsNone(self.scheduler.output_path(999, 0))


if __name__ == "__main__":
    unittest.main()
