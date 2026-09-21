"""Import scripts/generation_scheduler.py against a stubbed WebUI and check what it registers."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from unittest import mock

from tests.helpers import EXTENSION_ROOT

try:
    import gradio  # noqa: F401
    import fastapi  # noqa: F401
except ImportError:  # pragma: no cover
    gradio = None


@unittest.skipIf(gradio is None, "gradio / fastapi not installed")
class EntryScriptTests(unittest.TestCase):
    def setUp(self):
        self.callbacks: dict[str, list] = {}
        script_callbacks = types.ModuleType("modules.script_callbacks")
        for name in ("on_ui_settings", "on_before_ui", "on_after_component", "on_ui_tabs", "on_app_started"):
            setattr(script_callbacks, name, lambda fn, _n=name: self.callbacks.setdefault(_n, []).append(fn))
        shared = types.ModuleType("modules.shared")
        shared.opts = types.SimpleNamespace(data={})
        modules = types.ModuleType("modules")
        modules.script_callbacks = script_callbacks
        modules.shared = shared
        self.enterContext(mock.patch.dict(sys.modules, {
            "modules": modules, "modules.script_callbacks": script_callbacks, "modules.shared": shared,
        }))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        spec = importlib.util.spec_from_file_location(
            "gsched_entry_under_test", EXTENSION_ROOT / "scripts" / "generation_scheduler.py"
        )
        self.entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.entry)

    def test_registers_every_callback_once(self):
        self.assertEqual(
            {name: len(fns) for name, fns in self.callbacks.items()},
            {"on_ui_settings": 1, "on_before_ui": 1, "on_after_component": 1, "on_ui_tabs": 1, "on_app_started": 1},
        )

    def test_scheduler_is_a_lazy_singleton_using_the_configured_data_dir(self):
        shared = sys.modules["modules.shared"]
        shared.opts.gsched_data_dir = self.tmp.name
        first = self.entry.get_scheduler()
        self.addCleanup(first.store.close)
        self.assertIs(self.entry.get_scheduler(), first)
        self.assertEqual(str(first.data_dir), self.tmp.name)

    def test_ui_tabs_callback_returns_the_queue_tab_and_connects_the_buttons(self):
        with mock.patch.object(self.entry.wiring, "connect_all") as connect:
            (tab,) = self.entry._on_ui_tabs()
        connect.assert_called_once_with()
        blocks, label, elem_id = tab
        self.assertEqual((label, elem_id), ("Queue", "gsched_queue"))
        self.assertTrue(any(getattr(b, "elem_id", None) == "gsched_root_host" for b in blocks.blocks.values()))

    def test_app_started_registers_routes_and_starts_the_worker_once(self):
        from fastapi import FastAPI

        sys.modules["modules.shared"].opts.gsched_data_dir = self.tmp.name
        app = FastAPI()
        scheduler = self.entry.get_scheduler()
        self.addCleanup(scheduler.store.close)
        self.addCleanup(scheduler.stop)
        self.entry._on_app_started(None, app)
        self.entry._on_app_started(None, FastAPI())  # a UI reload builds a new app
        paths = {route.path for route in app.routes}
        self.assertIn("/gsched/v1/state", paths)
        self.assertIn("/gsched/v1/jobs/{job_id}/move", paths)
        self.assertTrue(scheduler.runner._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
