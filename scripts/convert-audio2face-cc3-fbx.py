"""Convert a local Character Creator FBX into a bounded ARKit-52 GLB.

This Blender-only script preserves the source, maps Character Creator facial
shapes to canonical Audio2Face names, keeps an upper-body presenter, and
reduces non-facial geometry and embedded images to browser-safe bounds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import bpy

ARKIT_SOURCES: dict[str, tuple[str, ...]] = {
    "eyeBlinkLeft": ("Eye_Blink_L",),
    "eyeLookDownLeft": ("Eye_L_Look_Down",),
    "eyeLookInLeft": ("Eye_L_Look_R",),
    "eyeLookOutLeft": ("Eye_L_Look_L",),
    "eyeLookUpLeft": ("Eye_L_Look_Up",),
    "eyeSquintLeft": ("Eye_Squint_L",),
    "eyeWideLeft": ("Eye_Wide_L",),
    "eyeBlinkRight": ("Eye_Blink_R",),
    "eyeLookDownRight": ("Eye_R_Look_Down",),
    "eyeLookInRight": ("Eye_R_Look_L",),
    "eyeLookOutRight": ("Eye_R_Look_R",),
    "eyeLookUpRight": ("Eye_R_Look_Up",),
    "eyeSquintRight": ("Eye_Squint_R",),
    "eyeWideRight": ("Eye_Wide_R",),
    "jawForward": ("Jaw_Forward",),
    "jawLeft": ("Jaw_L",),
    "jawRight": ("Jaw_R",),
    "jawOpen": ("Jaw_Open",),
    "mouthClose": ("Mouth_Close",),
    "mouthFunnel": (
        "Mouth_Funnel_Up_L",
        "Mouth_Funnel_Up_R",
        "Mouth_Funnel_Down_L",
        "Mouth_Funnel_Down_R",
    ),
    "mouthPucker": (
        "Mouth_Pucker_Up_L",
        "Mouth_Pucker_Up_R",
        "Mouth_Pucker_Down_L",
        "Mouth_Pucker_Down_R",
    ),
    "mouthLeft": ("Mouth_L",),
    "mouthRight": ("Mouth_R",),
    "mouthSmileLeft": ("Mouth_Smile_L",),
    "mouthSmileRight": ("Mouth_Smile_R",),
    "mouthFrownLeft": ("Mouth_Frown_L",),
    "mouthFrownRight": ("Mouth_Frown_R",),
    "mouthDimpleLeft": ("Mouth_Dimple_L",),
    "mouthDimpleRight": ("Mouth_Dimple_R",),
    "mouthStretchLeft": ("Mouth_Stretch_L",),
    "mouthStretchRight": ("Mouth_Stretch_R",),
    "mouthRollLower": ("Mouth_Roll_In_Lower_L", "Mouth_Roll_In_Lower_R"),
    "mouthRollUpper": ("Mouth_Roll_In_Upper_L", "Mouth_Roll_In_Upper_R"),
    "mouthShrugLower": ("Mouth_Shrug_Lower",),
    "mouthShrugUpper": ("Mouth_Shrug_Upper",),
    "mouthPressLeft": ("Mouth_Press_L",),
    "mouthPressRight": ("Mouth_Press_R",),
    "mouthLowerDownLeft": ("Mouth_Down_Lower_L",),
    "mouthLowerDownRight": ("Mouth_Down_Lower_R",),
    "mouthUpperUpLeft": ("Mouth_Up_Upper_L",),
    "mouthUpperUpRight": ("Mouth_Up_Upper_R",),
    "browDownLeft": ("Brow_Drop_L",),
    "browDownRight": ("Brow_Drop_R",),
    "browInnerUp": ("Brow_Raise_Inner_L", "Brow_Raise_Inner_R"),
    "browOuterUpLeft": ("Brow_Raise_Outer_L",),
    "browOuterUpRight": ("Brow_Raise_Outer_R",),
    "cheekPuff": ("Cheek_Puff_L", "Cheek_Puff_R"),
    "cheekSquintLeft": ("Cheek_Raise_L",),
    "cheekSquintRight": ("Cheek_Raise_R",),
    "noseSneerLeft": ("Nose_Sneer_L",),
    "noseSneerRight": ("Nose_Sneer_R",),
    "tongueOut": ("Tongue_Out",),
}

FACE_CONTROLS = tuple(ARKIT_SOURCES)
EYE_CONTROLS = tuple(name for name in FACE_CONTROLS if name.startswith("eye"))
BROW_CONTROLS = tuple(name for name in FACE_CONTROLS if name.startswith("brow"))
KEEP_OBJECTS = {
    "CC_Base_Body",
    "Punk_Leather_Jacket",
    "Crop_T_Shirt",
    "Real_Hair",
    "Hair_Base",
    "Bang",
    "Bun",
    "CC_Base_EyeOcclusion",
    "CC_Base_Eye",
    "CC_Base_TearLine",
    "Camila_Brow",
    "CC_Base_Tongue",
    "CC_Base_Teeth",
}


def _activate_only(item: bpy.types.Object) -> None:
    """Make one explicit object the sole target of Blender edit operations."""
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    item.select_set(True)
    bpy.context.view_layer.objects.active = item


def _arguments() -> tuple[Path, Path]:
    """Read the explicit source and destination following Blender's separator."""
    if "--" not in sys.argv:
        raise RuntimeError("Expected -- <source.fbx> <destination.glb>")
    values = sys.argv[sys.argv.index("--") + 1 :]
    if len(values) != 2:
        raise RuntimeError("Expected exactly one source and destination")
    source = Path(values[0]).resolve(strict=True)
    destination = Path(values[1]).resolve()
    if source.suffix.casefold() != ".fbx" or destination.suffix.casefold() != ".glb":
        raise RuntimeError("Source must be FBX and destination must be GLB")
    if destination.exists():
        raise RuntimeError("Destination already exists; refusing to overwrite it")
    return source, destination


