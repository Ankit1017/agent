import { speechInputSocket, speechInputStartFrame } from "./api";
import type { SpeechInputEvent } from "./types";

export class LocalSpeechInputClient {
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private sink: GainNode | null = null;
  private transmitting = false;

  constructor(private readonly onEvent: (event: SpeechInputEvent) => void) {}

  async enable(mode: "wake" | "tap" = "wake"): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    try {
      this.socket = speechInputSocket();
      this.socket.binaryType = "arraybuffer";
      await new Promise<void>((resolve, reject) => {
        if (!this.socket)
          return reject(new Error("Microphone socket is unavailable"));
        this.socket.onopen = () => resolve();
        this.socket.onerror = () =>
          reject(new Error("Microphone connection failed"));
      });
      this.socket.onmessage = (message) => {
        const event = JSON.parse(String(message.data)) as SpeechInputEvent;
        this.onEvent(event);
      };
      this.socket.onclose = (event) => {
        this.transmitting = false;
        if (event.code !== 1000)
          this.onEvent({
            type: "error",
            reason: "Microphone connection closed",
          });
      };
      this.socket.send(speechInputStartFrame(mode));
      this.context = new AudioContext();
      await this.context.audioWorklet.addModule("/speech-input-worklet.js");
      this.source = this.context.createMediaStreamSource(this.stream);
      this.worklet = new AudioWorkletNode(
        this.context,
        "harness-speech-input",
        {
          processorOptions: { targetSampleRate: 16000 },
        },
      );
      this.worklet.port.onmessage = (message: MessageEvent<ArrayBuffer>) => {
        if (this.transmitting && this.socket?.readyState === WebSocket.OPEN)
          this.socket.send(message.data);
      };
      this.source.connect(this.worklet);
      this.sink = this.context.createGain();
      this.sink.gain.value = 0;
      this.worklet.connect(this.sink);
      this.sink.connect(this.context.destination);
      await this.context.resume();
      this.transmitting = true;
    } catch (error) {
      await this.disable();
      throw error;
    }
  }

  beginTap(): void {
    this.sendControl("begin_tap");
    this.transmitting = true;
  }

  finish(): void {
    this.sendControl("finish");
  }

  pause(): void {
    this.transmitting = false;
    this.sendControl("pause");
  }

  rearm(): void {
    this.sendControl("rearm");
    this.transmitting = true;
  }

  cancel(): void {
    this.transmitting = false;
    this.sendControl("cancel");
  }

  async disable(): Promise<void> {
    this.transmitting = false;
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "close" }));
      this.socket.close(1000);
    }
    this.socket = null;
    this.worklet?.disconnect();
    this.sink?.disconnect();
    this.source?.disconnect();
    this.worklet = null;
    this.sink = null;
    this.source = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    await this.context?.close().catch(() => undefined);
    this.context = null;
  }

  private sendControl(type: string): void {
    if (this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify({ type }));
  }
}
