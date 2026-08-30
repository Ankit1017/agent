"""Validate and read one fixed, self-contained Audio2Face GLB avatar."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from local_harness.domain.audio2face import (
    ARKIT_FACE_CONTROLS,
    AUDIO2FACE_TONGUE_CONTROLS,
    FaceAvatarAsset,
    FaceAvatarChoice,
    FaceAvatarStatus,
)
from local_harness.domain.errors import Audio2FaceUnavailableError

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_MAX_MESHES = 32
_MAX_VERTICES = 500_000
_MAX_PRIMITIVES = 128
_MAX_MORPHS_PER_MESH = 80
_MAX_MATERIALS = 64
_MAX_TEXTURES = 32
_MAX_NODES = 256
_MAX_NODE_DEPTH = 32
_SUPPORTED_EXTENSIONS = frozenset(
    {
        "KHR_lights_punctual",
        "KHR_materials_clearcoat",
        "KHR_materials_emissive_strength",
        "KHR_materials_ior",
        "KHR_materials_specular",
        "KHR_materials_transmission",
        "KHR_materials_unlit",
        "KHR_materials_volume",
        "KHR_mesh_quantization",
        "KHR_texture_transform",
    }
)
_MANIFEST_KEYS = {
    "version",
    "name",
    "sha256",
    "face_controls",
    "tongue_controls",
    "rights_confirmed",
    "meshes",
    "vertices",
    "primitives",
    "materials",
    "textures",
    "nodes",
    "node_depth",
}
_AVATAR_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


@dataclass(frozen=True, slots=True)
class AvatarValidation:
    """Describe the bounded structure of a validated GLB avatar."""

    face_controls: tuple[str, ...]
    tongue_controls: tuple[str, ...]
    meshes: int
    vertices: int
    primitives: int
    materials: int
    textures: int
    nodes: int
    node_depth: int


class LocalFaceAvatarRepository:
    """Read setup-installed avatars after revalidating manifests and bytes."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        """Load one fixed avatar without accepting request-controlled paths."""
        self._root = root.resolve()
        self._max_bytes = max_bytes
        self._assets: dict[str, FaceAvatarAsset] = {}
        self._statuses: dict[str, FaceAvatarStatus] = {}
        self._load_catalog()
        self._default_id = (
            "default"
            if "default" in self._assets
            else next(
                iter(sorted(self._assets)),
                "default" if "default" in self._statuses else "",
            )
        )

    def status(self, avatar_id: str | None = None) -> FaceAvatarStatus:
        """Return safe avatar setup state."""
        selected = avatar_id or self._default_id
        if selected in self._statuses:
            return self._statuses[selected]
        if not selected:
            return _missing_status()
        return FaceAvatarStatus(
            False,
            "",
            (),
            (),
            "Selected 3D avatar is unavailable. Run the protected avatar setup.",
            selected,
        )

    def asset(self, avatar_id: str | None = None) -> FaceAvatarAsset:
        """Return one exact validated in-memory GLB asset."""
        selected = avatar_id or self._default_id
        asset = self._assets.get(selected)
        if asset is None:
            raise Audio2FaceUnavailableError(self.status(selected).setup)
        return asset

    def catalog(self) -> tuple[FaceAvatarChoice, ...]:
        """Return a deterministic safe avatar catalog."""
        return tuple(
            FaceAvatarChoice(
                avatar_id,
                asset.name,
                len(asset.face_controls),
                len(asset.tongue_controls),
                asset.sha256,
            )
            for avatar_id, asset in sorted(self._assets.items())
        )

    def default_id(self) -> str:
        """Return the legacy avatar first, then the first named avatar."""
        return self._default_id

    def _load_catalog(self) -> None:
        self._load_one("default", self._root)
        catalog_root = self._root.parent / "avatars"
        if not catalog_root.is_dir():
            return
        for candidate in sorted(catalog_root.iterdir(), key=lambda item: item.name):
            if candidate.is_dir() and _AVATAR_ID.fullmatch(candidate.name):
                self._load_one(candidate.name, candidate)

    def _load_one(self, avatar_id: str, root: Path) -> None:
        asset_path = root / "avatar.glb"
        manifest_path = root / "manifest.json"
        if not asset_path.is_file() or not manifest_path.is_file():
            if avatar_id == "default":
                self._statuses[avatar_id] = _missing_status()
            return
        try:
            content = asset_path.read_bytes()
            if not 0 < len(content) <= self._max_bytes:
                raise ValueError("avatar file exceeds the configured size limit")
            raw_manifest = cast(object, json.loads(manifest_path.read_text(encoding="utf-8")))
            manifest = _validate_manifest(raw_manifest)
            digest = hashlib.sha256(content).hexdigest()
            if manifest["sha256"] != digest:
                raise ValueError("avatar checksum does not match its manifest")
            validation = validate_glb(content)
            if tuple(cast(list[str], manifest["face_controls"])) != validation.face_controls:
                raise ValueError("avatar face-control manifest is inconsistent")
            if tuple(cast(list[str], manifest["tongue_controls"])) != validation.tongue_controls:
                raise ValueError("avatar tongue-control manifest is inconsistent")
            for key in (
                "meshes",
                "vertices",
                "primitives",
                "materials",
                "textures",
                "nodes",
                "node_depth",
            ):
                if manifest[key] != getattr(validation, key):
                    raise ValueError("avatar scene-count manifest is inconsistent")
            name = cast(str, manifest["name"])
            self._assets[avatar_id] = FaceAvatarAsset(
                name,
                digest,
                validation.face_controls,
                validation.tongue_controls,
                content,
                avatar_id,
            )
            self._statuses[avatar_id] = FaceAvatarStatus(
                True,
                name,
                validation.face_controls,
                validation.tongue_controls,
                "3D Audio2Face avatar is ready.",
                avatar_id,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            message = str(exc)[:180]
            self._statuses[avatar_id] = FaceAvatarStatus(
                False,
                "",
                (),
                (),
                f"Invalid local avatar: {message}. Run scripts/setup-audio2face-avatar.ps1.",
                avatar_id,
            )


def validate_glb(content: bytes) -> AvatarValidation:
    """Validate a closed, self-contained GLB and return bounded scene metadata."""
    document = _read_glb_document(content)
    asset = document.get("asset")
    if not isinstance(asset, dict) or str(asset.get("version")) != "2.0":
        raise ValueError("avatar must use glTF 2.0")
    _validate_extensions(document)
    for buffer in _objects(document, "buffers"):
        if "uri" in buffer:
            raise ValueError("avatar buffers must be embedded in the GLB")
    buffer_views = _objects(document, "bufferViews")
    for image in _objects(document, "images"):
        if "uri" in image:
            raise ValueError("avatar images must use embedded buffer views")
        buffer_view = image.get("bufferView")
        mime_type = image.get("mimeType")
        if (
            not isinstance(buffer_view, int)
            or not 0 <= buffer_view < len(buffer_views)
            or mime_type not in {"image/png", "image/jpeg", "image/webp"}
        ):
            raise ValueError("avatar embedded image metadata is invalid")

    meshes = _objects(document, "meshes")
    if len(meshes) > _MAX_MESHES:
        raise ValueError(f"avatar exceeds the {_MAX_MESHES}-mesh limit")
    accessors = _objects(document, "accessors")
    vertices = 0
    primitive_count = 0
    controls: set[str] = set()
    for mesh in meshes:
        primitives = mesh.get("primitives", [])
        if not isinstance(primitives, list):
            raise ValueError("avatar mesh primitives are invalid")
        primitive_count += len(primitives)
        extras = mesh.get("extras", {})
        names = extras.get("targetNames", []) if isinstance(extras, dict) else []
        if names and (
            not isinstance(names, list)
            or any(not isinstance(name, str) or not name for name in names)
        ):
            raise ValueError("avatar morph-target names are invalid")
        if len(names) > _MAX_MORPHS_PER_MESH:
            raise ValueError(
                f"avatar exceeds the {_MAX_MORPHS_PER_MESH}-morph-target-per-mesh limit"
            )
        controls.update(cast(list[str], names))
        for primitive in primitives:
            if not isinstance(primitive, dict):
                raise ValueError("avatar mesh primitive is invalid")
            targets = primitive.get("targets", [])
            if not isinstance(targets, list) or len(targets) > _MAX_MORPHS_PER_MESH:
                raise ValueError(
                    f"avatar exceeds the {_MAX_MORPHS_PER_MESH}-morph-target-per-mesh limit"
                )
            if names and len(targets) != len(names):
                raise ValueError("avatar morph names do not match its morph targets")
            attributes = primitive.get("attributes", {})
            position = attributes.get("POSITION") if isinstance(attributes, dict) else None
            if not isinstance(position, int) or not 0 <= position < len(accessors):
                raise ValueError("avatar primitive has no valid POSITION accessor")
            count = accessors[position].get("count")
            if not isinstance(count, int) or count < 0:
                raise ValueError("avatar vertex count is invalid")
            vertices += count
    if primitive_count > _MAX_PRIMITIVES:
        raise ValueError(f"avatar exceeds the {_MAX_PRIMITIVES}-primitive limit")
    if vertices > _MAX_VERTICES:
        raise ValueError(f"avatar exceeds the {_MAX_VERTICES}-vertex limit")

    materials = len(_objects(document, "materials"))
    if materials > _MAX_MATERIALS:
        raise ValueError(f"avatar exceeds the {_MAX_MATERIALS}-material limit")
    textures = max(len(_objects(document, "textures")), len(_objects(document, "images")))
    if textures > _MAX_TEXTURES:
        raise ValueError(f"avatar exceeds the {_MAX_TEXTURES}-texture/image limit")
    nodes = _objects(document, "nodes")
    if len(nodes) > _MAX_NODES:
        raise ValueError(f"avatar exceeds the {_MAX_NODES}-node limit")
    depth = _node_depth(nodes)
    if depth > _MAX_NODE_DEPTH:
        raise ValueError(f"avatar exceeds the {_MAX_NODE_DEPTH}-node-depth limit")

    missing = sorted(set(ARKIT_FACE_CONTROLS).difference(controls))
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"avatar is missing required ARKit controls: {preview}")
    tongues = tuple(name for name in AUDIO2FACE_TONGUE_CONTROLS if name in controls)
    return AvatarValidation(
        ARKIT_FACE_CONTROLS,
        tongues,
        len(meshes),
        vertices,
        primitive_count,
        materials,
        textures,
        len(nodes),
        depth,
    )


def install_avatar(asset_path: Path, root: Path, max_bytes: int, name: str) -> AvatarValidation:
    """Validate and atomically install one explicitly selected local avatar."""
    content = asset_path.resolve(strict=True).read_bytes()
    if not 0 < len(content) <= max_bytes:
        raise ValueError("avatar file exceeds the configured size limit")
    if not 1 <= len(name.strip()) <= 80:
        raise ValueError("avatar display name must contain 1-80 characters")
    validation = validate_glb(content)
    root = root.resolve()
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".avatar-install-{uuid.uuid4().hex}"
    backup = parent / f".avatar-backup-{uuid.uuid4().hex}"
    staging.mkdir()
    manifest = {
        "version": 1,
        "name": name.strip(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "face_controls": list(validation.face_controls),
        "tongue_controls": list(validation.tongue_controls),
        "rights_confirmed": True,
        **{
            key: value
            for key, value in asdict(validation).items()
            if key not in {"face_controls", "tongue_controls"}
        },
    }
    try:
        staging.joinpath("avatar.glb").write_bytes(content)
        staging.joinpath("manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        if root.exists():
            os.replace(root, backup)
        os.replace(staging, root)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if backup.exists() and not root.exists():
            os.replace(backup, root)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup.exists():
            shutil.rmtree(backup)
    return validation


def _read_glb_document(content: bytes) -> dict[str, object]:
    if len(content) < 20 or content[:4] != _GLB_MAGIC:
        raise ValueError("avatar is not a binary GLB")
    magic, version, declared_length = struct.unpack_from("<4sII", content, 0)
    if magic != _GLB_MAGIC or version != 2 or declared_length != len(content):
        raise ValueError("avatar GLB header is invalid")
    offset = 12
    json_payload: bytes | None = None
    has_binary = False
    while offset < len(content):
        if offset + 8 > len(content):
            raise ValueError("avatar GLB chunk header is incomplete")
        length, kind = struct.unpack_from("<II", content, offset)
        offset += 8
        end = offset + length
        if end > len(content):
            raise ValueError("avatar GLB chunk exceeds the file")
        if kind == _JSON_CHUNK:
            if json_payload is not None:
                raise ValueError("avatar GLB contains multiple JSON chunks")
            json_payload = content[offset:end]
        elif kind == _BIN_CHUNK:
            has_binary = True
        else:
            raise ValueError("avatar GLB contains an unsupported chunk")
        offset = end
    if json_payload is None or not has_binary:
        raise ValueError("avatar GLB must contain JSON and binary chunks")
    try:
        value = cast(object, json.loads(json_payload.rstrip(b" \t\r\n\x00").decode("utf-8")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("avatar GLB JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("avatar GLB document must be an object")
    return cast(dict[str, object], value)


def _validate_extensions(document: dict[str, object]) -> None:
    for key in ("extensionsUsed", "extensionsRequired"):
        values = document.get(key, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError("avatar GLB extensions are invalid")
        unsupported = sorted(set(cast(list[str], values)).difference(_SUPPORTED_EXTENSIONS))
        if unsupported:
            raise ValueError(f"avatar uses unsupported extension: {unsupported[0]}")


def _objects(document: dict[str, object], key: str) -> list[dict[str, object]]:
    value = document.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"avatar {key} collection is invalid")
    return cast(list[dict[str, object]], value)


def _node_depth(nodes: list[dict[str, object]]) -> int:
    visiting: set[int] = set()
    memo: dict[int, int] = {}

    def depth(index: int) -> int:
        if not 0 <= index < len(nodes):
            raise ValueError("avatar node references an invalid child")
        if index in visiting:
            raise ValueError("avatar node graph contains a cycle")
        if index in memo:
            return memo[index]
        visiting.add(index)
        children = nodes[index].get("children", [])
        if not isinstance(children, list) or any(not isinstance(item, int) for item in children):
            raise ValueError("avatar node children are invalid")
        value = 1 + max((depth(item) for item in cast(list[int], children)), default=0)
        visiting.remove(index)
        memo[index] = value
        return value

    return max((depth(index) for index in range(len(nodes))), default=0)


def _validate_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("avatar manifest is invalid")
    manifest = cast(dict[str, object], value)
    if manifest["version"] != 1 or manifest["rights_confirmed"] is not True:
        raise ValueError("avatar manifest version or rights acknowledgement is invalid")
    name = manifest["name"]
    digest = manifest["sha256"]
    if not isinstance(name, str) or not 1 <= len(name) <= 80:
        raise ValueError("avatar manifest name is invalid")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("avatar manifest checksum is invalid")
    for key in ("face_controls", "tongue_controls"):
        controls = manifest[key]
        if not isinstance(controls, list) or any(not isinstance(item, str) for item in controls):
            raise ValueError("avatar control manifest is invalid")
    for key in (
        "meshes",
        "vertices",
        "primitives",
        "materials",
        "textures",
        "nodes",
        "node_depth",
    ):
        if not isinstance(manifest[key], int) or cast(int, manifest[key]) < 0:
            raise ValueError("avatar scene-count manifest is invalid")
    return manifest


def _missing_status() -> FaceAvatarStatus:
    return FaceAvatarStatus(
        False,
        "",
        (),
        (),
        "Run scripts/setup-audio2face-avatar.ps1 with a licensed ARKit-52 GLB.",
    )
