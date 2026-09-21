"""Run the Node tests for javascript/gsched_core.js when ``node`` is available."""

from __future__ import annotations

import shutil
import subprocess
import unittest

from tests.helpers import EXTENSION_ROOT

JS_TEST = EXTENSION_ROOT / "tests" / "js" / "test_gsched_core.mjs"


class JsCoreTests(unittest.TestCase):
    def test_gsched_core_js(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        result = subprocess.run(
            [node, str(JS_TEST)],
            cwd=EXTENSION_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            self.fail(f"JS core tests failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
