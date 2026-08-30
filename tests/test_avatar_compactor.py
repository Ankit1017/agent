"""Regression tests for the browser-safe GLB morph compactor."""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path
from types import ModuleType


def _compactor() -> ModuleType:
    """Load the repository-owned conversion script without executing its CLI."""
    path = Path(__file__).parents[1] / "scripts" / "compact-glb-morphs.py"
    specification = importlib.util.spec_from_file_location("avatar_compactor", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_sparse_fixture(path: Path) -> None:
    """Write one GLB whose morph accessor already uses sparse buffer views."""
    positions = struct.pack("<9f", *(0.0 for _ in range(9)))
    indices = struct.pack("<3B", 0, 1, 2)
    values = struct.pack(
        "<9f",
        0.00001,
        0.0,
        0.0,
        0.1,
        0.0,
        0.0,
        0.0,
        0.2,
        0.0,
    )
    binary = positions + indices + b"\0" + values
    document = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {
                "buffer": 0,
                "byteOffset": len(positions),
                "byteLength": len(indices),
            },
            {
                "buffer": 0,
                "byteOffset": len(positions) + len(indices) + 1,
                "byteLength": len(values),
            },
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "sparse": {
                    "count": 3,
                    "indices": {"bufferView": 1, "componentType": 5121},
                    "values": {"bufferView": 2},
                },
            },
        ],
        "meshes": [
            {
                "extras": {"targetNames": ["jawOpen"]},
                "primitives": [{"attributes": {"POSITION": 0}, "targets": [{"POSITION": 1}]}],
            }
        ],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def test_compactor_retains_and_remaps_existing_sparse_views(tmp_path: Path) -> None:
    """Existing sparse morphs remain loadable after insignificant deltas are removed."""
    source = tmp_path / "source.glb"
    destination = tmp_path / "compact.glb"
    _write_sparse_fixture(source)

    converted, _ = _compactor().compact(source, destination)

    content = destination.read_bytes()
    json_length = struct.unpack_from("<I", content, 12)[0]
    document = json.loads(content[20 : 20 + json_length])
    sparse = document["accessors"][1]["sparse"]
    assert converted == 1
    assert sparse["count"] == 2
    assert sparse["indices"]["bufferView"] < len(document["bufferViews"])
    assert sparse["values"]["bufferView"] < len(document["bufferViews"])
