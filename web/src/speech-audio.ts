export interface PcmMetadata {
  sampleRate: number;
  channels: number;
  sampleWidth: number;
}

export class PcmPlayer {
  private readonly analyser: AnalyserNode;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private stopped = false;
  private trailing: Uint8Array | null = null;

  constructor(
    private readonly context: AudioContext,
    private readonly sampleRate: number,
  ) {
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(context.destination);
  }

  async start(): Promise<void> {
    await this.context.resume();
    this.nextStart = this.context.currentTime + 0.08;
  }

  enqueue(value: Uint8Array): void {
    if (this.stopped || value.byteLength === 0) return;
    let bytes = value;
    if (this.trailing) {
      const joined = new Uint8Array(
        this.trailing.byteLength + value.byteLength,
      );
      joined.set(this.trailing);
      joined.set(value, this.trailing.byteLength);
      bytes = joined;
      this.trailing = null;
    }
    if (bytes.byteLength % 2) {
      this.trailing = bytes.slice(-1);
      bytes = bytes.slice(0, -1);
    }
    if (!bytes.byteLength) return;
    const samples = new Float32Array(bytes.byteLength / 2);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * 2, true) / 32768;
    }
    const buffer = this.context.createBuffer(
      1,
      samples.length,
      this.sampleRate,
    );
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.analyser);
    source.onended = () => this.sources.delete(source);
    this.sources.add(source);
    const startAt = Math.max(this.nextStart, this.context.currentTime + 0.01);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
  }

  level(): number {
    if (this.stopped) return 0;
    const samples = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) {
      const centered = (sample - 128) / 128;
      sum += centered * centered;
    }
    return Math.min(1, Math.sqrt(sum / samples.length) * 3.5);
  }

  async finish(): Promise<void> {
    const milliseconds = Math.max(
      0,
      (this.nextStart - this.context.currentTime) * 1000,
    );
    if (milliseconds) {
      await new Promise<void>((resolve) =>
        window.setTimeout(resolve, milliseconds),
      );
    }
  }

  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // A source may already have ended between iteration and stop.
      }
    }
    this.sources.clear();
    void this.context.close();
  }
}

export function wavBlob(chunks: Uint8Array[], metadata: PcmMetadata): Blob {
  const length = chunks.reduce((total, chunk) => total + chunk.byteLength, 0);
  const output = new ArrayBuffer(44 + length);
  const view = new DataView(output);
  writeAscii(view, 0, "RIFF");
  view.setUint32(4, 36 + length, true);
  writeAscii(view, 8, "WAVE");
  writeAscii(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, metadata.channels, true);
  view.setUint32(24, metadata.sampleRate, true);
  view.setUint32(
    28,
    metadata.sampleRate * metadata.channels * metadata.sampleWidth,
    true,
  );
  view.setUint16(32, metadata.channels * metadata.sampleWidth, true);
  view.setUint16(34, metadata.sampleWidth * 8, true);
  writeAscii(view, 36, "data");
  view.setUint32(40, length, true);
  const target = new Uint8Array(output, 44);
  let offset = 0;
  for (const chunk of chunks) {
    target.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new Blob([output], { type: "audio/wav" });
}

function writeAscii(view: DataView, offset: number, value: string): void {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}
