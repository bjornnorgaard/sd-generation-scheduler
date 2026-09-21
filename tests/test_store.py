from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.helpers import FakeClock

from lib_generation_scheduler import constants
from lib_generation_scheduler.store import (
    InvalidJobState,
    JobNotFound,
    JobStore,
)


def make_store(test: unittest.TestCase | None = None, **kwargs) -> JobStore:
    store = JobStore(":memory:", clock=FakeClock(), **kwargs)
    if test is not None:
        test.addCleanup(store.close)
    return store


def add(store: JobStore, prompt: str = "p", kind: str = "txt2img"):
    return store.add_job(kind, "[]", summary={"prompt": prompt})


class AddAndReadTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)

    def test_add_returns_pending_job_with_summary(self):
        job = self.store.add_job(
            "txt2img", "[1]", summary={"prompt": "a cat"}, context={"m": 1}, user="bo"
        )
        self.assertEqual(job.status, constants.PENDING)
        self.assertEqual(job.summary, {"prompt": "a cat"})
        self.assertEqual(job.context, {"m": 1})
        self.assertEqual(job.user, "bo")
        self.assertEqual(self.store.get_args(job.id), "[1]")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add_job("extras", "[]")

    def test_pending_is_fifo(self):
        a, b, c = add(self.store, "a"), add(self.store, "b"), add(self.store, "c")
        self.assertEqual([j.id for j in self.store.pending()], [a.id, b.id, c.id])

    def test_public_view_hides_args(self):
        job = add(self.store)
        self.assertNotIn("args", job.public())

    def test_get_missing(self):
        self.assertIsNone(self.store.get(99))
        with self.assertRaises(JobNotFound):
            self.store.get_args(99)

    def test_counts(self):
        add(self.store)
        add(self.store)
        counts = self.store.counts()
        self.assertEqual(counts[constants.PENDING], 2)
        self.assertEqual(counts[constants.RUNNING], 0)

    def test_pending_ahead_counts_running_and_earlier(self):
        a, b, c = add(self.store), add(self.store), add(self.store)
        self.assertEqual(self.store.pending_ahead(a.id), 0)
        self.assertEqual(self.store.pending_ahead(c.id), 2)
        self.store.claim_next()  # a → running
        self.assertEqual(self.store.pending_ahead(b.id), 1)
        self.assertEqual(self.store.pending_ahead(c.id), 2)
        self.assertEqual(self.store.pending_ahead(a.id), 0)  # not pending any more


class ClaimAndFinishTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)

    def test_claim_takes_first_pending_and_marks_running(self):
        a, b = add(self.store), add(self.store)
        claimed = self.store.claim_next()
        self.assertEqual(claimed.id, a.id)
        self.assertEqual(claimed.status, constants.RUNNING)
        self.assertIsNotNone(claimed.started_at)
        self.assertEqual(self.store.running().id, a.id)
        self.assertEqual(self.store.claim_next().id, b.id)

    def test_claim_empty(self):
        self.assertIsNone(self.store.claim_next())
        self.assertFalse(self.store.has_pending())

    def test_finish_records_result(self):
        job = add(self.store)
        self.store.claim_next()
        done = self.store.finish(job.id, constants.DONE, result={"outputs": ["a.png"]})
        self.assertEqual(done.status, constants.DONE)
        self.assertEqual(done.result, {"outputs": ["a.png"]})
        self.assertIsNotNone(done.finished_at)

    def test_finish_failed_records_error(self):
        job = add(self.store)
        self.store.claim_next()
        failed = self.store.finish(job.id, constants.FAILED, error="boom")
        self.assertEqual(failed.error, "boom")

    def test_finish_requires_running(self):
        job = add(self.store)
        with self.assertRaises(InvalidJobState):
            self.store.finish(job.id, constants.DONE)

    def test_finish_rejects_non_final_status(self):
        job = add(self.store)
        self.store.claim_next()
        with self.assertRaises(ValueError):
            self.store.finish(job.id, constants.PENDING)

    def test_finished_newest_first_with_limit(self):
        ids = []
        for _ in range(3):
            job = add(self.store)
            self.store.claim_next()
            self.store.finish(job.id, constants.DONE)
            ids.append(job.id)
        self.assertEqual([j.id for j in self.store.finished()], ids[::-1])
        self.assertEqual([j.id for j in self.store.finished(limit=2)], ids[:0:-1])

    def test_recover_interrupted(self):
        job = add(self.store)
        self.store.claim_next()
        self.assertEqual(self.store.recover_interrupted(), 1)
        recovered = self.store.get(job.id)
        self.assertEqual(recovered.status, constants.INTERRUPTED)
        self.assertIn("stopped", recovered.error)
        self.assertEqual(self.store.recover_interrupted(), 0)


class RemoveClearTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)

    def test_remove_pending(self):
        job = add(self.store)
        removed = self.store.remove(job.id)
        self.assertEqual(removed.id, job.id)
        self.assertIsNone(self.store.get(job.id))

    def test_remove_running_refused(self):
        job = add(self.store)
        self.store.claim_next()
        with self.assertRaises(InvalidJobState):
            self.store.remove(job.id)
        self.assertIsNotNone(self.store.get(job.id))

    def test_remove_missing(self):
        with self.assertRaises(JobNotFound):
            self.store.remove(5)

    def test_clear_scopes(self):
        done = add(self.store)
        self.store.claim_next()
        self.store.finish(done.id, constants.DONE)
        running = add(self.store)
        self.store.claim_next()
        p1, p2 = add(self.store), add(self.store)

        self.assertEqual([j.id for j in self.store.clear("pending")], [p1.id, p2.id])
        self.assertIsNotNone(self.store.get(running.id))
        self.assertIsNotNone(self.store.get(done.id))

        self.assertEqual([j.id for j in self.store.clear("finished")], [done.id])
        self.assertIsNotNone(self.store.get(running.id))  # never touches the running job

        add(self.store)
        cleared = self.store.clear("all")
        self.assertEqual(len(cleared), 1)
        self.assertIsNotNone(self.store.get(running.id))

    def test_clear_unknown_scope(self):
        with self.assertRaises(ValueError):
            self.store.clear("everything")

    def test_prune_finished_keeps_newest(self):
        ids = []
        for _ in range(4):
            job = add(self.store)
            self.store.claim_next()
            self.store.finish(job.id, constants.DONE)
            ids.append(job.id)
        pruned = self.store.prune_finished(keep=2)
        self.assertEqual(sorted(j.id for j in pruned), sorted(ids[:2]))
        self.assertEqual([j.id for j in self.store.finished()], ids[:1:-1])
        self.assertEqual(self.store.prune_finished(keep=2), [])


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)
        self.a, self.b, self.c, self.d = (add(self.store, n) for n in "abcd")

    def order(self):
        return [j.summary["prompt"] for j in self.store.pending()]

    def test_move_down_then_up(self):
        self.assertTrue(self.store.move(self.a.id, "down"))
        self.assertEqual(self.order(), ["b", "a", "c", "d"])
        self.assertTrue(self.store.move(self.a.id, "up"))
        self.assertEqual(self.order(), ["a", "b", "c", "d"])

    def test_move_top_and_bottom(self):
        self.assertTrue(self.store.move(self.c.id, "top"))
        self.assertEqual(self.order(), ["c", "a", "b", "d"])
        self.assertTrue(self.store.move(self.c.id, "bottom"))
        self.assertEqual(self.order(), ["a", "b", "d", "c"])

    def test_boundaries_are_noops(self):
        self.assertFalse(self.store.move(self.a.id, "up"))
        self.assertFalse(self.store.move(self.a.id, "top"))
        self.assertFalse(self.store.move(self.d.id, "down"))
        self.assertFalse(self.store.move(self.d.id, "bottom"))
        self.assertEqual(self.order(), ["a", "b", "c", "d"])

    def test_repeated_top_moves_keep_order_consistent(self):
        for job in (self.b, self.c, self.d):
            self.store.move(job.id, "top")
        self.assertEqual(self.order(), ["d", "c", "b", "a"])

    def test_only_pending_can_move(self):
        self.store.claim_next()
        with self.assertRaises(InvalidJobState):
            self.store.move(self.a.id, "down")

    def test_unknown_move_and_job(self):
        with self.assertRaises(ValueError):
            self.store.move(self.a.id, "sideways")
        with self.assertRaises(JobNotFound):
            self.store.move(99, "up")

    def test_moved_job_is_claimed_first(self):
        self.store.move(self.d.id, "top")
        self.assertEqual(self.store.claim_next().id, self.d.id)


class DuplicateAndMetaTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store(self)

    def test_duplicate_appends_pending_copy(self):
        job = self.store.add_job("img2img", '["x"]', summary={"prompt": "hi"}, context={"c": 1})
        self.store.claim_next()
        self.store.finish(job.id, constants.DONE, result={"outputs": ["o.png"]})
        later = add(self.store, "later")

        copy = self.store.duplicate(job.id)
        self.assertEqual(copy.status, constants.PENDING)
        self.assertEqual(copy.kind, "img2img")
        self.assertEqual(copy.summary, {"prompt": "hi"})
        self.assertEqual(copy.context, {"c": 1})
        self.assertIsNone(copy.result)
        self.assertEqual(self.store.get_args(copy.id), '["x"]')
        self.assertEqual([j.id for j in self.store.pending()], [later.id, copy.id])

    def test_duplicate_missing(self):
        with self.assertRaises(JobNotFound):
            self.store.duplicate(3)

    def test_meta_roundtrip(self):
        self.assertIsNone(self.store.get_meta("k"))
        self.assertEqual(self.store.get_meta("k", "d"), "d")
        self.store.set_meta("k", "1")
        self.store.set_meta("k", "2")
        self.assertEqual(self.store.get_meta("k"), "2")


class PersistenceTests(unittest.TestCase):
    def test_reopen_keeps_jobs_and_meta(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "queue.sqlite3"
            first = JobStore(path)
            job = first.add_job("txt2img", '["a"]', summary={"prompt": "kept"})
            first.set_meta("paused", "1")
            first.close()

            second = JobStore(path)
            self.assertEqual(second.get(job.id).summary, {"prompt": "kept"})
            self.assertEqual(second.get_args(job.id), '["a"]')
            self.assertEqual(second.get_meta("paused"), "1")
            newer = second.add_job("txt2img", "[]")
            self.assertGreater(newer.id, job.id)
            second.close()


if __name__ == "__main__":
    unittest.main()
