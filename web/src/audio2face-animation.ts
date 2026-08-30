import type { FaceRigAnimation } from "./types";

export interface MorphBinding {
  influences: number[];
  dictionary: Record<string, number>;
}

export function decodeFloat32(value: string): Float32Array {
  const raw = window.atob(value);
  if (raw.length % 4) throw new Error("Invalid facial-animation data");
  const bytes = Uint8Array.from(raw, (character) => character.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const values = new Float32Array(raw.length / 4);
  for (let index = 0; index < values.length; index += 1) {
    values[index] = view.getFloat32(index * 4, true);
  }
  return values;
}

export function applyFrame(
  bindings: MorphBinding[],
  animation: FaceRigAnimation,
  values: Float32Array,
  elapsedSeconds: number,
) {
  const names = [...animation.face_controls, ...animation.tongue_controls];
  const width = names.length;
  if (!width || values.length !== animation.frame_count * width) return;
  const position = Math.min(
    Math.max(0, elapsedSeconds * animation.fps),
    animation.frame_count - 1,
  );
  const lower = Math.floor(position);
  const upper = Math.min(lower + 1, animation.frame_count - 1);
  const mix = position - lower;
  resetBindings(bindings);
  names.forEach((name, control) => {
    const first = values[lower * width + control] ?? 0;
    const second = values[upper * width + control] ?? first;
    applyNamed(bindings, name, first + (second - first) * mix);
  });
}

export function applyNamed(
  bindings: MorphBinding[],
  name: string,
  value: number,
) {
  for (const binding of bindings) {
    const index = binding.dictionary[name];
    if (index !== undefined)
      binding.influences[index] = Math.max(0, Math.min(1, value));
  }
}

export function resetBindings(bindings: MorphBinding[]) {
  for (const binding of bindings) binding.influences.fill(0);
}
