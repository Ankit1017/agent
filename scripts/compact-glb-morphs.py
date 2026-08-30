"""Compact GLB facial morph accessors into bounded sparse accessors."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any, cast

_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942


def _read_glb(path: Path) -> tuple[dict[str, Any], bytes]:
    """Read one strict two-chunk GLB document."""
    content = path.resolve(strict=True).read_bytes()
    if len(content) < 28 or content[:4] != b"glTF":
        raise ValueError("source is not a binary GLB")
    _, version, declared = struct.unpack_from("<4sII", content, 0)
    if version != 2 or declared != len(content):
        raise ValueError("source GLB header is invalid")
    offset = 12
    document: dict[str, Any] | None = None
    binary: bytes | None = None
    while offset < len(content):
        length, kind = struct.unpack_from("<II", content, offset)
        offset += 8
        payload = content[offset : offset + length]
        offset += length
        if kind == _JSON_CHUNK:
            document = cast(dict[str, Any], json.loads(payload.rstrip(b" \t\r\n\0")))
        elif kind == _BIN_CHUNK:
            binary = payload
        else:
            raise ValueError("source GLB contains an unsupported chunk")
    if document is None or binary is None:
        raise ValueError("source GLB is incomplete")
    return document, binary


def _morph_accessors(document: dict[str, Any]) -> set[int]:
    """Return accessor indices referenced by mesh morph targets."""
    return {
        int(accessor)
        for mesh in document.get("meshes", [])
        for primitive in mesh.get("primitives", [])
        for target in primitive.get("targets", [])
        for accessor in target.values()
    }


def compact(source: Path, destination: Path, epsilon: float = 1e-3) -> tuple[int, int]:
    """Write a visually equivalent GLB using sparse facial deltas."""
    if destination.exists():
        raise ValueError("destination already exists; refusing to overwrite it")
    document, old_binary = _read_glb(source)
    accessors = cast(list[dict[str, Any]], document.get("accessors", []))
    old_views = cast(list[dict[str, Any]], document.get("bufferViews", []))
    targets = _morph_accessors(document)
    converted: dict[int, tuple[bytes, bytes, int, int]] = {}
    converted_views: set[int] = set()
    for index in targets:
        accessor = accessors[index]
        view_index = accessor.get("bufferView")
        if (
            not isinstance(view_index, int)
            or accessor.get("componentType") != 5126
            or accessor.get("type") != "VEC3"
            or accessor.get("normalized") is True
        ):
            continue
        view = old_views[view_index]
        count = int(accessor.get("count", 0))
        stride = int(view.get("byteStride", 12))
        start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
        if count <= 0 or stride < 12 or start + (count - 1) * stride + 12 > len(old_binary):
            raise ValueError("morph accessor exceeds its binary buffer")
        selected: list[int] = []
        values = bytearray()
        for vertex in range(count):
            raw = old_binary[start + vertex * stride : start + vertex * stride + 12]
            x, y, z = struct.unpack("<fff", raw)
            if max(abs(x), abs(y), abs(z)) > epsilon:
                selected.append(vertex)
                values.extend(raw)
        if not selected or len(selected) >= count * 0.8:
            continue
        index_component = 5123 if count <= 65_535 else 5125
        index_format = "<H" if index_component == 5123 else "<I"
        indices = b"".join(struct.pack(index_format, vertex) for vertex in selected)
        converted[index] = (indices, bytes(values), len(selected), index_component)
        converted_views.add(view_index)

    index_formats = {5121: ("<B", 1), 5123: ("<H", 2), 5125: ("<I", 4)}
    for index in targets:
        if index in converted:
            continue
        accessor = accessors[index]
        sparse = accessor.get("sparse")
        if (
            not isinstance(sparse, dict)
            or accessor.get("componentType") != 5126
            or accessor.get("type") != "VEC3"
            or accessor.get("bufferView") is not None
        ):
            continue
        indices_meta = sparse.get("indices")
        values_meta = sparse.get("values")
        component = indices_meta.get("componentType") if isinstance(indices_meta, dict) else None
        if (
            not isinstance(indices_meta, dict)
            or not isinstance(values_meta, dict)
            or component not in index_formats
            or not isinstance(indices_meta.get("bufferView"), int)
            or not isinstance(values_meta.get("bufferView"), int)
        ):
            continue
        count = int(sparse.get("count", 0))
        index_format, index_width = index_formats[component]
        index_view = old_views[int(indices_meta["bufferView"])]
        value_view = old_views[int(values_meta["bufferView"])]
        index_start = int(index_view.get("byteOffset", 0)) + int(indices_meta.get("byteOffset", 0))
        value_start = int(value_view.get("byteOffset", 0)) + int(values_meta.get("byteOffset", 0))
        if (
            count <= 0
            or index_start + count * index_width > len(old_binary)
            or value_start + count * 12 > len(old_binary)
        ):
            raise ValueError("sparse morph accessor exceeds its binary buffer")
        retained_indices = bytearray()
        retained_values = bytearray()
        for entry in range(count):
            value_offset = value_start + entry * 12
            raw = old_binary[value_offset : value_offset + 12]
            if max(abs(value) for value in struct.unpack("<fff", raw)) <= epsilon:
                continue
            source_index = struct.unpack_from(
                index_format, old_binary, index_start + entry * index_width
            )[0]
            retained_indices.extend(struct.pack(index_format, source_index))
            retained_values.extend(raw)
        retained_count = len(retained_values) // 12
        if retained_count and retained_count < count:
            converted[index] = (
                bytes(retained_indices),
                bytes(retained_values),
                retained_count,
                int(component),
            )

    used_views: set[int] = set()
    for index, accessor in enumerate(accessors):
        view = accessor.get("bufferView")
        if isinstance(view, int) and index not in converted:
            used_views.add(view)
        sparse = accessor.get("sparse")
        if isinstance(sparse, dict) and index not in converted:
            indices = sparse.get("indices")
            values = sparse.get("values")
            if isinstance(indices, dict) and isinstance(indices.get("bufferView"), int):
                used_views.add(int(indices["bufferView"]))
            if isinstance(values, dict) and isinstance(values.get("bufferView"), int):
                used_views.add(int(values["bufferView"]))
    for image in document.get("images", []):
        view = image.get("bufferView")
        if isinstance(view, int):
            used_views.add(view)

    new_binary = bytearray()
    new_views: list[dict[str, Any]] = []

    def append_block(payload: bytes, metadata: dict[str, Any] | None = None) -> int:
        while len(new_binary) % 4:
            new_binary.append(0)
        view = {"buffer": 0, "byteOffset": len(new_binary), "byteLength": len(payload)}
        if metadata:
            view.update(metadata)
        new_views.append(view)
        new_binary.extend(payload)
        return len(new_views) - 1

    remap: dict[int, int] = {}
    for old_index in sorted(used_views):
        view = old_views[old_index]
        start = int(view.get("byteOffset", 0))
        length = int(view.get("byteLength", 0))
        metadata = {
            key: value
            for key, value in view.items()
            if key not in {"buffer", "byteOffset", "byteLength"}
        }
        remap[old_index] = append_block(old_binary[start : start + length], metadata)

    for index, accessor in enumerate(accessors):
        if index in converted:
            indices, values, count, component = converted[index]
            index_view = append_block(indices)
            value_view = append_block(values)
            accessor.pop("bufferView", None)
            accessor.pop("byteOffset", None)
            accessor["sparse"] = {
                "count": count,
                "indices": {"bufferView": index_view, "componentType": component},
                "values": {"bufferView": value_view},
            }
        elif isinstance(accessor.get("bufferView"), int):
            accessor["bufferView"] = remap[int(accessor["bufferView"])]
        if index not in converted and isinstance(accessor.get("sparse"), dict):
            sparse = cast(dict[str, Any], accessor["sparse"])
            indices = sparse.get("indices")
            values = sparse.get("values")
            if isinstance(indices, dict) and isinstance(indices.get("bufferView"), int):
                indices["bufferView"] = remap[int(indices["bufferView"])]
            if isinstance(values, dict) and isinstance(values.get("bufferView"), int):
                values["bufferView"] = remap[int(values["bufferView"])]
    for image in document.get("images", []):
        if isinstance(image.get("bufferView"), int):
            image["bufferView"] = remap[int(image["bufferView"])]

    document["bufferViews"] = new_views
    document["buffers"] = [{"byteLength": len(new_binary)}]
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    new_binary += b"\0" * ((4 - len(new_binary) % 4) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(new_binary)
    output = (
        struct.pack("<4sII", b"glTF", 2, length)
        + struct.pack("<II", len(encoded), _JSON_CHUNK)
        + encoded
        + struct.pack("<II", len(new_binary), _BIN_CHUNK)
        + new_binary
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(output)
    return len(converted), len(output)


def main() -> None:
    """Parse command-line paths and compact one generated avatar."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    accessors, size = compact(args.source, args.destination)
    print(f"Compacted {accessors} morph accessors into {size} bytes")


if __name__ == "__main__":
    main()
