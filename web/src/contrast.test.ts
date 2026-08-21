import { describe, expect, it } from "vitest";

function luminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((value) => Number.parseInt(value, 16) / 255)
    .map((value) =>
      value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4,
    );
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(foreground: string, background: string): number {
  const values = [luminance(foreground), luminance(background)].sort(
    (left, right) => right - left,
  );
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe("semantic theme contrast", () => {
  it.each([
    ["dark primary", "#f1f5f9", "#111923", 7],
    ["dark secondary", "#c5d0dc", "#111923", 7],
    ["dark supporting", "#9aa9ba", "#111923", 4.5],
    ["light primary", "#17212b", "#ffffff", 7],
    ["light secondary", "#3f4d5d", "#ffffff", 7],
    ["light supporting", "#5f6f82", "#ffffff", 4.5],
    ["dark accent", "#38bdf8", "#111923", 4.5],
    ["dark action", "#ffffff", "#0e7490", 4.5],
    ["dark success", "#86efac", "#111923", 4.5],
    ["dark warning", "#fcd34d", "#111923", 4.5],
    ["dark error", "#fca5a5", "#111923", 4.5],
    ["light accent", "#0369a1", "#ffffff", 4.5],
    ["light action", "#ffffff", "#075985", 4.5],
    ["light success", "#166534", "#ffffff", 4.5],
    ["light warning", "#854d0e", "#ffffff", 4.5],
    ["light error", "#b91c1c", "#ffffff", 4.5],
  ])("keeps %s text readable", (_name, foreground, background, minimum) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(minimum);
  });
});
