import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import {
  applyFrame,
  applyNamed,
  decodeFloat32,
  resetBindings,
  type MorphBinding,
} from "./audio2face-animation";
import {
  calculatePresenterFraming,
  type PresenterFramingMode,
} from "./avatar-framing";
import { inspectAvatar, type AvatarInspection } from "./avatar-inspection";
import { PresenterPoseRig } from "./avatar-pose";
import type { FaceRigAnimation } from "./types";

export type AvatarMode = "3d" | "2d";

const EYE_CONTROL = /^eye(?:Look|Blink|Squint|Wide)/;

function findEyeBounds(root: THREE.Object3D): THREE.Box3 | null {
  const bounds = new THREE.Box3();
  let found = false;
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (mesh.isMesh) {
      const controls = Object.keys(mesh.morphTargetDictionary ?? {});
      const eyeControls = controls.filter((name) => EYE_CONTROL.test(name));
      const normalizedName = mesh.name
        .toLocaleLowerCase()
        .replace(/[^a-z0-9]/g, "");
      const namedEye =
        normalizedName.includes("leye") ||
        normalizedName.includes("reye") ||
        normalizedName.includes("lefteye") ||
        normalizedName.includes("righteye");
      if (
        namedEye ||
        (eyeControls.length >= 2 && eyeControls.length === controls.length)
      ) {
        bounds.union(new THREE.Box3().setFromObject(mesh, true));
        found = true;
      }
    }
  });
  return found ? bounds : null;
}

interface SpeakingAvatarProps {
  mode: AvatarMode;
  available: boolean;
  active: boolean;
  animation: FaceRigAnimation | null;
  startedAt: number;
  mouthLevel: number;
  eyeOffset?: { x: number; y: number };
  compact?: boolean;
  label: string;
  fallbackNotice?: ReactNode;
  avatarId?: string;
  avatarRevision?: string;
}

