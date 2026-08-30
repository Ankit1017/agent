import * as THREE from "three";

export interface AvatarInspection {
  meshNames: readonly string[];
  materialCount: number;
  textureCount: number;
  morphTargetCount: number;
  skeletonBoneNames: readonly string[];
  hasUsableMaterials: boolean;
}

/** Inspect rendered GLB resources without changing imported materials. */
export function inspectAvatar(root: THREE.Object3D): AvatarInspection {
  const meshNames = new Set<string>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  const morphTargets = new Set<string>();
  const skeletonBoneNames = new Set<string>();

  root.traverse((object) => {
    const bone = object as THREE.Bone;
    if (bone.isBone) skeletonBoneNames.add(bone.name || "Unnamed bone");
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    meshNames.add(mesh.name || "Unnamed mesh");
    for (const name of Object.keys(mesh.morphTargetDictionary ?? {})) {
      morphTargets.add(name);
    }
    const meshMaterials = Array.isArray(mesh.material)
      ? mesh.material
      : mesh.material
        ? [mesh.material]
        : [];
    for (const material of meshMaterials) {
      materials.add(material);
      for (const value of Object.values(material)) {
        const texture = value as THREE.Texture | undefined;
        if (texture?.isTexture) textures.add(texture);
      }
    }
  });

  return {
    meshNames: [...meshNames].sort(),
    materialCount: materials.size,
    textureCount: textures.size,
    morphTargetCount: morphTargets.size,
    skeletonBoneNames: [...skeletonBoneNames].sort(),
    hasUsableMaterials: materials.size > 0 && textures.size > 0,
  };
}
