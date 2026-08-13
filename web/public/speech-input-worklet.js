/* global AudioWorkletProcessor, sampleRate, registerProcessor */

class HarnessSpeechInputProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.sourceRate = sampleRate;
    this.targetRate = options.processorOptions?.targetSampleRate ?? 16000;
    this.pending = [];
    this.position = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input?.length) return true;
    const ratio = this.sourceRate / this.targetRate;
    const outputLength = Math.floor((input.length - this.position) / ratio);
    if (outputLength <= 0) {
      this.position = Math.max(0, this.position - input.length);
      return true;
    }
    const pcm = new Int16Array(outputLength);
    for (let index = 0; index < outputLength; index += 1) {
      const sourceIndex = Math.min(
        input.length - 1,
        Math.floor(this.position + index * ratio),
      );
      const value = Math.max(-1, Math.min(1, input[sourceIndex]));
      pcm[index] = value < 0 ? value * 32768 : value * 32767;
    }
    this.position = this.position + outputLength * ratio - input.length;
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("harness-speech-input", HarnessSpeechInputProcessor);