def _delete_vertices(
    item: bpy.types.Object, *, above: float | None = None, below: float | None = None
) -> None:
    """Delete vertices outside one Z boundary across all shape keys."""
    _activate_only(item)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for vertex in item.data.vertices:
        vertex.select = (above is not None and vertex.co.z >= above) or (
            below is not None and vertex.co.z < below
        )
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.delete(type="VERT")
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")


def _remove_shape_keys(item: bpy.types.Object) -> None:
    """Remove every shape key from one mesh."""
    if item.data.shape_keys is None:
        return
    for key in list(item.data.shape_keys.key_blocks)[::-1]:
        item.shape_key_remove(key)


def _prepare_body() -> bpy.types.Object:
    """Keep one continuous shape-keyed upper-body skin mesh."""
    body = bpy.data.objects.get("CC_Base_Body")
    if body is None or body.type != "MESH" or body.data.shape_keys is None:
        raise RuntimeError("Character Creator body mesh with facial shapes is missing")
    _delete_vertices(body, below=115.0)
    print(f"Retained {len(body.data.vertices)} continuous presenter skin vertices")
    return body


def _copy_shape(item: bpy.types.Object, target: str, sources: tuple[str, ...]) -> bool:
    """Create a canonical target by summing available source-shape deltas."""
    if item.data.shape_keys is None:
        return False
    keys = item.data.shape_keys.key_blocks
    available = [source for source in sources if source in keys]
    if not available:
        return False
    basis = keys[0]
    created = item.shape_key_add(name=target, from_mix=False)
    for index, point in enumerate(created.data):
        point.co = basis.data[index].co.copy()
        for source in available:
            point.co += keys[source].data[index].co - basis.data[index].co
    return True


def _map_shapes(item: bpy.types.Object, controls: tuple[str, ...]) -> None:
    """Map and retain only the requested canonical controls on one mesh."""
    if item.data.shape_keys is None:
        return
    created = {target for target in controls if _copy_shape(item, target, ARKIT_SOURCES[target])}
    for key in list(item.data.shape_keys.key_blocks)[1:][::-1]:
        if key.name not in created:
            item.shape_key_remove(key)


def _decimate(item: bpy.types.Object, ratio: float) -> None:
    """Apply bounded simplification only to meshes without facial shapes."""
    if item.data.shape_keys is not None or ratio >= 1.0:
        return
    _activate_only(item)
    modifier = item.modifiers.new(name="Harness presenter optimization", type="DECIMATE")
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.ops.object.select_all(action="DESELECT")


def _optimize_images() -> None:
    """Bound embedded source images while retaining diffuse and alpha textures."""
    for image in list(bpy.data.images):
        if image.source != "FILE" or not image.has_data:
            continue
        width, height = image.size
        maximum = max(width, height)
        if maximum > 384:
            scale = 384 / maximum
            image.scale(max(1, round(width * scale)), max(1, round(height * scale)))
        image.pack()


