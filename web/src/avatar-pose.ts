import * as THREE from "three";

export type PresenterPosePreset = "idle" | "explain";

type BoneSlot =
  | "leftShoulder"
  | "leftUpperArm"
  | "leftForearm"
  | "leftHand"
  | "rightShoulder"
  | "rightUpperArm"
  | "rightForearm"
  | "rightHand"
  | "spine"
  | "chest"
  | "neck"
  | "head";

const ALIASES: Record<BoneSlot, readonly string[]> = {
  leftShoulder: [
    "leftshoulder",
    "leftclavicle",
    "lclavicle",
    "claviclel",
    "shoulderl",
  ],
  leftUpperArm: ["leftupperarm", "leftarm", "lupperarm", "upperarml", "uparml"],
  leftForearm: [
    "leftforearm",
    "leftlowerarm",
    "lforearm",
    "lowerarml",
    "forearml",
  ],
  leftHand: ["lefthand", "leftwrist", "lhand", "handl", "wristl"],
  rightShoulder: [
    "rightshoulder",
    "rightclavicle",
    "rclavicle",
    "clavicler",
    "shoulderr",
  ],
  rightUpperArm: [
    "rightupperarm",
    "rightarm",
    "rupperarm",
    "upperarmr",
    "uparmr",
  ],
  rightForearm: [
    "rightforearm",
    "rightlowerarm",
    "rforearm",
    "lowerarmr",
    "forearmr",
  ],
  rightHand: ["righthand", "rightwrist", "rhand", "handr", "wristr"],
  spine: ["spine", "spine1", "spine01", "lowerchest"],
  chest: ["upperchest", "chest", "spine2", "spine02", "spine3"],
  neck: ["neck", "neck1", "necktwist01", "necktwist02"],
  head: ["head"],
};

interface BoneTransform {
  quaternion: THREE.Quaternion;
  position: THREE.Vector3;
  scale: THREE.Vector3;
}

interface PoseTransition {
  startedAt: number;
  duration: number;
  start: Map<string, THREE.Quaternion>;
  end: Map<string, THREE.Quaternion>;
}

function normalizedBoneName(name: string): string {
  return name.toLocaleLowerCase().replace(/[^a-z0-9]/g, "");
}

function matchesAlias(name: string, aliases: readonly string[]): boolean {
  const normalized = normalizedBoneName(name);
  return aliases.some(
    (alias) => normalized === alias || normalized.endsWith(alias),
  );
}

/** Discover supported presenter bones without depending on one rig convention. */
export function discoverPresenterBones(
  root: THREE.Object3D,
): Partial<Record<BoneSlot, THREE.Bone>> {
  const bones: THREE.Bone[] = [];
  root.traverse((object) => {
    const bone = object as THREE.Bone;
    if (bone.isBone) bones.push(bone);
  });
  const detected: Partial<Record<BoneSlot, THREE.Bone>> = {};
  for (const slot of Object.keys(ALIASES) as BoneSlot[]) {
    detected[slot] = bones.find((bone) =>
      matchesAlias(bone.name, ALIASES[slot]),
    );
  }
  return detected;
}

function orientToward(
  root: THREE.Object3D,
  bone: THREE.Bone | undefined,
  child: THREE.Bone | undefined,
  desiredDirection: THREE.Vector3,
): boolean {
  if (!bone || !child || !bone.parent) return false;
  root.updateMatrixWorld(true);
  const start = bone.getWorldPosition(new THREE.Vector3());
  const end = child.getWorldPosition(new THREE.Vector3());
  const currentDirection = end.sub(start);
  if (currentDirection.lengthSq() < 1e-8) return false;
  currentDirection.normalize();
  const correction = new THREE.Quaternion().setFromUnitVectors(
    currentDirection,
    desiredDirection.clone().normalize(),
  );
  const desiredWorld = correction.multiply(
    bone.getWorldQuaternion(new THREE.Quaternion()),
  );
  const parentInverse = bone.parent
    .getWorldQuaternion(new THREE.Quaternion())
    .invert();
  bone.quaternion.copy(parentInverse.multiply(desiredWorld)).normalize();
  root.updateMatrixWorld(true);
  return true;
}

/** Blend a compatible humanoid rig between restrained presenter poses. */
export class PresenterPoseRig {
  readonly detectedBoneNames: readonly string[];
  readonly applied: boolean;
  private readonly bones: Partial<Record<BoneSlot, THREE.Bone>>;
  private readonly originals = new Map<string, BoneTransform>();
  private readonly targets: Record<
    PresenterPosePreset,
    Map<string, THREE.Quaternion>
  >;
  private transition: PoseTransition | null = null;
  private preset: PresenterPosePreset | null = null;

  constructor(private readonly root: THREE.Object3D) {
    this.bones = discoverPresenterBones(root);
    const unique = new Map<string, THREE.Bone>();
    for (const bone of Object.values(this.bones)) {
      if (bone) unique.set(bone.uuid, bone);
    }
    for (const bone of unique.values()) {
      this.originals.set(bone.uuid, {
        quaternion: bone.quaternion.clone(),
        position: bone.position.clone(),
        scale: bone.scale.clone(),
      });
    }
    this.detectedBoneNames = [...unique.values()]
      .map((bone) => bone.name)
      .sort();
    this.targets = {
      idle: this.buildTarget("idle"),
      explain: this.buildTarget("explain"),
    };
    this.restoreOriginals();
    this.applied = Boolean(
      this.bones.leftUpperArm || this.bones.rightUpperArm || this.bones.spine,
    );
  }

