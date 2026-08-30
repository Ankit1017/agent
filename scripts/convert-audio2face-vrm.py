"""Convert one locally supplied VRM-style GLB into a compact ARKit-52 GLB.

This script runs only inside Blender's Python runtime. It preserves the source
file, creates missing controls from named source shapes or eye geometry, removes
non-runtime morphs, and exports a self-contained standard GLB.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

ARKIT_CONTROLS = {
    "eyeBlinkLeft",
    "eyeLookDownLeft",
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeSquintLeft",
    "eyeWideLeft",
    "eyeBlinkRight",
    "eyeLookDownRight",
    "eyeLookInRight",
    "eyeLookOutRight",
    "eyeLookUpRight",
    "eyeSquintRight",
    "eyeWideRight",
    "jawForward",
    "jawLeft",
    "jawRight",
    "jawOpen",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "mouthLeft",
    "mouthRight",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff",
    "cheekSquintLeft",
    "cheekSquintRight",
    "noseSneerLeft",
    "noseSneerRight",
    "tongueOut",
}

SOURCE_SHAPES: dict[str, tuple[str, ...]] = {
    "mouthRollLower": ("h_expressions.MPB_Down_h",),
    "mouthRollUpper": ("h_expressions.MPB_Up_h",),
    "mouthShrugLower": ("h_expressions.Chin_h",),
    "mouthShrugUpper": ("h_expressions.LlipUp_h", "h_expressions.RlipUp_h"),
    "cheekSquintLeft": ("h_expressions.Lsquint_h",),
    "cheekSquintRight": ("h_expressions.Rsquint_h",),
    "tongueOut": ("OutMiddle_tg_h",),
}


def _arguments() -> tuple[Path, Path]:
    """Read the two explicit paths following Blender's argument separator."""
    if "--" not in sys.argv:
        raise RuntimeError("Expected -- <source.glb> <destination.glb>")
    values = sys.argv[sys.argv.index("--") + 1 :]
    if len(values) != 2:
        raise RuntimeError("Expected exactly one source and destination")
    source = Path(values[0]).resolve(strict=True)
    destination = Path(values[1]).resolve()
    if source.suffix.casefold() != ".glb" or destination.suffix.casefold() != ".glb":
        raise RuntimeError("Source and destination must be GLB files")
    if source == destination:
        raise RuntimeError("Destination must not overwrite the source avatar")
    return source, destination


def _mesh_with_shape(name: str) -> bpy.types.Object | None:
    """Return the first mesh containing the exact source shape."""
    for item in bpy.context.scene.objects:
        if item.type == "MESH" and item.data.shape_keys and name in item.data.shape_keys.key_blocks:
            return item
    return None


def _copy_combined_shape(target: str, sources: tuple[str, ...]) -> None:
    """Create one target by combining source deltas on their owning mesh."""
    owner = _mesh_with_shape(sources[0])
    if owner is None or owner.data.shape_keys is None:
        raise RuntimeError(f"Required source morph is missing for {target}")
    keys = owner.data.shape_keys.key_blocks
    if target in keys:
        return
    if any(source not in keys for source in sources):
        raise RuntimeError(f"Required source morph is missing for {target}")
    basis = keys[0]
    created = owner.shape_key_add(name=target, from_mix=False)
    for index, vertex in enumerate(created.data):
        coordinate = Vector(basis.data[index].co)
        for source in sources:
            coordinate += Vector(keys[source].data[index].co) - Vector(basis.data[index].co)
        vertex.co = coordinate


def _eye_object(side: str) -> bpy.types.Object:
    """Find the separate eye mesh for one character side."""
    marker = f"{side.casefold()}_eye"
    candidates = [
        item
        for item in bpy.context.scene.objects
        if item.type == "MESH" and marker in item.name.casefold()
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one {side} eye mesh")
    return candidates[0]


def _rotated_eye_shape(owner: bpy.types.Object, name: str, axis: str, degrees: float) -> None:
    """Create a directional eye shape by rotating its vertices about their center."""
    if owner.data.shape_keys is None:
        owner.shape_key_add(name="Basis", from_mix=False)
    keys = owner.data.shape_keys.key_blocks
    if name in keys:
        return
    basis = keys[0]
    center = sum((Vector(point.co) for point in basis.data), Vector()) / len(basis.data)
    rotation = Matrix.Rotation(math.radians(degrees), 4, axis)
    created = owner.shape_key_add(name=name, from_mix=False)
    for index, vertex in enumerate(created.data):
        vertex.co = center + rotation @ (Vector(basis.data[index].co) - center)


def _create_eye_controls() -> None:
    """Add four bounded look directions to each separate eye mesh."""
    left = _eye_object("L")
    right = _eye_object("R")
    angle = 9.0
    _rotated_eye_shape(left, "eyeLookUpLeft", "X", -angle)
    _rotated_eye_shape(left, "eyeLookDownLeft", "X", angle)
    _rotated_eye_shape(left, "eyeLookInLeft", "Z", angle)
    _rotated_eye_shape(left, "eyeLookOutLeft", "Z", -angle)
    _rotated_eye_shape(right, "eyeLookUpRight", "X", -angle)
    _rotated_eye_shape(right, "eyeLookDownRight", "X", angle)
    _rotated_eye_shape(right, "eyeLookInRight", "Z", -angle)
    _rotated_eye_shape(right, "eyeLookOutRight", "Z", angle)


def _remove_non_runtime_shapes() -> None:
    """Retain Basis plus only canonical runtime shapes on each mesh."""
    for item in bpy.context.scene.objects:
        if item.type != "MESH" or item.data.shape_keys is None:
            continue
        keys = item.data.shape_keys.key_blocks
        for key in list(keys)[1:][::-1]:
            if key.name not in ARKIT_CONTROLS:
                item.shape_key_remove(key)


def _remove_non_character_objects() -> None:
    """Remove known source preview objects that are not part of the character."""
    for item in list(bpy.context.scene.objects):
        if item.type in {"CAMERA", "LIGHT"} or item.name in {"Cube", "Icosphere"}:
            bpy.data.objects.remove(item, do_unlink=True)


def _control_names() -> set[str]:
    """Collect exported morph names from all meshes."""
    return {
        key.name
        for item in bpy.context.scene.objects
        if item.type == "MESH" and item.data.shape_keys is not None
        for key in list(item.data.shape_keys.key_blocks)[1:]
    }


def main() -> None:
    """Import, retarget, minimize, validate in memory, and export the avatar."""
    source, destination = _arguments()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.gltf(filepath=str(source))
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import the source avatar")

    _create_eye_controls()
    for target, sources in SOURCE_SHAPES.items():
        _copy_combined_shape(target, sources)
    _remove_non_runtime_shapes()
    _remove_non_character_objects()
    missing = sorted(ARKIT_CONTROLS.difference(_control_names()))
    if missing:
        raise RuntimeError("Converted avatar is missing controls: " + ", ".join(missing))

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RuntimeError("Destination already exists; refusing to overwrite it")
    bpy.data.orphans_purge(do_recursive=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(destination),
        export_format="GLB",
        export_yup=True,
        export_morph=True,
        export_morph_normal=False,
        export_morph_tangent=False,
        export_cameras=False,
        export_lights=False,
        export_unused_images=False,
    )
    if "FINISHED" not in result or not destination.is_file():
        raise RuntimeError("Blender could not export the converted avatar")
    print(f"Converted ARKit-52 avatar: {destination.name} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
