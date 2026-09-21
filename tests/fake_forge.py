"""Just enough of Forge Neo's ``modules`` / ``modules_forge`` to drive the executor."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock


class FakeOpts:
    def __init__(self, **values):
        self.data = dict(values)
        self.set_calls: list[tuple[str, object]] = []

    def __getattr__(self, name):
        try:
            return self.__dict__["data"][name]
        except KeyError:
            raise AttributeError(name) from None

    def set(self, name, value):
        self.set_calls.append((name, value))
        self.data[name] = value


class FakeState:
    def __init__(self):
        self.interrupted = False
        self.skipped = False
        self.stopping_generation = False
        self.sampling_step = 0
        self.sampling_steps = 0
        self.job_no = 0
        self.job_count = 0
        self.job = ""
        self.began: list[str] = []
        self.ended = 0

    def begin(self, job=None):
        self.began.append(job)
        self.interrupted = False

    def end(self):
        self.ended += 1

    def interrupt(self):
        self.interrupted = True


class FakeForge:
    """Holds the fake modules and records what the executor did to them."""

    def __init__(self, **opts):
        import threading

        self.opts = FakeOpts(
            sd_model_checkpoint="current.safetensors [aaaa]",
            forge_additional_modules=["/m/current_vae.safetensors"],
            forge_unet_storage_dtype="Automatic",
            CLIP_stop_at_last_layers=2,
            **opts,
        )
        self.state = FakeState()
        self.calls: list[tuple] = []          # entry-point invocations
        self.events: list[str] = []           # ordered side effects
        self.lock = threading.Lock()
        self.next_result = None               # what the entry point returns
        self.raises: BaseException | None = None
        self.last_exception = None
        self.interrupt_during_run = False
        self.qlock_held_during_run = None

        self.shared = types.ModuleType("modules.shared")
        self.shared.opts = self.opts
        self.shared.state = self.state

        self.progress = types.ModuleType("modules.progress")
        self.progress.create_task_id = lambda kind: f"task({kind}-TEST)"

        self.call_queue = types.ModuleType("modules.call_queue")
        self.call_queue.queue_lock = self.lock
        self.call_queue.wrap_gradio_gpu_call = self._wrap_gpu_call

        self.txt2img = types.ModuleType("modules.txt2img")
        self.txt2img.txt2img = self._entry("txt2img")
        self.img2img = types.ModuleType("modules.img2img")
        self.img2img.img2img = self._entry("img2img")

        self.main_entry = types.ModuleType("modules_forge.main_entry")
        self.main_entry.checkpoint_change = self._checkpoint_change
        self.main_entry.modules_change = self._modules_change
        self.main_entry.dtype_change = self._dtype_change
        self.main_thread = types.ModuleType("modules_forge.main_thread")
        self.main_thread.last_exception = None

        self.modules = types.ModuleType("modules")
        self.modules_forge = types.ModuleType("modules_forge")
        for name in ("shared", "progress", "call_queue", "txt2img", "img2img"):
            setattr(self.modules, name, getattr(self, name))
        self.modules_forge.main_entry = self.main_entry
        self.modules_forge.main_thread = self.main_thread

    # -- Forge behaviour -------------------------------------------------

    def _wrap_gpu_call(self, func, extra_outputs=None):
        """Same shape as modules.call_queue.wrap_gradio_gpu_call + wrap_gradio_call_no_job."""

        def f(*args, **kwargs):
            assert args and args[0].startswith("task("), "first arg must be a task id"
            with self.lock:
                self.state.begin(job=args[0])
                try:
                    res = func(*args, **kwargs)
                finally:
                    self.state.skipped = False
                    self.state.interrupted = False
                    self.state.stopping_generation = False
                self.state.end()
            return res

        def safe(*args, **kwargs):
            try:
                return tuple(f(*args, **kwargs))
            except Exception as exc:  # noqa: BLE001 - mirrors Forge turning errors into HTML
                self.events.append(f"wrapper swallowed {type(exc).__name__}")
                return (None, None, "", f"<div class='error'>{exc}</div>")

        return safe

    def _entry(self, kind):
        def entry(id_task, request, *args):
            self.calls.append((kind, id_task, request, args))
            self.events.append(f"run {kind}")
            if self.interrupt_during_run:
                self.state.interrupt()
            if self.raises is not None:
                raise self.raises
            self.main_thread.last_exception = self.last_exception
            return self.next_result

        return entry

    def _checkpoint_change(self, name, preset, save=True, refresh=True):
        assert preset is None and save is False and refresh is True
        self.events.append(f"checkpoint {name}")
        changed = name != self.opts.data["sd_model_checkpoint"]
        self.opts.data["sd_model_checkpoint"] = name
        return changed

    def _modules_change(self, values, preset, save=True, refresh=True):
        assert preset is None and save is False and refresh is True
        self.events.append(f"modules {values}")
        self.opts.data["forge_additional_modules"] = list(values)
        return True

    def _dtype_change(self, dtype, preset, save=True, refresh=True):
        assert preset is None and save is False and refresh is True
        self.events.append(f"dtype {dtype}")
        self.opts.data["forge_unet_storage_dtype"] = dtype
        return True

    # -- installation ----------------------------------------------------

    @contextmanager
    def installed(self):
        patched = {
            "modules": self.modules,
            "modules.shared": self.shared,
            "modules.progress": self.progress,
            "modules.call_queue": self.call_queue,
            "modules.txt2img": self.txt2img,
            "modules.img2img": self.img2img,
            "modules_forge": self.modules_forge,
            "modules_forge.main_entry": self.main_entry,
            "modules_forge.main_thread": self.main_thread,
        }
        with mock.patch.dict(sys.modules, patched):
            yield self


def make_gallery_result(paths, infotexts=None, seed=123):
    """A txt2img-style return tuple whose images claim to be saved at ``paths``."""
    import json

    images = [SimpleNamespace(already_saved_as=str(p)) for p in paths]
    info = {"infotexts": infotexts or ["Steps: 20"], "seed": seed}
    return ({"value": images, "visible": True, "__type__": "update"}, None, json.dumps(info), "", "")
