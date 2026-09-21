from __future__ import annotations

import logging
import threading
import unittest

from tests.helpers import FakeClock, FakeExecutor

from lib_generation_scheduler import codec, constants
from lib_generation_scheduler.runner import Outcome, Runner
from lib_generation_scheduler.store import JobStore


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.store = JobStore(":memory:", clock=FakeClock())
        self.addCleanup(self.store.close)
        self.executor = FakeExecutor()
        self.finished: list[int] = []
        self.pause_on_interrupt = True
        self.runner = self.make_runner()

    def make_runner(self) -> Runner:
        runner = Runner(
            self.store,
            self.executor,
            pause_on_interrupt=lambda: self.pause_on_interrupt,
            on_finished=lambda job: self.finished.append(job.id),
            poll_seconds=0.05,
        )
        self.addCleanup(runner.stop)
        return runner

    def add(self, *args):
        return self.store.add_job("txt2img", codec.encode_args(list(args), "unused"))


class ProcessingTests(RunnerTestCase):
    def test_runs_pending_jobs_in_order_and_records_results(self):
        a, b = self.add("a"), self.add("b")
        self.executor.outcomes = [
            Outcome(constants.DONE, result={"outputs": ["a.png"]}),
            Outcome(constants.FAILED, error="OOM"),
        ]
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())

        self.assertEqual([r[0] for r in self.executor.runs], [a.id, b.id])
        self.assertEqual([r[1] for r in self.executor.runs], [["a"], ["b"]])
        self.assertEqual(self.store.get(a.id).status, constants.DONE)
        self.assertEqual(self.store.get(a.id).result, {"outputs": ["a.png"]})
        self.assertEqual(self.store.get(b.id).status, constants.FAILED)
        self.assertEqual(self.store.get(b.id).error, "OOM")
        self.assertEqual(self.finished, [a.id, b.id])

    def test_failure_does_not_stop_the_queue(self):
        self.add("a"), self.add("b")
        self.executor.outcomes = [Outcome(constants.FAILED, error="x")]
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.counts()[constants.DONE], 1)
        self.assertEqual(self.store.counts()[constants.FAILED], 1)

    def test_executor_exception_marks_failed_and_worker_survives(self):
        a, b = self.add("a"), self.add("b")
        self.executor.outcomes = [RuntimeError("kaboom")]
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(a.id).status, constants.FAILED)
        self.assertEqual(self.store.get(a.id).error, "RuntimeError: kaboom")
        self.assertEqual(self.store.get(b.id).status, constants.DONE)

    def test_undecodable_arguments_fail_the_job(self):
        job = self.store.add_job("txt2img", '[{"__gsched__": "image", "file": "gone.png"}]',
                                 inputs_dir="/nonexistent")
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(job.id).status, constants.FAILED)
        self.assertEqual(self.executor.runs, [])

    def test_jobs_added_while_running_are_picked_up(self):
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        job = self.add("late")
        self.runner.wake()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(job.id).status, constants.DONE)

    def test_only_one_job_runs_at_a_time(self):
        self.executor.gate = threading.Event()
        a, b = self.add("a"), self.add("b")
        self.runner.start()
        self.assertTrue(self.executor.started.wait(2))
        self.assertTrue(self.runner.busy)
        self.assertEqual(self.runner.current.id, a.id)
        self.assertEqual(self.store.get(b.id).status, constants.PENDING)
        self.assertEqual(self.runner.progress(), {"step": 3, "steps": 20, "job_no": 0, "job_count": 1})
        self.executor.gate.set()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(len(self.executor.runs), 2)
        self.assertIsNone(self.runner.progress())


class PauseTests(RunnerTestCase):
    def test_paused_runner_does_not_start_jobs(self):
        job = self.add("a")
        self.runner.pause()
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.executor.runs, [])
        self.assertEqual(self.store.get(job.id).status, constants.PENDING)

    def test_resume_processes_backlog(self):
        job = self.add("a")
        self.runner.pause()
        self.runner.start()
        self.runner.resume()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(job.id).status, constants.DONE)

    def test_pause_lets_current_job_finish_then_holds_the_rest(self):
        self.executor.gate = threading.Event()
        a, b = self.add("a"), self.add("b")
        self.runner.start()
        self.assertTrue(self.executor.started.wait(2))
        self.runner.pause()
        self.executor.gate.set()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(a.id).status, constants.DONE)
        self.assertEqual(self.store.get(b.id).status, constants.PENDING)

    def test_pause_state_persists_across_runners(self):
        self.runner.pause()
        self.assertTrue(self.make_runner().paused)
        self.runner.resume()
        self.assertFalse(self.make_runner().paused)

    def test_stop_ends_the_thread(self):
        self.runner.start()
        thread = self.runner._thread
        self.runner.stop()
        self.assertFalse(thread.is_alive())

    def test_start_is_idempotent(self):
        self.runner.start()
        thread = self.runner._thread
        self.runner.start()
        self.assertIs(self.runner._thread, thread)


class InterruptTests(RunnerTestCase):
    def test_interrupt_without_running_job(self):
        self.assertFalse(self.runner.interrupt_current())
        self.assertEqual(self.executor.interrupted, 0)

    def interrupted_run(self):
        self.executor.gate = threading.Event()
        self.executor.outcomes = [Outcome(constants.INTERRUPTED, result={"outputs": []})]
        a, b = self.add("a"), self.add("b")
        self.runner.start()
        self.assertTrue(self.executor.started.wait(2))
        self.assertTrue(self.runner.interrupt_current())
        self.assertTrue(self.runner.wait_idle())
        return a, b

    def test_interrupt_marks_job_and_pauses_by_default(self):
        a, b = self.interrupted_run()
        self.assertEqual(self.store.get(a.id).status, constants.INTERRUPTED)
        self.assertEqual(self.store.get(b.id).status, constants.PENDING)
        self.assertTrue(self.runner.paused)

    def test_interrupt_can_keep_going(self):
        self.pause_on_interrupt = False
        self.executor.gate = None
        self.executor.outcomes = [Outcome(constants.INTERRUPTED)]
        a, b = self.add("a"), self.add("b")
        self.runner.start()
        self.assertTrue(self.runner.wait_idle())
        self.assertEqual(self.store.get(a.id).status, constants.INTERRUPTED)
        self.assertEqual(self.store.get(b.id).status, constants.DONE)
        self.assertFalse(self.runner.paused)


if __name__ == "__main__":
    unittest.main()
