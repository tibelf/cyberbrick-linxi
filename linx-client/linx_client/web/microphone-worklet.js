/* 麦克风转为单声道 16 kHz、16 位、20 ms PCM。未按住说话时只输出静音。 */
class MicrophonePcm extends AudioWorkletProcessor {
  constructor() {
    super();
    this.enabled = false;
    this.input = [];
    this.position = 0;
    this.samples = [];
    this.port.onmessage = event => { this.enabled = event.data.enabled === true; };
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) this.input.push(this.enabled ? sample : 0);
    const step = sampleRate / 16000;
    while (this.position + 1 < this.input.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const value = this.input[index] * (1 - fraction) + this.input[index + 1] * fraction;
      this.samples.push(Math.max(-1, Math.min(1, value)));
      this.position += step;
      if (this.samples.length === 320) {
        const buffer = new ArrayBuffer(640);
        const view = new DataView(buffer);
        this.samples.forEach((sample, i) => view.setInt16(i * 2, sample < 0 ? sample * 32768 : sample * 32767, true));
        this.port.postMessage(buffer, [buffer]);
        this.samples = [];
      }
    }
    const consumed = Math.min(Math.floor(this.position), this.input.length - 1);
    this.input.splice(0, consumed);
    this.position -= consumed;
    return true;
  }
}
registerProcessor('microphone-pcm', MicrophonePcm);
