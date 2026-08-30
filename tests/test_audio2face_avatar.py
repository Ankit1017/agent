"""Offline validation coverage for fixed local Audio2Face GLB avatars."""

from __future__ import annotations

import copy
import json
import struct
from pathlib import Path
from typing import cast

import pytest

from local_harness.domain.audio2face import ARKIT_FACE_CONTROLS, AUDIO2FACE_TONGUE_CONTROLS
from local_harness.domain.errors import Audio2FaceUnavailableError
from local_harness.infrastructure.audio2face_avatar import (
    LocalFaceAvatarRepository,
    install_avatar,
    validate_glb,
)


def _glb(document: dict[str, object], binary: bytes = b"\0" * 12) -> bytes:
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    binary += b"\0" * ((4 - len(binary) % 4) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (
        struct.pack("<4sII", b"glTF", 2, length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _document() -> dict[str, object]:
    targets: list[dict[str, object]] = [{} for _ in ARKIT_FACE_CONTROLS]
    return {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": 12}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 12}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 1,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [0, 0, 0],
            }
        ],
        "meshes": [
            {
                "extras": {"targetNames": list(ARKIT_FACE_CONTROLS)},
                "primitives": [{"attributes": {"POSITION": 0}, "targets": targets}],
            }
        ],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }


def test_valid_glb_installs_atomically_and_loads_from_fixed_repository(tmp_path: Path) -> None:
    """A rights-confirmed ARKit GLB is installed with a verified safe manifest."""
    source = tmp_path / "face.glb"
    source.write_bytes(_glb(_document()))
    root = tmp_path / "models" / "avatar"
    validation = install_avatar(source, root, 52_428_800, "Test Face")
    assert validation.face_controls == ARKIT_FACE_CONTROLS
    repository = LocalFaceAvatarRepository(root, 52_428_800)
    assert repository.status().available is True
    assert repository.asset().content == source.read_bytes()
    assert repository.asset().name == "Test Face"


def test_repository_reports_missing_and_tampered_installations_safely(tmp_path: Path) -> None:
    """Missing and checksum-tampered fixed assets are unavailable without leaking their path."""
    root = tmp_path / "avatar"
    missing = LocalFaceAvatarRepository(root, 52_428_800)
    assert missing.status().available is False
    with pytest.raises(Audio2FaceUnavailableError, match="setup-audio2face-avatar"):
        missing.asset()

    source = tmp_path / "face.glb"
    source.write_bytes(_glb(_document()))
    install_avatar(source, root, 52_428_800, "Test Face")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    tampered = LocalFaceAvatarRepository(root, 52_428_800)
    assert tampered.status().available is False
    assert str(root) not in tampered.status().setup
    with pytest.raises(Audio2FaceUnavailableError, match="checksum"):
        tampered.asset()


def test_repository_catalog_preserves_legacy_and_named_avatar_selection(tmp_path: Path) -> None:
    """Named characters are exact-ID selectable without replacing the legacy avatar."""
    source = tmp_path / "face.glb"
    source.write_bytes(_glb(_document()))
    model_root = tmp_path / "audio2face"
    install_avatar(source, model_root / "avatar", 52_428_800, "Original Face")
    install_avatar(source, model_root / "avatars" / "amber", 52_428_800, "Amber")

    repository = LocalFaceAvatarRepository(model_root / "avatar", 52_428_800)
    assert repository.default_id() == "default"
    assert [item.avatar_id for item in repository.catalog()] == ["amber", "default"]
    assert repository.asset("amber").name == "Amber"
    assert repository.asset("amber").avatar_id == "amber"
    with pytest.raises(Audio2FaceUnavailableError, match="Selected 3D avatar"):
        repository.asset("unknown")


def test_glb_rejects_missing_controls_and_external_resources() -> None:
    """Morph omissions and external buffers or images fail with actionable errors."""
    missing = _document()
    meshes = cast(list[dict[str, object]], missing["meshes"])
    mesh = meshes[0]
    extras = cast(dict[str, object], mesh["extras"])
    primitives = cast(list[dict[str, object]], mesh["primitives"])
    extras["targetNames"] = list(ARKIT_FACE_CONTROLS[:-1])
    primitives[0]["targets"] = [{} for _ in ARKIT_FACE_CONTROLS[:-1]]
    with pytest.raises(ValueError, match="missing required ARKit"):
        validate_glb(_glb(missing))

    external = _document()
    external["buffers"] = [{"byteLength": 12, "uri": "https://example.test/a.bin"}]
    with pytest.raises(ValueError, match="embedded"):
        validate_glb(_glb(external))

    image = _document()
    image["images"] = [{"uri": "face.png"}]
    with pytest.raises(ValueError, match="buffer views"):
        validate_glb(_glb(image))


def test_glb_signature_and_optional_tongue_intersection() -> None:
    """Only GLB 2 is accepted and optional matching tongue controls are retained."""
    with pytest.raises(ValueError, match="binary GLB"):
        validate_glb(b"not-a-glb")

    document = _document()
    meshes = cast(list[dict[str, object]], document["meshes"])
    extras = cast(dict[str, object], meshes[0]["extras"])
    primitives = cast(list[dict[str, object]], meshes[0]["primitives"])
    names = list(ARKIT_FACE_CONTROLS) + list(AUDIO2FACE_TONGUE_CONTROLS[:2])
    extras["targetNames"] = names
    primitives[0]["targets"] = [{} for _ in names]
    validation = validate_glb(_glb(document))
    assert validation.tongue_controls == AUDIO2FACE_TONGUE_CONTROLS[:2]


def test_glb_rejects_malformed_scene_collections_and_references() -> None:
    """Malformed scene shapes fail closed before Three.js can receive the asset."""
    invalid: list[tuple[dict[str, object], str]] = []

    version = _document()
    version["asset"] = {"version": "1.0"}
    invalid.append((version, "glTF 2.0"))

    extension = _document()
    extension["extensionsUsed"] = ["EXT_external_resource"]
    invalid.append((extension, "unsupported extension"))

    collections = _document()
    collections["materials"] = "invalid"
    invalid.append((collections, "materials collection"))

    primitives_document = copy.deepcopy(_document())
    meshes = cast(list[dict[str, object]], primitives_document["meshes"])
    meshes[0]["primitives"] = "invalid"
    invalid.append((primitives_document, "mesh primitives"))

    names_document = copy.deepcopy(_document())
    meshes = cast(list[dict[str, object]], names_document["meshes"])
    extras = cast(dict[str, object], meshes[0]["extras"])
    extras["targetNames"] = [""]
    invalid.append((names_document, "morph-target names"))

    target_document = copy.deepcopy(_document())
    meshes = cast(list[dict[str, object]], target_document["meshes"])
    primitives = cast(list[dict[str, object]], meshes[0]["primitives"])
    primitives[0]["targets"] = []
    invalid.append((target_document, "morph names do not match"))

    position_document = copy.deepcopy(_document())
    meshes = cast(list[dict[str, object]], position_document["meshes"])
    primitives = cast(list[dict[str, object]], meshes[0]["primitives"])
    primitives[0]["attributes"] = {}
    invalid.append((position_document, "POSITION accessor"))

    count_document = copy.deepcopy(_document())
    accessors = cast(list[dict[str, object]], count_document["accessors"])
    accessors[0]["count"] = -1
    invalid.append((count_document, "vertex count"))

    child_document = copy.deepcopy(_document())
    child_document["nodes"] = [{"children": [4]}]
    invalid.append((child_document, "invalid child"))

    cycle_document = copy.deepcopy(_document())
    cycle_document["nodes"] = [{"children": [0]}]
    invalid.append((cycle_document, "cycle"))

    for document, message in invalid:
        with pytest.raises(ValueError, match=message):
            validate_glb(_glb(document))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(meshes=value["meshes"] * 33), "32-mesh"),
        (lambda value: value["accessors"][0].update(count=500_001), "500000-vertex"),
        (
            lambda value: value["meshes"][0].update(
                primitives=value["meshes"][0]["primitives"] * 129
            ),
            "128-primitive",
        ),
        (
            lambda value: value["meshes"][0]["extras"].update(
                targetNames=list(ARKIT_FACE_CONTROLS) + [f"extra{index}" for index in range(29)]
            ),
            "80-morph-target",
        ),
        (lambda value: value.update(materials=[{}] * 65), "64-material"),
        (lambda value: value.update(textures=[{}] * 33), "32-texture"),
        (
            lambda value: value.update(images=[{"bufferView": 0, "mimeType": "image/png"}] * 33),
            "32-texture",
        ),
        (lambda value: value.update(nodes=[{}] * 257), "256-node"),
    ],
)
def test_glb_enforces_numeric_complexity_limits(mutate: object, message: str) -> None:
    """Each published scene-complexity limit is deterministic and testable."""
    document = _document()
    mutate(document)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        validate_glb(_glb(document))


def test_glb_rejects_node_depth_and_configured_byte_limit(tmp_path: Path) -> None:
    """Deep graphs and configured file-size violations fail without exposing paths."""
    document = _document()
    document["nodes"] = [({"children": [index + 1]} if index < 32 else {}) for index in range(33)]
    with pytest.raises(ValueError, match="32-node-depth"):
        validate_glb(_glb(document))

    source = tmp_path / "large.glb"
    source.write_bytes(_glb(_document()))
    with pytest.raises(ValueError, match="configured size"):
        install_avatar(source, tmp_path / "avatar", 10, "Face")
