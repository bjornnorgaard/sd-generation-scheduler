from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from tests.helpers import FakeClock, FakeExecutor

from lib_generation_scheduler import api, constants
from lib_generation_scheduler.runner import Outcome
from lib_generation_scheduler.service import Scheduler

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from lib_generation_scheduler import routes
except ImportError:  # pragma: no cover
    FastAPI = None

try:
    import PIL.Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.executor = FakeExecutor()
        self.scheduler = Scheduler(self.tmp / "data", self.executor, clock=FakeClock(), poll_seconds=0.05)
        self.addCleanup(self.scheduler.store.close)
        self.addCleanup(self.scheduler.stop)
        self.api = api.QueueApi(self.scheduler)

    def add(self, prompt="p"):
        return self.scheduler.enqueue("txt2img", ["", prompt], roles={"prompt": 1}).job


class QueueApiTests(ApiTestCase):
    def test_state(self):
        self.add("a")
        status, body = self.api.state()
        self.assertEqual(status, 200)
        self.assertEqual(body["pending"][0]["summary"]["prompt"], "a")

    def test_pause_resume(self):
        self.assertEqual(self.api.pause(), (200, {"paused": True}))
        self.assertTrue(self.scheduler.runner.paused)
        self.assertEqual(self.api.resume(), (200, {"paused": False}))
        self.assertFalse(self.scheduler.runner.paused)

    def test_clear_validates_scope(self):
        self.add(), self.add()
        self.assertEqual(self.api.clear("bogus")[0], 400)
        self.assertEqual(self.api.clear(None)[0], 400)
        self.assertEqual(self.api.clear("pending"), (200, {"removed": 2}))

    def test_remove(self):
        job = self.add()
        self.assertEqual(self.api.remove(job.id), (200, {"removed": 1}))
        self.assertEqual(self.api.remove(job.id)[0], 404)

    def test_remove_running_conflicts(self):
        self.executor.gate = threading.Event()
        job = self.add()
        self.scheduler.start(autostart=True)
        self.assertTrue(self.executor.started.wait(2))
        status, body = self.api.remove(job.id)
        self.assertEqual(status, 409)
        self.assertIn("interrupt", body["error"])
        self.assertEqual(self.api.interrupt(), (200, {"interrupted": True}))
        self.scheduler.runner.wait_idle()

    def test_interrupt_when_idle(self):
        self.assertEqual(self.api.interrupt(), (200, {"interrupted": False}))

    def test_move(self):
        a, b = self.add("a"), self.add("b")
        self.assertEqual(self.api.move(b.id, "top"), (200, {"moved": True}))
        self.assertEqual(self.api.move(b.id, "top"), (200, {"moved": False}))
        self.assertEqual(self.api.move(a.id, "sideways")[0], 400)
        self.assertEqual(self.api.move(99, "up")[0], 404)

    def test_move_non_pending_conflicts(self):
        job = self.add()
        self.scheduler.start(autostart=True)
        self.scheduler.runner.wait_idle()
        self.assertEqual(self.api.move(job.id, "up")[0], 409)

    def test_requeue(self):
        job = self.add()
        status, body = self.api.requeue(job.id)
        self.assertEqual(status, 200)
        self.assertEqual(body["job"]["status"], constants.PENDING)
        self.assertNotEqual(body["job"]["id"], job.id)
        self.assertEqual(self.api.requeue(999)[0], 404)

    def test_output_file_serves_only_recorded_outputs(self):
        image = self.tmp / "o.png"
        image.write_bytes(b"not really a png")
        self.executor.outcomes = [Outcome(constants.DONE, result={"outputs": [str(image)]})]
        job = self.add()
        self.scheduler.start(autostart=True)
        self.scheduler.runner.wait_idle()
        self.assertEqual(self.api.output_file(job.id, 0, thumbnail=False), (200, image))
        self.assertEqual(self.api.output_file(job.id, 1, thumbnail=False)[0], 404)
        self.assertEqual(self.api.output_file(999, 0, thumbnail=False)[0], 404)

    def test_thumbnail_falls_back_to_original_when_it_cannot_decode(self):
        image = self.tmp / "o.png"
        image.write_bytes(b"not really a png")
        self.executor.outcomes = [Outcome(constants.DONE, result={"outputs": [str(image)]})]
        job = self.add()
        self.scheduler.start(autostart=True)
        self.scheduler.runner.wait_idle()
        self.assertEqual(self.api.output_file(job.id, 0, thumbnail=True), (200, image))

    @unittest.skipIf(PILImage is None, "Pillow not installed")
    def test_thumbnail_is_small_cached_and_removed_with_the_job(self):
        image = self.tmp / "big.png"
        PILImage.new("RGB", (1024, 512), (200, 10, 10)).save(image)
        self.executor.outcomes = [Outcome(constants.DONE, result={"outputs": [str(image)]})]
        job = self.add()
        self.scheduler.start(autostart=True)
        self.scheduler.runner.wait_idle()

        status, thumb = self.api.output_file(job.id, 0, thumbnail=True)
        self.assertEqual(status, 200)
        self.assertNotEqual(thumb, image)
        with PILImage.open(thumb) as opened:
            self.assertEqual(opened.size, (256, 128))
        mtime = thumb.stat().st_mtime_ns
        self.assertEqual(self.api.output_file(job.id, 0, thumbnail=True)[1], thumb)
        self.assertEqual(thumb.stat().st_mtime_ns, mtime)  # served from cache

        self.scheduler.remove(job.id)
        self.assertFalse(thumb.exists())
        self.assertTrue(image.exists())  # the user's generated image is never touched


