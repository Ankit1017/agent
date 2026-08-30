import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { PresenterPoseRig, discoverPresenterBones } from "./avatar-pose";

function arm(
  root: THREE.Object3D,
  names: readonly [string, string, string],
  rotation: number,
) {
  const upper = new THREE.Bone();
  upper.name = names[0];
  upper.quaternion.setFromAxisAngle(new THREE.Vector3(0, 0, 1), rotation);
  const forearm = new THREE.Bone();
  forearm.name = names[1];
  forearm.position.y = 1;
  const hand = new THREE.Bone();
  hand.name = names[2];
  hand.position.y = 1;
  forearm.add(hand);
  upper.add(forearm);
  root.add(upper);
  return { upper, forearm, hand };
}

function direction(from: THREE.Object3D, to: THREE.Object3D) {
  return to
    .getWorldPosition(new THREE.Vector3())
    .sub(from.getWorldPosition(new THREE.Vector3()))
    .normalize();
}

describe("presenter avatar pose rig", () => {
  it("discovers Mixamo and dotted Blender bone aliases", () => {
    const root = new THREE.Object3D();
    arm(
      root,
      ["mixamorig:LeftArm", "mixamorig:LeftForeArm", "mixamorig:LeftHand"],
      -Math.PI / 2,
    );
    arm(root, ["upper_arm.R", "lower_arm.R", "hand.R"], Math.PI / 2);

    const detected = discoverPresenterBones(root);

    expect(detected.leftUpperArm?.name).toBe("mixamorig:LeftArm");
    expect(detected.leftForearm?.name).toBe("mixamorig:LeftForeArm");
    expect(detected.rightUpperArm?.name).toBe("upper_arm.R");
    expect(detected.rightForearm?.name).toBe("lower_arm.R");
  });

  it("supports relaxed idle and restrained explain poses", () => {
    const root = new THREE.Object3D();
    const left = arm(
      root,
      ["mixamorig:LeftArm", "LeftForeArm", "LeftHand"],
      -Math.PI / 2,
    );
    const right = arm(
      root,
      ["RightUpperArm", "mixamorig:RightForeArm", "RightHand"],
      Math.PI / 2,
    );
    const original = left.upper.quaternion.clone();
    const rig = new PresenterPoseRig(root);

    rig.update(0, "idle", true);
    expect(direction(left.upper, left.forearm).y).toBeLessThan(-0.98);
    expect(direction(right.upper, right.forearm).y).toBeLessThan(-0.98);

    rig.update(500, "explain", true);
    expect(direction(left.forearm, left.hand).z).toBeGreaterThan(0.9);
    expect(direction(right.forearm, right.hand).z).toBeGreaterThan(0.9);

    rig.dispose();
    expect(left.upper.quaternion.angleTo(original)).toBeLessThan(1e-6);
  });

  it("skips body posing safely when compatible bones are absent", () => {
    const rig = new PresenterPoseRig(new THREE.Object3D());
    expect(rig.applied).toBe(false);
    expect(() => rig.update(0, "explain", false)).not.toThrow();
    rig.dispose();
  });
});
