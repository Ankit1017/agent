import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as THREE from "three";
import { SpeakingAvatar } from "./Audio2FaceAvatar";
import { applyFrame, decodeFloat32 } from "./audio2face-animation";
import { calculatePresenterFraming } from "./avatar-framing";
import { inspectAvatar } from "./avatar-inspection";
import type { FaceRigAnimation } from "./types";

function packed(values: number[]): string {
  const bytes = new Uint8Array(values.length * 4);
  const view = new DataView(bytes.buffer);
  values.forEach((value, index) => view.setFloat32(index * 4, value, true));
  return window.btoa(String.fromCharCode(...bytes));
}

describe("Audio2Face avatar presentation", () => {
  it("frames a full-body model around its eyes and upper torso", () => {
    const model = new THREE.Box3(
      new THREE.Vector3(-0.8, 0, -0.15),
      new THREE.Vector3(0.8, 1.75, 0.2),
    );
    const eyes = new THREE.Box3(
      new THREE.Vector3(-0.05, 1.57, 0.1),
      new THREE.Vector3(0.05, 1.62, 0.15),
    );

    const framing = calculatePresenterFraming(model, eyes, "normal", 1.2);

    expect(framing.viewHeight).toBeCloseTo(0.805);
    expect(framing.target.x).toBeCloseTo(0);
    expect(framing.target.y).toBeCloseTo(1.4984);
    expect(framing.eyeVerticalRatio).toBe(0.38);
    expect(framing.cameraDistance).toBeGreaterThan(2);
  });

  it("uses a tighter head-and-shoulders frame in compact mode", () => {
    const model = new THREE.Box3(
      new THREE.Vector3(-0.12, 1.4, -0.1),
      new THREE.Vector3(0.12, 1.7, 0.12),
    );
    const eyes = new THREE.Box3(
      new THREE.Vector3(-0.05, 1.57, 0.08),
      new THREE.Vector3(0.05, 1.62, 0.12),
    );

    const normal = calculatePresenterFraming(model, eyes, "normal", 1);
    const compact = calculatePresenterFraming(model, eyes, "compact", 1);

    expect(normal.viewHeight).toBeCloseTo(0.324);
    expect(compact.viewHeight).toBeCloseTo(0.276);
    expect(compact.viewHeight).toBeLessThan(normal.viewHeight);
    expect(compact.eyeVerticalRatio).toBe(0.38);
  });

  it("detects textures instead of accepting an untextured white material", () => {
    const root = new THREE.Object3D();
    const plain = new THREE.Mesh(
      new THREE.BoxGeometry(),
      new THREE.MeshStandardMaterial({ color: 0xffffff }),
    );
    plain.name = "Face";
    root.add(plain);
    expect(inspectAvatar(root).hasUsableMaterials).toBe(false);

    const texture = new THREE.Texture();
    plain.material.map = texture;
    plain.material.needsUpdate = true;
    expect(inspectAvatar(root).hasUsableMaterials).toBe(true);
    plain.geometry.dispose();
    plain.material.dispose();
    texture.dispose();
  });

  it("decodes little-endian weights and interpolates matching morphs", () => {
    const encoded = packed([0, 1, 1, 0]);
    const values = decodeFloat32(encoded);
    const influences = [0, 0];
    const animation: FaceRigAnimation = {
      encoding: "float32-le-frame-major",
      fps: 1,
      frame_count: 2,
      face_controls: ["jawOpen", "eyeBlinkLeft"],
      tongue_controls: [],
      weights_base64: encoded,
    };

    applyFrame(
      [{ dictionary: { jawOpen: 0, eyeBlinkLeft: 1 }, influences }],
      animation,
      values,
      0.5,
    );

    expect(Array.from(values)).toEqual([0, 1, 1, 0]);
    expect(influences).toEqual([0.5, 0.5]);
  });

  it("uses the accessible 2D fallback when the fixed avatar is unavailable", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    render(
      <SpeakingAvatar
        mode="3d"
        available={false}
        active={false}
        animation={null}
        startedAt={0}
        mouthLevel={0}
        label="Avatar idle"
        fallbackNotice="Install a licensed avatar."
      />,
    );

    expect(screen.getByRole("img", { name: "Avatar idle" })).toHaveClass(
      "speech-avatar",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Install a licensed avatar.",
    );
  });
});
