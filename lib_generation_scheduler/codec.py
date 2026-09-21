"""Turn the Gradio argument list of a Generate click into JSON + sidecar files and back.

Plain values (numbers, strings, lists, dicts) go straight into the JSON. Anything
that cannot live in JSON — PIL images, NumPy arrays, raw bytes, uploaded files — is
written next to the job under ``directory`` and referenced by a tagged object. Values
we cannot faithfully round-trip raise :class:`UnserializableArgument` so the caller can
refuse to queue instead of silently corrupting a job.

Dataclass instances (ControlNet's ``ControlNetUnit``) and ``Enum`` members are stored by
class path plus their fields and re-created on restore. Only dataclasses and Enums are ever
re-created that way, and only by importing the named class and assigning fields — no
``__init__``, ``__reduce__`` or other code from the stored data is executed.

PIL and NumPy are imported lazily and only when a value of that kind shows up; the
module works (and is tested) without either installed.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

TAG = "__gsched__"


class UnserializableArgument(ValueError):
    def __init__(self, path: str, value: Any) -> None:
        super().__init__(f"Cannot queue argument {path}: unsupported type {type(value).__name__}")
        self.path = path
        self.value = value


class FileRef:
    """Stand-in for an uploaded-file object: exposes ``.name`` like Gradio's wrapper."""

    def __init__(self, name: str, orig_name: str | None = None) -> None:
        self.name = name
        self.orig_name = orig_name or Path(name).name

    def __repr__(self) -> str:
        return f"FileRef({self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, FileRef) and other.name == self.name

    def __hash__(self) -> int:
        return hash(self.name)


def _pil_image_class():
    pil = sys.modules.get("PIL.Image")
    if pil is None:
        try:
            import PIL.Image as pil  # noqa: PLC0415
        except ImportError:
            return None
    return pil.Image


def _numpy():
    try:
        import numpy  # noqa: PLC0415
    except ImportError:
        return None
    return numpy


def _class_path(cls: type) -> str:
    if "<locals>" in cls.__qualname__:
        raise TypeError(f"{cls.__qualname__} is defined inside a function and cannot be restored")
    return f"{cls.__module__}:{cls.__qualname__}"


def _resolve_class(path: str) -> type:
    module_name, _, qualname = path.partition(":")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        target = getattr(target, part)
    if not isinstance(target, type):
        raise ValueError(f"{path} is not a class")
    return target


class _Encoder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.counter = 0
        self.pil_image = _pil_image_class()
        self.np = _numpy()

    def _next_file(self, suffix: str) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.counter += 1
        return self.directory / f"arg{self.counter:03d}{suffix}"

    def encode(self, value: Any, path: str) -> Any:
        # Enum first: IntEnum / StrEnum members are also ints / strs.
        if isinstance(value, enum.Enum):
            try:
                return {TAG: "enum", "class": _class_path(type(value)), "name": value.name}
            except TypeError as exc:
                raise UnserializableArgument(path, value) from exc

        if value is None or isinstance(value, (bool, int, float, str)):
            return value

        if isinstance(value, dict):
            if TAG in value:
                raise UnserializableArgument(path, value)
            out = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise UnserializableArgument(f"{path}[{key!r}]", key)
                out[key] = self.encode(item, f"{path}[{key!r}]")
            return out

        if isinstance(value, list):
            return [self.encode(item, f"{path}[{i}]") for i, item in enumerate(value)]

        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            try:
                class_path = _class_path(type(value))
            except TypeError as exc:
                raise UnserializableArgument(path, value) from exc
            fields = {
                field.name: self.encode(getattr(value, field.name), f"{path}.{field.name}")
                for field in dataclasses.fields(value)
            }
            return {TAG: "dataclass", "class": class_path, "fields": fields}

        if isinstance(value, tuple):
            return {TAG: "tuple", "items": [self.encode(v, f"{path}[{i}]") for i, v in enumerate(value)]}

        if self.pil_image is not None and isinstance(value, self.pil_image):
            target = self._next_file(".png")
            value.save(target, format="PNG")
            return {TAG: "image", "file": target.name}

        if self.np is not None:
            if isinstance(value, self.np.ndarray):
                if value.dtype == object:
                    raise UnserializableArgument(path, value)
                target = self._next_file(".npy")
                self.np.save(target, value, allow_pickle=False)
                return {TAG: "ndarray", "file": target.name}
            if isinstance(value, self.np.generic):
                return self.encode(value.item(), path)

        if isinstance(value, (bytes, bytearray)):
            target = self._next_file(".bin")
            target.write_bytes(bytes(value))
            return {TAG: "bytes", "file": target.name}

        name = getattr(value, "name", None)
        if isinstance(name, str) and Path(name).is_file():
            target = self._next_file(Path(name).suffix or ".bin")
            shutil.copyfile(name, target)
            return {TAG: "file", "file": target.name, "orig_name": Path(name).name}

        raise UnserializableArgument(path, value)


def encode_args(args: list[Any] | tuple[Any, ...], directory: str | Path) -> str:
    """Serialize ``args``; side files (images, arrays, uploads) are written to ``directory``."""
    encoder = _Encoder(Path(directory))
    encoded = [encoder.encode(arg, f"args[{i}]") for i, arg in enumerate(args)]
    return json.dumps(encoded)


def _decode(value: Any, directory: Path) -> Any:
    if isinstance(value, list):
        return [_decode(item, directory) for item in value]
    if not isinstance(value, dict):
        return value

    kind = value.get(TAG)
    if kind is None:
        return {key: _decode(item, directory) for key, item in value.items()}

    if kind == "tuple":
        return tuple(_decode(item, directory) for item in value["items"])
    if kind == "enum":
        cls = _resolve_class(value["class"])
        if not issubclass(cls, enum.Enum):
            raise ValueError(f"{value['class']} is not an Enum")
        return cls[value["name"]]
    if kind == "dataclass":
        cls = _resolve_class(value["class"])
        if not dataclasses.is_dataclass(cls):
            raise ValueError(f"{value['class']} is not a dataclass")
        instance = object.__new__(cls)
        for name, item in value["fields"].items():
            object.__setattr__(instance, name, _decode(item, directory))
        return instance

    target = directory / value["file"]
    if kind == "image":
        image_class = _pil_image_class()
        if image_class is None:
            raise RuntimeError("Pillow is required to restore queued images")
        import PIL.Image  # noqa: PLC0415

        with PIL.Image.open(target) as opened:
            opened.load()
            return opened.copy()
    if kind == "ndarray":
        np = _numpy()
        if np is None:
            raise RuntimeError("NumPy is required to restore queued arrays")
        return np.load(target, allow_pickle=False)
    if kind == "bytes":
        return target.read_bytes()
    if kind == "file":
        return FileRef(str(target), value.get("orig_name"))
    raise ValueError(f"Unknown encoded argument kind: {kind!r}")


def decode_args(args_json: str, directory: str | Path) -> list[Any]:
    """Inverse of :func:`encode_args`."""
    return [_decode(item, Path(directory)) for item in json.loads(args_json)]


def copy_inputs(source: str | Path | None, target: str | Path) -> Path | None:
    """Duplicate a job's side files for a re-queued copy. ``None`` when there were none."""
    if source is None or not Path(source).is_dir():
        return None
    shutil.copytree(source, target)
    return Path(target)
