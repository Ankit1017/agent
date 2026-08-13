import { describe, expect, it, vi } from "vitest";
import { PcmPlayer, wavBlob } from "./speech-audio";

function audioContext() {
  const starts: number[] = [];
  const analyser = {
    fftSize: 0,
    connect: vi.fn(),
    getByteTimeDomainData: (value: Uint8Array) => value.fill(128),
  };
  const context = {
    currentTime: 2,
    destination: {},
    resume: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    createAnalyser: () => analyser,
    createBuffer: (_channels: number, length: number, sampleRate: number) => ({
      duration: length / sampleRate,
      copyToChannel: vi.fn(),
    }),
    createBufferSource: () => ({
      buffer: null,
      connect: vi.fn(),
      start: (at: number) => starts.push(at),
      stop: vi.fn(),
      onended: null,
    }),
  };
  return { context: context as unknown as AudioContext, starts };
}

describe("streaming PCM audio", () => {
  it("buffers briefly and schedules adjacent chunks", async () => {
    const { context, starts } = audioContext();
    const player = new PcmPlayer(context, 1_000);
    await player.start();
    player.enqueue(new Uint8Array([0, 0, 1, 0]));
    player.enqueue(new Uint8Array([2, 0, 3, 0]));
    expect(starts[0]).toBeCloseTo(2.08);
    expect(starts[1]).toBeCloseTo(2.082);
    expect(player.level()).toBe(0);
    player.stop();
  });

  it("creates a valid little-endian PCM WAV in the browser", async () => {
    const blob = wavBlob([new Uint8Array([1, 0, 2, 0])], {
      sampleRate: 22_050,
      channels: 1,
      sampleWidth: 2,
    });
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(reader.error);
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.readAsArrayBuffer(blob);
    });
    const bytes = new Uint8Array(buffer);
    expect(new TextDecoder().decode(bytes.slice(0, 4))).toBe("RIFF");
    expect(new DataView(bytes.buffer).getUint32(24, true)).toBe(22_050);
    expect(bytes.slice(44)).toEqual(new Uint8Array([1, 0, 2, 0]));
  });
});