def _simplify_materials() -> None:
    """Retain bounded presenter PBR maps and drop oversized unused shader maps."""
    for material in bpy.data.materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        tree = material.node_tree
        image_nodes = [node for node in tree.nodes if node.type == "TEX_IMAGE" and node.image]
        diffuse = next(
            (node.image for node in image_nodes if "diffuse" in node.image.name.casefold()),
            image_nodes[0].image if image_nodes else None,
        )
        opacity = next(
            (node.image for node in image_nodes if "opacity" in node.image.name.casefold()),
            None,
        )
        old_principled = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        base_color = (
            tuple(old_principled.inputs["Base Color"].default_value)
            if old_principled is not None
            else (0.5, 0.5, 0.5, 1.0)
        )
        tree.nodes.clear()
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Base Color"].default_value = base_color
        shader.inputs["Roughness"].default_value = 0.52
        tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        material_name = material.name.casefold()
        if "female_t_shirt" in material_name:
            shader.inputs["Base Color"].default_value = (0.012, 0.014, 0.018, 1.0)
            shader.inputs["Roughness"].default_value = 0.68
        elif diffuse is not None:
            diffuse_node = tree.nodes.new("ShaderNodeTexImage")
            diffuse_node.image = diffuse
            tree.links.new(diffuse_node.outputs["Color"], shader.inputs["Base Color"])
        if "skin_head" in material_name:
            shader.inputs["Roughness"].default_value = 0.46
        elif "punk_leather" in material_name:
            shader.inputs["Roughness"].default_value = 0.34
            shader.inputs["Metallic"].default_value = 0.2
        elif "hair" in material_name:
            shader.inputs["Roughness"].default_value = 0.44
        elif "cornea" in material_name or "eye_" in material_name:
            shader.inputs["Roughness"].default_value = 0.2
        if opacity is not None:
            opacity_node = tree.nodes.new("ShaderNodeTexImage")
            opacity_node.image = opacity
            opacity_node.image.colorspace_settings.name = "Non-Color"
            tree.links.new(opacity_node.outputs["Color"], shader.inputs["Alpha"])
            if hasattr(material, "surface_render_method"):
                material.surface_render_method = "DITHERED"
            elif hasattr(material, "blend_method"):
                material.blend_method = "HASHED"


def _remove_unused_material_slots() -> None:
    """Remove material references made empty by portrait-only geometry trimming."""
    for item in bpy.context.scene.objects:
        if item.type != "MESH":
            continue
        _activate_only(item)
        bpy.ops.object.material_slot_remove_unused()
        bpy.ops.object.select_all(action="DESELECT")


def _remove_unused_objects() -> None:
    """Keep the presenter and rig while dropping lower-body wardrobe variants."""
    for item in list(bpy.context.scene.objects):
        if item.type in {"CAMERA", "LIGHT"}:
            bpy.data.objects.remove(item, do_unlink=True)
        elif item.type == "MESH" and item.name not in KEEP_OBJECTS:
            bpy.data.objects.remove(item, do_unlink=True)


def _control_names() -> set[str]:
    """Collect all canonical controls prepared for GLB export."""
    return {
        key.name
        for item in bpy.context.scene.objects
        if item.type == "MESH" and item.data.shape_keys is not None
        for key in list(item.data.shape_keys.key_blocks)[1:]
    }


def main() -> None:
    """Import, retarget, optimize, validate, and export one CC3 presenter."""
    source, destination = _arguments()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    result = bpy.ops.import_scene.fbx(filepath=str(source), use_anim=False)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not import the source character")
    face = _prepare_body()
    _map_shapes(
        face,
        tuple(
            name for name in FACE_CONTROLS if name != "tongueOut" and not name.startswith("eyeLook")
        ),
    )
    for name in ("CC_Base_EyeOcclusion", "CC_Base_TearLine"):
        item = bpy.data.objects.get(name)
        if item is not None:
            _map_shapes(item, EYE_CONTROLS)
    brow = bpy.data.objects.get("Camila_Brow")
    if brow is not None:
        _map_shapes(brow, BROW_CONTROLS)
    tongue = bpy.data.objects.get("CC_Base_Tongue")
    if tongue is not None:
        _map_shapes(tongue, ("tongueOut",))
    _remove_unused_objects()
    for name in ("Crop_T_Shirt", "Punk_Leather_Jacket"):
        wardrobe = bpy.data.objects.get(name)
        if wardrobe is not None:
            _delete_vertices(wardrobe, below=90.0)
    _remove_unused_material_slots()
    _simplify_materials()
    for item in list(bpy.context.scene.objects):
        if item.type != "MESH":
            continue
        if item.name in {"CC_Base_Body", "CC_Base_Teeth"}:
            _decimate(item, 0.55)
        elif item.data.shape_keys is None:
            _decimate(item, 0.38)
    _optimize_images()
    bpy.data.orphans_purge(do_recursive=True)

    missing = sorted(set(FACE_CONTROLS).difference(_control_names()))
    if missing:
        raise RuntimeError("Converted avatar is missing controls: " + ", ".join(missing))
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = bpy.ops.export_scene.gltf(
        filepath=str(destination),
        export_format="GLB",
        export_yup=True,
        export_morph=True,
        export_morph_normal=False,
        export_morph_tangent=False,
        export_animations=False,
        export_cameras=False,
        export_lights=False,
        export_unused_images=False,
        export_image_quality=82,
    )
    if "FINISHED" not in result or not destination.is_file():
        raise RuntimeError("Blender could not export the converted avatar")
    print(f"Converted ARKit-52 presenter: {destination.name} ({destination.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