  /** Blend into one fixed presenter preset and add bounded idle motion. */
  update(
    now: number,
    preset: PresenterPosePreset,
    reducedMotion: boolean,
  ): void {
    if (this.preset !== preset) {
      const start = new Map<string, THREE.Quaternion>();
      for (const [uuid, original] of this.originals) {
        const bone = this.boneByUuid(uuid);
        start.set(
          uuid,
          bone?.quaternion.clone() ?? original.quaternion.clone(),
        );
      }
      this.transition = {
        startedAt: now,
        duration: reducedMotion ? 0 : 300,
        start,
        end: this.targets[preset],
      };
      this.preset = preset;
    }

    const transition = this.transition;
    if (transition) {
      const raw =
        transition.duration === 0
          ? 1
          : Math.min(
              1,
              Math.max(0, (now - transition.startedAt) / transition.duration),
            );
      const amount = raw * raw * (3 - 2 * raw);
      for (const [uuid, end] of transition.end) {
        const bone = this.boneByUuid(uuid);
        const start = transition.start.get(uuid);
        if (bone && start) bone.quaternion.copy(start).slerp(end, amount);
      }
      if (raw >= 1) this.transition = null;
    } else {
      for (const [uuid, end] of this.targets[preset]) {
        this.boneByUuid(uuid)?.quaternion.copy(end);
      }
    }

    this.applyIdleMotion(now, preset, reducedMotion);
    this.root.updateMatrixWorld(true);
  }

  /** Restore the imported GLB pose before disposal. */
  dispose(): void {
    this.restoreOriginals();
  }

  private buildTarget(
    preset: PresenterPosePreset,
  ): Map<string, THREE.Quaternion> {
    this.restoreOriginals();
    const torso = this.bones.chest ?? this.bones.spine;
    torso?.quaternion.multiply(
      new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0),
        THREE.MathUtils.degToRad(-4),
      ),
    );
    this.bones.head?.quaternion.multiply(
      new THREE.Quaternion().setFromAxisAngle(
        new THREE.Vector3(0, 1, 0),
        THREE.MathUtils.degToRad(4),
      ),
    );
    this.root.updateMatrixWorld(true);

    this.poseSide("left", preset);
    this.poseSide("right", preset);
    const target = new Map<string, THREE.Quaternion>();
    for (const uuid of this.originals.keys()) {
      const bone = this.boneByUuid(uuid);
      if (bone) target.set(uuid, bone.quaternion.clone());
    }
    return target;
  }

  private poseSide(side: "left" | "right", preset: PresenterPosePreset): void {
    const sign = side === "left" ? 1 : -1;
    const upper = this.bones[`${side}UpperArm`];
    const forearm = this.bones[`${side}Forearm`];
    const hand = this.bones[`${side}Hand`];
    const upperDirection =
      preset === "idle"
        ? new THREE.Vector3(sign * 0.12, -1, 0.05)
        : new THREE.Vector3(sign * 0.42, -0.7, 0.58);
    const forearmDirection =
      preset === "idle"
        ? new THREE.Vector3(sign * 0.06, -1, 0.03)
        : new THREE.Vector3(-sign * 0.18, 0.24, 0.95);
    orientToward(this.root, upper, forearm, upperDirection);
    orientToward(this.root, forearm, hand, forearmDirection);
    if (hand && preset === "explain") {
      hand.quaternion.multiply(
        new THREE.Quaternion().setFromEuler(
          new THREE.Euler(
            THREE.MathUtils.degToRad(-8),
            THREE.MathUtils.degToRad(sign * 8),
            THREE.MathUtils.degToRad(sign * 6),
          ),
        ),
      );
    }
    this.root.updateMatrixWorld(true);
  }

  private applyIdleMotion(
    now: number,
    preset: PresenterPosePreset,
    reducedMotion: boolean,
  ): void {
    for (const bone of [this.bones.spine, this.bones.chest]) {
      if (!bone) continue;
      const original = this.originals.get(bone.uuid);
      if (original) bone.scale.copy(original.scale);
    }
    if (reducedMotion || preset !== "idle") return;
    const breathing = 1 + Math.sin(now / 1150) * 0.003;
    const chest = this.bones.chest ?? this.bones.spine;
    const chestOriginal = chest ? this.originals.get(chest.uuid) : undefined;
    if (chest && chestOriginal) {
      chest.scale.set(
        chestOriginal.scale.x * breathing,
        chestOriginal.scale.y * breathing,
        chestOriginal.scale.z * breathing,
      );
    }
    const head = this.bones.head;
    if (head) {
      head.quaternion.multiply(
        new THREE.Quaternion().setFromAxisAngle(
          new THREE.Vector3(0, 1, 0),
          Math.sin(now / 1800) * THREE.MathUtils.degToRad(0.45),
        ),
      );
    }
  }

  private restoreOriginals(): void {
    for (const [uuid, original] of this.originals) {
      const bone = this.boneByUuid(uuid);
      if (!bone) continue;
      bone.quaternion.copy(original.quaternion);
      bone.position.copy(original.position);
      bone.scale.copy(original.scale);
    }
    this.root.updateMatrixWorld(true);
  }

  private boneByUuid(uuid: string): THREE.Bone | undefined {
    return Object.values(this.bones).find((bone) => bone?.uuid === uuid);
  }
}
