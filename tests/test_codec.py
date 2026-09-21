from __future__ import annotations

import dataclasses
import enum
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import EXTENSION_ROOT  # noqa: F401  (sets sys.path)

from lib_generation_scheduler import codec

try:
    import PIL.Image as PILImage
except ImportError:  # pragma: no cover - depends on interpreter
    PILImage = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


class Color(enum.Enum):
    RED = "red"
    BLUE = "blue"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


@dataclasses.dataclass
class Unit:
    enabled: bool = True
    module: str = "None"
    weight: float = 1.0
    color: Color = Color.RED
    level: Level = Level.LOW
    image: object = None
    _idx: int = -1


@dataclasses.dataclass(frozen=True)
class Frozen:
    value: int = 0


class CodecTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "job"

    def roundtrip(self, args):
        return codec.decode_args(codec.encode_args(args, self.dir), self.dir)


class ObjectTests(CodecTestCase):
    def test_enum_roundtrip_including_int_enums(self):
        self.assertEqual(self.roundtrip([Color.BLUE, Level.HIGH]), [Color.BLUE, Level.HIGH])
        restored = self.roundtrip([Level.HIGH])[0]
        self.assertIs(restored, Level.HIGH)
        self.assertNotIsInstance(restored, str)

    def test_dataclass_roundtrip_keeps_type_and_fields(self):
        unit = Unit(enabled=False, module="canny", weight=0.7, color=Color.BLUE, level=Level.HIGH, _idx=2)
        (restored,) = self.roundtrip([unit])
        self.assertIsInstance(restored, Unit)
        self.assertEqual(restored, unit)

    def test_frozen_dataclass(self):
        self.assertEqual(self.roundtrip([Frozen(5)]), [Frozen(5)])

    def test_dataclass_inside_containers(self):
        args = [[Unit(module="a"), Unit(module="b")], {"unit": Unit()}]
        self.assertEqual(self.roundtrip(args), args)

    def test_dataclass_holding_an_uploaded_file_stores_it_in_the_job_directory(self):
        source = Path(self._tmp.name) / "mask.png"
        source.write_bytes(b"m")

        class Upload:
            name = str(source)

        (restored,) = self.roundtrip([Unit(image=Upload())])
        source.unlink()
        self.assertEqual(Path(restored.image.name).read_bytes(), b"m")

    def test_dataclass_class_itself_is_not_an_instance(self):
        with self.assertRaises(codec.UnserializableArgument):
            codec.encode_args([Unit], self.dir)

    def test_locally_defined_classes_are_refused(self):
        @dataclasses.dataclass
        class Local:
            x: int = 1

        class LocalEnum(enum.Enum):
            A = 1

        for value in (Local(), LocalEnum.A):
            with self.subTest(value=value), self.assertRaises(codec.UnserializableArgument):
                codec.encode_args([value], self.dir)

    def test_restore_only_recreates_dataclasses_and_enums(self):
        for kind, target in (("dataclass", "os:getcwd"), ("dataclass", "os:path"),
                             ("dataclass", "pathlib:Path"), ("enum", "pathlib:Path")):
            payload = json.dumps([{codec.TAG: kind, "class": target, "fields": {}, "name": "x"}])
            with self.subTest(kind=kind, target=target), self.assertRaises((ValueError, AttributeError)):
                codec.decode_args(payload, self.dir)

    def test_restore_does_not_run_the_class_constructor(self):
        @dataclasses.dataclass
        class Exploding:
            x: int = 0

            def __post_init__(self):
                raise AssertionError("constructor ran")

        Exploding.__qualname__ = "Exploding"
        globals()["Exploding"] = Exploding
        self.addCleanup(globals().pop, "Exploding", None)
        payload = json.dumps([{codec.TAG: "dataclass", "class": f"{__name__}:Exploding", "fields": {"x": 3}}])
        (restored,) = codec.decode_args(payload, self.dir)
        self.assertEqual(restored.x, 3)


