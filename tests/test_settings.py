from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import EXTENSION_ROOT

from lib_generation_scheduler import constants, settings


class DefaultsTests(unittest.TestCase):
    def test_every_option_key_has_a_default(self):
        keys = {v for k, v in vars(constants).items() if k.startswith("OPT_")}
        self.assertEqual(set(settings.OPT_DEFAULTS), keys)

    def test_safe_opt_without_webui_returns_defaults(self):
        with mock.patch.dict(sys.modules, {"modules": None}):
            self.assertEqual(settings.safe_opt(constants.OPT_HISTORY_LIMIT), 100)
            self.assertFalse(settings.safe_opt(constants.OPT_AUTOSTART_ON_LAUNCH))

    def test_safe_opt_reads_webui_options_and_falls_back_per_key(self):
        shared = mock.MagicMock()
        shared.opts = mock.MagicMock(spec_set=["gsched_history_limit"])
        shared.opts.gsched_history_limit = 7
        modules = mock.MagicMock(shared=shared)
        with mock.patch.dict(sys.modules, {"modules": modules, "modules.shared": shared}):
            self.assertEqual(settings.safe_opt(constants.OPT_HISTORY_LIMIT), 7)
            self.assertTrue(settings.safe_opt(constants.OPT_PAUSE_ON_INTERRUPT))  # not set → default

    def test_history_limit_is_sanitised(self):
        for raw, expected in ((5, 5), ("12", 12), (-3, 0), ("junk", 100), (None, 100)):
            with self.subTest(raw=raw), mock.patch.object(settings, "safe_opt", return_value=raw):
                self.assertEqual(settings.history_limit(), expected)


class DataDirTests(unittest.TestCase):
    def test_default_is_inside_the_extension(self):
        self.assertEqual(settings.resolve_data_dir("", EXTENSION_ROOT), EXTENSION_ROOT / "data")
        self.assertEqual(settings.resolve_data_dir(None, EXTENSION_ROOT), EXTENSION_ROOT / "data")
        self.assertEqual(settings.resolve_data_dir("   ", EXTENSION_ROOT), EXTENSION_ROOT / "data")

    def test_absolute_and_relative_paths(self):
        root = Path("/opt/ext")
        self.assertEqual(settings.resolve_data_dir("/var/queue", root), Path("/var/queue"))
        self.assertEqual(settings.resolve_data_dir("state", root), root / "state")

    def test_home_is_expanded(self):
        self.assertEqual(settings.resolve_data_dir("~/q", Path("/x")), Path.home() / "q")

    def test_option_registration(self):
        added = {}
        shared = mock.MagicMock()
        shared.opts.add_option.side_effect = lambda key, info: added.__setitem__(key, info)
        shared.OptionInfo.side_effect = lambda default, label, **kw: mock.MagicMock(
            default=default, label=label, section=kw["section"], info=lambda text: mock.MagicMock(
                default=default, label=label, section=kw["section"], help=text))
        with mock.patch.dict(sys.modules, {"modules": mock.MagicMock(shared=shared), "modules.shared": shared}):
            settings.on_ui_settings()
        self.assertEqual(set(added), set(settings.OPT_DEFAULTS))
        for key, option in added.items():
            self.assertEqual(option.default, settings.OPT_DEFAULTS[key])
            self.assertEqual(option.section, constants.SECTION)


if __name__ == "__main__":
    unittest.main()
