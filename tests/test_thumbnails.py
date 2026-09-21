from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import EXTENSION_ROOT  # noqa: F401

from lib_generation_scheduler import thumbnails

try:
    import PIL.Image as PILImage
except ImportError:  # pragma: no cover
    PILImage = None


@unittest.skipIf(PILImage is None, "Pillow not installed")
class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.source = self.tmp / "src.png"
        PILImage.new("RGBA", (800, 400), (5, 6, 7, 128)).save(self.source)

    def test_creates_a_bounded_jpeg(self):
        thumb = thumbnails.thumbnail_for(self.source, self.tmp / "cache", "1-0")
        self.assertEqual(thumb, self.tmp / "cache" / "1-0-384.jpg")
        with PILImage.open(thumb) as image:
            self.assertEqual(image.size, (384, 192))
            self.assertEqual(image.format, "JPEG")

    def test_small_images_are_not_upscaled(self):
        small = self.tmp / "small.png"
        PILImage.new("RGB", (40, 30)).save(small)
        with PILImage.open(thumbnails.thumbnail_for(small, self.tmp / "c", "s")) as image:
            self.assertEqual(image.size, (40, 30))

    def test_cache_is_reused_until_the_source_changes(self):
        first = thumbnails.thumbnail_for(self.source, self.tmp / "c", "x")
        stamp = first.stat().st_mtime_ns
        self.assertEqual(thumbnails.thumbnail_for(self.source, self.tmp / "c", "x"), first)
        self.assertEqual(first.stat().st_mtime_ns, stamp)
        os.utime(self.source, (first.stat().st_mtime + 10, first.stat().st_mtime + 10))
        thumbnails.thumbnail_for(self.source, self.tmp / "c", "x")
        self.assertGreater(first.stat().st_mtime_ns, stamp)

    def test_undecodable_source_falls_back_to_the_original(self):
        broken = self.tmp / "broken.png"
        broken.write_bytes(b"nope")
        self.assertEqual(thumbnails.thumbnail_for(broken, self.tmp / "c", "b"), broken)


if __name__ == "__main__":
    unittest.main()