class PlainValueTests(CodecTestCase):
    def test_plain_values_roundtrip_without_side_files(self):
        args = ["task", "a cat", "", ["style1"], 1, 2, 6.5, True, None, {"k": [1, 2]}]
        self.assertEqual(self.roundtrip(args), args)
        self.assertFalse(self.dir.exists())

    def test_tuple_type_preserved(self):
        self.assertEqual(self.roundtrip([(1, "a"), [1, "a"]]), [(1, "a"), [1, "a"]])

    def test_nested_tuple(self):
        self.assertEqual(self.roundtrip([{"a": ((1, 2), 3)}]), [{"a": ((1, 2), 3)}])

    def test_unicode_survives(self):
        self.assertEqual(self.roundtrip(["猫 ☕ ñ"]), ["猫 ☕ ñ"])

    def test_bytes_stored_as_file(self):
        result = self.roundtrip([b"\x00\x01binary"])
        self.assertEqual(result, [b"\x00\x01binary"])
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_unknown_object_rejected_with_position(self):
        with self.assertRaises(codec.UnserializableArgument) as ctx:
            codec.encode_args([1, ["ok", object()]], self.dir)
        self.assertEqual(ctx.exception.path, "args[1][1]")

    def test_non_string_dict_key_rejected(self):
        with self.assertRaises(codec.UnserializableArgument):
            codec.encode_args([{1: "x"}], self.dir)

    def test_reserved_tag_in_user_dict_rejected(self):
        with self.assertRaises(codec.UnserializableArgument):
            codec.encode_args([{codec.TAG: "image"}], self.dir)

    def test_uploaded_file_copied_and_restored(self):
        source = Path(self._tmp.name) / "upload.png"
        source.write_bytes(b"data")

        class Upload:
            name = str(source)

        (restored,) = self.roundtrip([Upload()])
        source.unlink()  # the original temp file may vanish; the copy must survive
        self.assertIsInstance(restored, codec.FileRef)
        self.assertEqual(restored.orig_name, "upload.png")
        self.assertEqual(Path(restored.name).read_bytes(), b"data")
        self.assertEqual(Path(restored.name).suffix, ".png")

    def test_object_with_name_that_is_not_a_file_rejected(self):
        class Named:
            name = "/definitely/not/here"

        with self.assertRaises(codec.UnserializableArgument):
            codec.encode_args([Named()], self.dir)

    def test_unknown_tag_on_decode(self):
        payload = json.dumps([{codec.TAG: "mystery", "file": "x"}])
        with self.assertRaises(ValueError):
            codec.decode_args(payload, self.dir)

    def test_copy_inputs(self):
        self.assertIsNone(codec.copy_inputs(None, self.dir / "x"))
        self.assertIsNone(codec.copy_inputs(self.dir / "missing", self.dir / "x"))
        self.dir.mkdir()
        (self.dir / "arg001.bin").write_bytes(b"1")
        target = Path(self._tmp.name) / "copy"
        self.assertEqual(codec.copy_inputs(self.dir, target), target)
        self.assertEqual((target / "arg001.bin").read_bytes(), b"1")


@unittest.skipIf(PILImage is None, "Pillow not installed")
class ImageTests(CodecTestCase):
    def test_rgba_image_roundtrips_pixels(self):
        image = PILImage.new("RGBA", (4, 3), (10, 20, 30, 40))
        image.putpixel((1, 1), (200, 100, 50, 255))
        (restored,) = self.roundtrip([image])
        self.assertEqual(restored.size, (4, 3))
        self.assertEqual(restored.mode, "RGBA")
        self.assertEqual(restored.getpixel((1, 1)), (200, 100, 50, 255))
        self.assertEqual(restored.getpixel((0, 0)), (10, 20, 30, 40))

    def test_multiple_images_get_distinct_files(self):
        a = PILImage.new("RGB", (2, 2), (255, 0, 0))
        b = PILImage.new("L", (2, 2), 7)
        ra, rb = self.roundtrip([a, "x", b])[0::2]
        self.assertEqual(ra.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(rb.getpixel((0, 0)), 7)
        self.assertEqual(len(list(self.dir.iterdir())), 2)

    def test_restored_image_is_detached_from_file(self):
        (restored,) = self.roundtrip([PILImage.new("RGB", (2, 2))])
        for f in self.dir.iterdir():
            f.unlink()
        restored.load()  # would raise if it still needed the file


@unittest.skipIf(np is None, "NumPy not installed")
class ArrayTests(CodecTestCase):
    def test_array_roundtrip(self):
        array = np.arange(12, dtype=np.uint8).reshape(3, 4)
        (restored,) = self.roundtrip([array])
        self.assertTrue((restored == array).all())
        self.assertEqual(restored.dtype, np.uint8)

    def test_array_inside_dict(self):
        array = np.ones((2, 2), dtype=np.float32)
        (restored,) = self.roundtrip([{"image": array, "mask": None}])
        self.assertTrue((restored["image"] == array).all())
        self.assertIsNone(restored["mask"])

    def test_object_array_rejected(self):
        with self.assertRaises(codec.UnserializableArgument):
            codec.encode_args([np.array([object()], dtype=object)], self.dir)

    def test_numpy_scalars_become_python(self):
        result = self.roundtrip([np.float32(1.5), np.int64(3), np.bool_(True)])
        self.assertEqual(result, [1.5, 3, True])
        self.assertIsInstance(result[1], int)


if __name__ == "__main__":
    unittest.main()
