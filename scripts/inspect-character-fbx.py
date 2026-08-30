"""Inspect one local FBX character inside Blender without modifying it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy


def main() -> None:
    """Import the explicit FBX and print bounded structural metadata."""
    if "--" not in sys.argv:
        raise RuntimeError("Expected -- <source.fbx>")
    values = sys.argv[sys.argv.index("--") + 1 :]
    if len(values) != 1:
        raise RuntimeError("Expected exactly one FBX source")
    source = Path(values[0]).resolve(strict=True)
    if source.suffix.casefold() != ".fbx":
        raise RuntimeError("Source must be an FBX file")

    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.fbx(filepath=str(source), use_anim=False)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import the source character")

    meshes: list[dict[str, object]] = []
    controls: set[str] = set()
    for item in bpy.context.scene.objects:
        if item.type != "MESH":
            continue
        shapes = (
            [key.name for key in list(item.data.shape_keys.key_blocks)[1:]]
            if item.data.shape_keys is not None
            else []
        )
        controls.update(shapes)
        z_values = [float(vertex.co.z) for vertex in item.data.vertices]
        meshes.append(
            {
                "name": item.name[:120],
                "vertices": len(item.data.vertices),
                "materials": len(item.data.materials),
                "morphs": shapes[:300],
                "z_range": [min(z_values, default=0.0), max(z_values, default=0.0)],
                "vertices_above_135": sum(value >= 135.0 for value in z_values),
                "vertices_above_140": sum(value >= 140.0 for value in z_values),
                "vertices_above_145": sum(value >= 145.0 for value in z_values),
            }
        )
    bones = sorted(
        {
            bone.name
            for item in bpy.context.scene.objects
            if item.type == "ARMATURE"
            for bone in item.data.bones
        }
    )
    images = [
        {
            "name": image.name[:120],
            "packed": image.packed_file is not None,
            "available": image.has_data,
        }
        for image in bpy.data.images
    ]
    output = {
        "meshes": meshes[:64],
        "mesh_count": len(meshes),
        "vertex_count": sum(int(mesh["vertices"]) for mesh in meshes),
        "material_count": len(bpy.data.materials),
        "image_count": len(images),
        "images": images[:64],
        "morph_count": len(controls),
        "morphs": sorted(controls)[:500],
        "bone_count": len(bones),
        "bones": bones[:500],
    }
    print("HARNESS_CHARACTER_INSPECTION=" + json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    main()
