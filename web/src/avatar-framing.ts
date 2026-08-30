import * as THREE from "three";

export type PresenterFramingMode = "normal" | "compact";

export interface PresenterFraming {
  target: THREE.Vector3;
  viewHeight: number;
  cameraDistance: number;
  near: number;
  far: number;
  eyeVerticalRatio: number | null;
}

/** Calculate scale-independent orthographic presenter framing. */
export function calculatePresenterFraming(
  modelBounds: THREE.Box3,
  eyeBounds: THREE.Box3 | null,
  mode: PresenterFramingMode,
  aspect: number,
): PresenterFraming {
  const modelSize = modelBounds.getSize(new THREE.Vector3());
  const modelCenter = modelBounds.getCenter(new THREE.Vector3());
  const modelHeight = Math.max(modelSize.y, 0.1);
  const safeAspect = Math.max(aspect, 0.5);
  let viewHeight = modelHeight * (mode === "compact" ? 0.36 : 0.5);
  const target = new THREE.Vector3(
    modelCenter.x,
    modelBounds.max.y - viewHeight * 0.5,
    modelCenter.z,
  );
  let eyeVerticalRatio: number | null = null;

  if (eyeBounds && !eyeBounds.isEmpty()) {
    const eyeSize = eyeBounds.getSize(new THREE.Vector3());
    const eyeCenter = eyeBounds.getCenter(new THREE.Vector3());
    const eyeSpan = Math.max(eyeSize.x, 0.01);
    const fullBody = modelHeight > eyeSpan * 8;
    if (fullBody) {
      viewHeight = modelHeight * (mode === "compact" ? 0.31 : 0.46);
    } else {
      viewHeight = modelHeight * (mode === "compact" ? 0.92 : 1.08);
    }
    viewHeight = Math.max(viewHeight, (eyeSpan * 2.7) / safeAspect);
    const desiredEyeRatio = 0.38;
    target.set(
      eyeCenter.x,
      eyeCenter.y - (0.5 - desiredEyeRatio) * viewHeight,
      eyeCenter.z,
    );
    eyeVerticalRatio = desiredEyeRatio;
  }

  const radius = Math.max(modelSize.length() * 0.5, viewHeight * 0.5, 0.1);
  const cameraDistance = radius * 3;
  return {
    target,
    viewHeight,
    cameraDistance,
    near: Math.max(0.001, cameraDistance - radius * 2.25),
    far: cameraDistance + radius * 2.25,
    eyeVerticalRatio,
  };
}
