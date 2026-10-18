// Captures mic audio at the device rate and emits 100 ms chunks of 16 kHz PCM16.
// Downsampling averages each output sample's source window (cheap low-pass, no aliasing spikes).
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0; this.sum = 0; this.n = 0;
    this.out = new Int16Array(1600); this.pos = 0;
    this.level = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = ch[i];
      this.level = Math.max(this.level * 0.9995, Math.abs(s));
      this.sum += s; this.n++; this.acc += 1;
      if (this.acc >= this.ratio) {
        const v = Math.max(-1, Math.min(1, this.sum / this.n));
        this.out[this.pos++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.acc -= this.ratio; this.sum = 0; this.n = 0;
        if (this.pos === this.out.length) {
          this.port.postMessage({ pcm: this.out.buffer, level: this.level }, [this.out.buffer]);
          this.out = new Int16Array(1600); this.pos = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PcmCapture);