export function SpeakingAvatar({
  mode,
  available,
  active,
  animation,
  startedAt,
  mouthLevel,
  eyeOffset = { x: 0, y: 0 },
  compact = false,
  label,
  fallbackNotice,
  avatarId = "default",
  avatarRevision = "",
}: SpeakingAvatarProps) {
  const [failure, setFailure] = useState<string | null>(null);
  const handleFailure = useCallback(
    (message: string) => setFailure(message),
    [],
  );
  const use3d = mode === "3d" && available && !failure;
  return (
    <div className={`speaking-avatar-frame ${compact ? "compact" : ""}`}>
      {use3d ? (
        <ThreeFaceAvatar
          key={`${avatarId}:${avatarRevision}`}
          active={active}
          animation={animation}
          startedAt={startedAt}
          compact={compact}
          label={label}
          onFailure={handleFailure}
          avatarId={avatarId}
          avatarRevision={avatarRevision}
        />
      ) : (
        <SvgFaceAvatar
          active={active}
          mouthLevel={mouthLevel}
          eyeOffset={eyeOffset}
          label={label}
          compact={compact}
        />
      )}
      {mode === "3d" && (!available || failure) && (
        <div className="avatar-fallback" role="status">
          <small>{failure ?? fallbackNotice}</small>
          {available && failure && (
            <button
              type="button"
              className="button secondary avatar-retry"
              onClick={() => setFailure(null)}
            >
              Retry 3D
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function SvgFaceAvatar({
  active,
  mouthLevel,
  eyeOffset,
  compact,
  label,
}: {
  active: boolean;
  mouthLevel: number;
  eyeOffset: { x: number; y: number };
  compact: boolean;
  label: string;
}) {
  return (
    <svg
      className={`speech-avatar ${compact ? "compact" : ""} ${active ? "active" : ""}`}
      viewBox="0 0 320 320"
      role="img"
      aria-label={label}
    >
      <circle className="avatar-halo" cx="160" cy="160" r="145" />
      <rect
        className="avatar-head"
        x="65"
        y="55"
        width="190"
        height="210"
        rx="72"
      />
      <circle
        className="avatar-eye"
        cx={125 + eyeOffset.x * 4}
        cy={135 + eyeOffset.y * 3}
        r="12"
      />
      <circle
        className="avatar-eye"
        cx={195 + eyeOffset.x * 4}
        cy={135 + eyeOffset.y * 3}
        r="12"
      />
      <ellipse
        className="avatar-mouth"
        cx="160"
        cy="205"
        rx="32"
        ry={6 + mouthLevel * 22}
      />
    </svg>
  );
}

function ThreeFaceAvatar({
  active,
  animation,
  startedAt,
  compact,
  label,
  onFailure,
  avatarId,
  avatarRevision,
}: {
  active: boolean;
  animation: FaceRigAnimation | null;
  startedAt: number;
  compact: boolean;
  label: string;
  onFailure: (message: string) => void;
  avatarId: string;
  avatarRevision: string;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const state = useRef({ active, animation, startedAt });
  const [debugInfo, setDebugInfo] = useState<
    | (AvatarInspection & {
        framingMode: PresenterFramingMode;
        presenterPoseApplied: boolean;
      })
    | null
  >(null);

  useEffect(() => {
    state.current = { active, animation, startedAt };
  }, [active, animation, startedAt]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const target = element;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    let disposed = false;
    let frameHandle = 0;
    let renderer: THREE.WebGLRenderer | null = null;
    let root: THREE.Object3D | null = null;
    let mixer: THREE.AnimationMixer | null = null;
    let poseRig: PresenterPoseRig | null = null;
    const bindings: MorphBinding[] = [];
    try {
      renderer = createAvatarRenderer(target);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.9;
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFShadowMap;
    } catch (error: unknown) {
      const detail =
        error instanceof Error && error.message.includes("WebGL2")
          ? "WebGL2 is unavailable. Enable Chrome hardware acceleration, restart Chrome, then retry 3D."
          : "WebGL could not start. Close unused 3D tabs, then retry 3D.";
      onFailure(`${detail} The safe 2D avatar remains available.`);
      return;
    }
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b1220);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 100);
    scene.add(new THREE.HemisphereLight(0xb8c7da, 0x101827, 0.38));
    const key = new THREE.DirectionalLight(0xffd8b5, 2.25);
    key.position.set(2.8, 4.2, 4.5);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0001;
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x91b9e8, 0.72);
    fill.position.set(-3.5, 2.2, 3.5);
    scene.add(fill);
    const rim = new THREE.DirectionalLight(0x8dbdff, 1.45);
    rim.position.set(0.8, 3.4, -4.5);
    scene.add(rim);

    function updateFraming() {
      if (!root) return;
      root.updateMatrixWorld(true);
      const modelBounds = new THREE.Box3().setFromObject(root, true);
      if (modelBounds.isEmpty()) throw new Error("avatar has no geometry");
      const aspect = Math.max(0.5, target.clientWidth / target.clientHeight);
      const framingMode: PresenterFramingMode = compact ? "compact" : "normal";
      const framing = calculatePresenterFraming(
        modelBounds,
        findEyeBounds(root),
        framingMode,
        aspect,
      );
      const halfHeight = framing.viewHeight * 0.5;
      const halfWidth = halfHeight * aspect;
      camera.left = -halfWidth;
      camera.right = halfWidth;
      camera.top = halfHeight;
      camera.bottom = -halfHeight;
      camera.near = framing.near;
      camera.far = framing.far;
      camera.position.set(
        framing.target.x,
        framing.target.y,
        framing.target.z + framing.cameraDistance,
      );
      camera.lookAt(framing.target);
      camera.updateProjectionMatrix();
    }

    function resize() {
      if (!renderer) return;
      const width = Math.max(1, target.clientWidth);
      const height = Math.max(1, target.clientHeight);
      renderer.setSize(width, height, false);
      updateFraming();
    }
    const observer = new ResizeObserver(resize);
    observer.observe(target);
    resize();

    const avatarPath =
      avatarId === "default"
        ? "/api/v1/speech/audio2face/avatar"
        : `/api/v1/speech/audio2face/avatars/${encodeURIComponent(avatarId)}`;
    const avatarUrl = avatarRevision
      ? `${avatarPath}?revision=${encodeURIComponent(avatarRevision)}`
      : avatarPath;
    void new GLTFLoader()
      .loadAsync(avatarUrl)
      .then((gltf) => {
        if (disposed) {
          disposeObject(gltf.scene);
          return;
        }
        root = gltf.scene;
        const inspection = inspectAvatar(root);
        if (!inspection.hasUsableMaterials) {
          throw new Error("avatar asset has no usable materials or textures");
        }
        root.traverse((object) => {
          const mesh = object as THREE.Mesh;
          if (mesh.isMesh) {
            mesh.castShadow = true;
            mesh.receiveShadow = true;
          }
          if (
            mesh.isMesh &&
            mesh.morphTargetDictionary &&
            mesh.morphTargetInfluences
          ) {
            bindings.push({
              dictionary: mesh.morphTargetDictionary,
              influences: mesh.morphTargetInfluences,
            });
          }
        });
        if (!bindings.length)
          throw new Error("avatar has no renderable morph targets");
        poseRig = new PresenterPoseRig(root);
        poseRig.update(
          performance.now(),
          state.current.active ? "explain" : "idle",
          reducedMotion,
        );
        if (gltf.animations.length) mixer = new THREE.AnimationMixer(root);
        scene.add(root);
        updateFraming();
        if (import.meta.env.DEV) {
          setDebugInfo({
            ...inspection,
            framingMode: compact ? "compact" : "normal",
            presenterPoseApplied: poseRig.applied,
          });
        }
      })
      .catch((error: unknown) => {
        if (disposed) return;
        const message =
          error instanceof Error ? error.message : "3D rendering failed";
        onFailure(
          message.includes("no usable materials")
            ? "Avatar asset has no usable materials or textures; using the safe 2D avatar."
            : "3D rendering failed; using the safe 2D avatar.",
        );
      });

    let decodedKey = "";
    let decoded: Float32Array<ArrayBufferLike> = new Float32Array();
    const render = (now: number) => {
      if (disposed || !renderer) return;
      const current = state.current;
      poseRig?.update(now, current.active ? "explain" : "idle", reducedMotion);
      if (root && current.active && current.animation) {
        if (decodedKey !== current.animation.weights_base64) {
          decodedKey = current.animation.weights_base64;
          decoded = decodeFloat32(decodedKey);
        }
        applyFrame(
          bindings,
          current.animation,
          decoded,
          Math.max(0, now - current.startedAt) / 1000,
        );
      } else if (root) {
        resetBindings(bindings);
        const blinkPhase = now % 5100;
        if (!reducedMotion && blinkPhase < 170) {
          const blink = Math.sin((blinkPhase / 170) * Math.PI) * 0.88;
          applyNamed(bindings, "eyeBlinkLeft", blink);
          applyNamed(bindings, "eyeBlinkRight", blink);
        }
      }
      renderer.render(scene, camera);
      frameHandle = requestAnimationFrame(render);
    };
    frameHandle = requestAnimationFrame(render);
    return () => {
      disposed = true;
      cancelAnimationFrame(frameHandle);
      observer.disconnect();
      mixer?.stopAllAction();
      if (root) mixer?.uncacheRoot(root);
      poseRig?.dispose();
      if (root) disposeObject(root);
      scene.clear();
      renderer?.setRenderTarget(null);
      renderer?.renderLists.dispose();
      renderer?.dispose();
    };
  }, [avatarId, avatarRevision, compact, onFailure]);

  return (
    <>
      <canvas
        ref={canvas}
        className="audio2face-canvas"
        role="img"
        aria-label={label}
      />
      {import.meta.env.DEV && debugInfo && (
        <AvatarDebugPanel information={debugInfo} />
      )}
    </>
  );
}

function createAvatarRenderer(canvas: HTMLCanvasElement): THREE.WebGLRenderer {
  const preferred: WebGLContextAttributes = {
    alpha: false,
    depth: true,
    stencil: true,
    antialias: true,
    premultipliedAlpha: true,
    preserveDrawingBuffer: false,
    powerPreference: "high-performance",
    failIfMajorPerformanceCaveat: false,
  };
  const compatible: WebGLContextAttributes = {
    ...preferred,
    stencil: false,
    antialias: false,
    powerPreference: "default",
  };
  const context =
    canvas.getContext("webgl2", preferred) ??
    canvas.getContext("webgl2", compatible);
  if (!context) {
    throw new Error("WebGL2 context is unavailable");
  }
  return new THREE.WebGLRenderer({
    canvas,
    context,
    alpha: false,
    antialias: context.getContextAttributes()?.antialias ?? false,
  });
}

function AvatarDebugPanel({
  information,
}: {
  information: AvatarInspection & {
    framingMode: PresenterFramingMode;
    presenterPoseApplied: boolean;
  };
}) {
  return (
    <details className="avatar-debug-panel">
      <summary>Avatar debug</summary>
      <dl>
        <div>
          <dt>Meshes</dt>
          <dd>{information.meshNames.length}</dd>
        </div>
        <div>
          <dt>Materials / textures</dt>
          <dd>
            {information.materialCount} / {information.textureCount}
          </dd>
        </div>
        <div>
          <dt>Morph targets</dt>
          <dd>{information.morphTargetCount}</dd>
        </div>
        <div>
          <dt>Skeleton bones</dt>
          <dd>{information.skeletonBoneNames.length}</dd>
        </div>
        <div>
          <dt>Framing</dt>
          <dd>{information.framingMode}</dd>
        </div>
        <div>
          <dt>Presenter pose</dt>
          <dd>
            {information.presenterPoseApplied ? "applied" : "unavailable"}
          </dd>
        </div>
      </dl>
      <p title={information.meshNames.join(", ")}>
        Meshes: {information.meshNames.slice(0, 6).join(", ")}
      </p>
      <p title={information.skeletonBoneNames.join(", ")}>
        Bones: {information.skeletonBoneNames.slice(0, 8).join(", ") || "none"}
      </p>
    </details>
  );
}

function disposeObject(root: THREE.Object3D) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();
  root.traverse((object) => {
    const mesh = object as THREE.Mesh;
    if (!mesh.isMesh) return;
    geometries.add(mesh.geometry);
    const meshMaterials = Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material];
    for (const material of meshMaterials) {
      materials.add(material);
      for (const value of Object.values(material)) {
        const texture = value as THREE.Texture | undefined;
        if (texture?.isTexture) textures.add(texture);
      }
    }
    const skinned = mesh as THREE.SkinnedMesh;
    if (skinned.isSkinnedMesh && skinned.skeleton.boneTexture) {
      textures.add(skinned.skeleton.boneTexture);
      skinned.skeleton.dispose();
    }
  });
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}
