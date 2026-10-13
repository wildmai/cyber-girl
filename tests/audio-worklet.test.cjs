const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../web/audio-worklet.js"), "utf8");

for (const sampleRate of [16000, 44100, 48000]) {
  let Processor;
  const frames = [];
  const context = {
    sampleRate,
    AudioWorkletProcessor: class {
      constructor() { this.port = { postMessage: (buffer) => frames.push(new Int16Array(buffer)) }; }
    },
    registerProcessor: (name, processor) => { assert.equal(name, "local-pcm-capture"); Processor = processor; },
  };
  vm.runInNewContext(source, context);
  const processor = new Processor();
  // A second of constant input must remain at the same amplitude at each device rate.
  for (let offset = 0; offset < sampleRate; offset += 128) {
    processor.process([[new Float32Array(Math.min(128, sampleRate - offset)).fill(0.5)]]);
  }
  assert.equal(frames.length, 31, `Unexpected frame count at ${sampleRate} Hz`);
  for (const frame of frames) {
    assert.equal(frame.length, 512);
    assert.equal(frame.byteLength, 1024);
    assert.ok(frame.every((sample) => sample === 16383));
  }
  assert.ok(processor.samples.length <= 4, "Resampling buffer grew without a bound");
}
console.log("PASS: PCM16 framing and 16 / 44.1 / 48 kHz capture resampling");