@unittest.skipIf(FastAPI is None, "fastapi not installed")
class RoutesTests(ApiTestCase):
    HEADERS = {"X-Gsched": "1"}

    def setUp(self):
        super().setUp()
        self.assertEqual(routes.CSRF_HEADER, "X-Gsched")
        app = FastAPI()
        routes.register_routes(app, lambda: self.scheduler)
        self.client = TestClient(app)

    def url(self, path):
        return f"{constants.API_PREFIX}{path}"

    def test_state_needs_no_header(self):
        self.add("a")
        response = self.client.get(self.url("/state"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pending"][0]["summary"]["prompt"], "a")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_mutations_are_refused_without_the_header(self):
        job = self.add()
        for method, path, body in [
            ("post", "/queue/pause", None),
            ("post", "/queue/resume", None),
            ("post", "/queue/clear", {"scope": "all"}),
            ("post", "/interrupt", None),
            ("post", f"/jobs/{job.id}/move", {"to": "top"}),
            ("post", f"/jobs/{job.id}/requeue", None),
            ("delete", f"/jobs/{job.id}", None),
        ]:
            with self.subTest(path=path):
                response = self.client.request(method.upper(), self.url(path), json=body)
                self.assertEqual(response.status_code, 403)
        self.assertFalse(self.scheduler.runner.paused)
        self.assertEqual(len(self.scheduler.store.pending()), 1)

    def test_pause_and_resume(self):
        self.assertEqual(self.client.post(self.url("/queue/pause"), headers=self.HEADERS).json(), {"paused": True})
        self.assertTrue(self.scheduler.runner.paused)
        self.assertEqual(self.client.post(self.url("/queue/resume"), headers=self.HEADERS).json(), {"paused": False})

    def test_clear_reads_scope_from_json_body(self):
        self.add(), self.add()
        bad = self.client.post(self.url("/queue/clear"), headers=self.HEADERS)
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(self.url("/queue/clear"), headers=self.HEADERS, json={"scope": "pending"})
        self.assertEqual(ok.json(), {"removed": 2})

    def test_delete_and_missing(self):
        job = self.add()
        self.assertEqual(self.client.delete(self.url(f"/jobs/{job.id}"), headers=self.HEADERS).status_code, 200)
        self.assertEqual(self.client.delete(self.url(f"/jobs/{job.id}"), headers=self.HEADERS).status_code, 404)

    def test_move(self):
        a, b = self.add("a"), self.add("b")
        response = self.client.post(self.url(f"/jobs/{b.id}/move"), headers=self.HEADERS, json={"to": "top"})
        self.assertEqual(response.json(), {"moved": True})
        self.assertEqual([j.id for j in self.scheduler.store.pending()], [b.id, a.id])
        bad = self.client.post(self.url(f"/jobs/{b.id}/move"), headers=self.HEADERS, json={"to": "nope"})
        self.assertEqual(bad.status_code, 400)

    def test_requeue(self):
        job = self.add()
        response = self.client.post(self.url(f"/jobs/{job.id}/requeue"), headers=self.HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.scheduler.store.pending()), 2)

    def test_outputs_are_served_as_files(self):
        image = self.tmp / "o.png"
        image.write_bytes(b"PNGDATA")
        self.executor.outcomes = [Outcome(constants.DONE, result={"outputs": [str(image)]})]
        job = self.add()
        self.scheduler.start(autostart=True)
        self.scheduler.runner.wait_idle()
        response = self.client.get(self.url(f"/jobs/{job.id}/outputs/0"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"PNGDATA")
        self.assertEqual(self.client.get(self.url(f"/jobs/{job.id}/outputs/5")).status_code, 404)
        self.assertEqual(self.client.get(self.url(f"/jobs/{job.id}/outputs/0/thumbnail")).status_code, 200)

    def test_output_index_is_not_a_path(self):
        response = self.client.get(self.url("/jobs/1/outputs/..%2f..%2fetc%2fpasswd"))
        self.assertIn(response.status_code, (404, 422))
        self.assertNotIn(b"root:", response.content)


if __name__ == "__main__":
    unittest.main()
