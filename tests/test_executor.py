from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.fake_forge import FakeForge, make_gallery_result
from tests.helpers import EXTENSION_ROOT  # noqa: F401

from lib_generation_scheduler import constants, executor
from lib_generation_scheduler.store import Job


def make_job(kind="txt2img", context=None, user=None) -> Job:
    return Job(id=1, kind=kind, status=constants.RUNNING, position=1, context=context or {}, user=user)


class ExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self.forge = FakeForge()
        self.enterContext(self.forge.installed())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.executor = executor.ForgeExecutor()

    def image(self, name="out.png") -> Path:
        path = self.tmp / name
        path.write_bytes(b"png")
        return path


class RunTests(ExecutorTestCase):
    def test_successful_run_calls_entry_point_like_generate(self):
        out = self.image()
        self.forge.next_result = make_gallery_result([out], ["Steps: 20, Seed: 5"], seed=5)
        outcome = self.executor.run(make_job(user="bo"), ["ignored-id-slot", "a cat", 512])

        (kind, id_task, request, args), = self.forge.calls
        self.assertEqual(kind, "txt2img")
        self.assertRegex(id_task, r"^task\(txt2img-")
        self.assertEqual(args, ("a cat", 512))  # the id slot is replaced, not forwarded
        self.assertEqual(request.username, "bo")
        self.assertEqual(outcome.status, constants.DONE)
        self.assertEqual(outcome.result["outputs"], [str(out)])
        self.assertEqual(outcome.result["infotext"], "Steps: 20, Seed: 5")
        self.assertEqual(outcome.result["seed"], 5)
        self.assertIn("elapsed", outcome.result)

    def test_goes_through_the_queue_lock_wrapper(self):
        self.forge.next_result = make_gallery_result([])
        self.executor.run(make_job(), ["", "p"])
        self.assertEqual(self.forge.state.began, [self.forge.calls[0][1]])
        self.assertEqual(self.forge.state.ended, 1)

    def test_img2img_uses_its_own_entry_point(self):
        self.forge.next_result = make_gallery_result([])
        self.executor.run(make_job("img2img"), ["", 0, "p"])
        self.assertEqual(self.forge.calls[0][0], "img2img")
        self.assertEqual(self.forge.calls[0][3], (0, "p"))

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            self.executor.run(make_job("extras"), [""])

    def test_exception_is_reported_as_failure(self):
        self.forge.raises = RuntimeError("CUDA out of memory")
        outcome = self.executor.run(make_job(), ["", "p"])
        self.assertEqual(outcome.status, constants.FAILED)
        self.assertEqual(outcome.error, "RuntimeError: CUDA out of memory")

    def test_none_result_uses_main_thread_error(self):
        self.forge.next_result = None
        self.forge.last_exception = "OOM"
        outcome = self.executor.run(make_job(), ["", "p"])
        self.assertEqual(outcome.status, constants.FAILED)
        self.assertEqual(outcome.error, "OOM")

    def test_none_result_without_details(self):
        self.forge.next_result = None
        outcome = self.executor.run(make_job(), ["", "p"])
        self.assertEqual(outcome.error, "Generation returned no result.")

    def test_interrupt_is_detected_before_the_wrapper_clears_it(self):
        self.forge.next_result = make_gallery_result([])
        self.forge.interrupt_during_run = True
        outcome = self.executor.run(make_job(), ["", "p"])
        self.assertEqual(outcome.status, constants.INTERRUPTED)
        self.assertFalse(self.forge.state.interrupted)  # the wrapper cleaned up as usual

    def test_interrupt_and_progress_delegate_to_shared_state(self):
        self.forge.state.sampling_step, self.forge.state.sampling_steps = 4, 20
        self.forge.state.job_no, self.forge.state.job_count = 1, 3
        self.assertEqual(self.executor.progress(),
                         {"step": 4, "steps": 20, "job_no": 1, "job_count": 3})
        self.executor.interrupt()
        self.assertTrue(self.forge.state.interrupted)


class ExtractResultTests(unittest.TestCase):
    def test_tolerates_garbage(self):
        self.assertEqual(executor.extract_result(None, 1.234), {"outputs": [], "elapsed": 1.23})
        self.assertEqual(executor.extract_result(("x", None, "not json"), 0)["outputs"], [])

    def test_skips_unsaved_and_missing_files_and_strips_mtime_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "a.png"
            real.write_bytes(b"x")
            from types import SimpleNamespace as NS

            gallery = [NS(already_saved_as=f"{real}?123.4"), NS(already_saved_as=str(real) + ".gone"),
                       NS(), (NS(already_saved_as=str(real)), "caption")]
            result = executor.extract_result(({"value": gallery}, None, "{}"), 0)
        self.assertEqual(result["outputs"], [str(real), str(real)])


