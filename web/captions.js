// Media clocks drive local captions. RTC uses audible 200 ms samples because
// Vidu's direct TTS protocol does not supply word timestamps or a duration.
export class PlaybackCaptions {
  constructor(render) {
    this.render = render;
    this.reset();
  }

  reset() {
    clearInterval(this.timer);
    this.timer = null;
    this.prefix = '';
    this.active = null;
    this.pending = [];
  }

  segment(text, duration) {
    const chars = Array.from(text || '');
    let weight = 0;
    const ends = chars.map(char => {
      weight += /[\s\p{P}\p{S}]/u.test(char) ? 0.5 : /[a-z]/i.test(char) ? 0.25 : 1;
      return weight;
    });
    return { chars, ends, weight, duration, elapsed: 0, shown: 0 };
  }

  show(seconds) {
    const part = this.active;
    if (!part || seconds < 0 || !part.chars.length) return;
    // Keep the last characters pending until RTC confirms the reply ended.
    const fraction = part.duration ? Math.min(1, seconds / part.duration)
      : Math.min(0.95, seconds * 5 / part.weight);
    const budget = fraction * part.weight;
    let count = Math.max(1, part.shown);
    while (count < part.chars.length && part.ends[count] <= budget) count++;
    if (count === part.shown) return;
    part.shown = count;
    this.render(this.prefix + part.chars.slice(0, count).join(''));
  }

  finish() {
    if (!this.active) return;
    this.prefix += this.active.chars.join('');
    this.active = null;
    this.render(this.prefix);
  }

  play(text, duration, clock) {
    clearInterval(this.timer);
    this.active = this.segment(text, duration);
    const update = () => this.show(clock());
    update();
    this.timer = setInterval(update, 50);
  }

  endLocal(complete = true) {
    clearInterval(this.timer);
    this.timer = null;
    if (complete) this.finish();
    else this.active = null;
  }

  enqueue(text, duration) {
    this.pending.push(this.segment(text, duration));
  }

  tick(seconds) {
    this.active ||= this.pending.shift();
    if (!this.active) return;
    this.active.elapsed += seconds;
    while (this.active.duration && this.active.elapsed >= this.active.duration && this.pending.length) {
      const remainder = this.active.elapsed - this.active.duration;
      this.finish();
      this.active = this.pending.shift();
      this.active.elapsed = remainder;
    }
    this.show(this.active.elapsed);
  }

  endRemote() {
    this.finish();
  }
}
