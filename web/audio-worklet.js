// Resample the actual device rate to 16 kHz, then send bounded PCM16 frames.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.samples = [];
    this.position = 0;
    this.ratio = sampleRate / 16000;
    this.frame = new Int16Array(512);
    this.offset = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    this.samples.push(...input);
    while (this.position + this.ratio < this.samples.length) {
      // Area averaging gives a low-pass box filter before downsampling.
      const end = this.position + this.ratio;
      let sum = 0;
      let cursor = this.position;
      while (cursor < end) {
        const index = Math.floor(cursor);
        const next = Math.min(index + 1, end);
        sum += this.samples[index] * (next - cursor);
        cursor = next;
      }
      const value = Math.max(-1, Math.min(1, sum / this.ratio));
      this.frame[this.offset++] = value < 0 ? value * 32768 : value * 32767;
      if (this.offset === 512) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(512);
        this.offset = 0;
      }
      this.position = end;
    }
    const used = Math.floor(this.position);
    this.samples.splice(0, used);
    this.position -= used;
    return true;
  }
}
registerProcessor("local-pcm-capture", CaptureProcessor);