class ContextTests(ExecutorTestCase):
    def test_capture_reads_model_state_and_requested_options(self):
        context = executor.capture_context(["CLIP_stop_at_last_layers", "not_an_option"])
        self.assertEqual(context["sd_model_checkpoint"], "current.safetensors [aaaa]")
        self.assertEqual(context["model"], "current.safetensors")
        self.assertEqual(context["forge_additional_modules"], ["/m/current_vae.safetensors"])
        self.assertEqual(context["forge_unet_storage_dtype"], "Automatic")
        self.assertEqual(context["opts"], {"CLIP_stop_at_last_layers": 2})

    def test_capture_without_extras_has_no_opts_key(self):
        self.assertNotIn("opts", executor.capture_context())

    def test_capture_strips_directories_from_model_name(self):
        self.forge.opts.data["sd_model_checkpoint"] = "sub/dir/model.safetensors [1234abcd]"
        self.assertEqual(executor.capture_context()["model"], "model.safetensors")

    def test_capture_with_no_checkpoint(self):
        self.forge.opts.data["sd_model_checkpoint"] = ""
        context = executor.capture_context()
        self.assertNotIn("sd_model_checkpoint", context)
        self.assertNotIn("model", context)

    def test_run_switches_model_around_the_generation_and_restores(self):
        self.forge.next_result = make_gallery_result([])
        context = {
            "sd_model_checkpoint": "queued.safetensors [bbbb]",
            "forge_additional_modules": ["/m/queued_vae.safetensors"],
            "forge_unet_storage_dtype": "fp8",
            "opts": {"CLIP_stop_at_last_layers": 1},
        }
        outcome = self.executor.run(make_job(context=context), ["", "p"])
        self.assertEqual(outcome.status, constants.DONE)
        events = [e for e in self.forge.events if not e.startswith("wrapper")]
        self.assertEqual(events, [
            "checkpoint queued.safetensors [bbbb]",
            "modules ['/m/queued_vae.safetensors']",
            "dtype fp8",
            "run txt2img",
            "dtype Automatic",
            "modules ['/m/current_vae.safetensors']",
            "checkpoint current.safetensors [aaaa]",
        ])
        self.assertEqual(self.forge.opts.data["CLIP_stop_at_last_layers"], 2)
        self.assertEqual(self.forge.opts.set_calls,
                         [("CLIP_stop_at_last_layers", 1), ("CLIP_stop_at_last_layers", 2)])

    def test_unchanged_state_touches_nothing(self):
        self.forge.next_result = make_gallery_result([])
        context = executor.capture_context(["CLIP_stop_at_last_layers"])
        self.executor.run(make_job(context=context), ["", "p"])
        self.assertEqual(self.forge.events, ["run txt2img"])
        self.assertEqual(self.forge.opts.set_calls, [])

    def test_restore_happens_even_when_generation_fails(self):
        self.forge.raises = RuntimeError("boom")
        context = {"sd_model_checkpoint": "queued.safetensors [bbbb]"}
        outcome = self.executor.run(make_job(context=context), ["", "p"])
        self.assertEqual(outcome.status, constants.FAILED)
        self.assertEqual(self.forge.opts.data["sd_model_checkpoint"], "current.safetensors [aaaa]")

    def test_failing_switch_does_not_fail_the_job(self):
        self.forge.next_result = make_gallery_result([])

        def broken(*_a, **_k):
            raise RuntimeError("model vanished")

        self.forge.main_entry.checkpoint_change = broken
        with self.assertLogs("generation_scheduler", "ERROR"):
            outcome = self.executor.run(
                make_job(context={"sd_model_checkpoint": "gone.safetensors"}), ["", "p"]
            )
        self.assertEqual(outcome.status, constants.DONE)

    def test_empty_context_is_a_noop(self):
        self.assertIsNone(executor.apply_context({})())


class ParseOptionNamesTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(executor.parse_option_names(" a, b ,,c\nd "), ["a", "b", "c", "d"])
        self.assertEqual(executor.parse_option_names(""), [])
        self.assertEqual(executor.parse_option_names(None), [])


if __name__ == "__main__":
    unittest.main()
