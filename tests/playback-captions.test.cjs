const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

(async () => {
  const timers = new Map();
  let id = 0;
  const context = vm.createContext({
    setInterval: fn => { timers.set(++id, fn); return id; },
    clearInterval: key => timers.delete(key),
  });
  const source = new vm.SourceTextModule(fs.readFileSync('web/captions.js', 'utf8'), { context });
  await source.link(() => {});
  await source.evaluate();
  let visible = '用户的问题';
  const captions = new source.namespace.PlaybackCaptions(text => { visible = text; });
  const reply = '你好，今天一起去散步吧。';
  captions.enqueue(reply);
  assert.equal(visible, '用户的问题'); // Submission and synthesis never expose the reply.
  captions.tick(0.2);
  assert(reply.startsWith(visible) && visible.length > 0 && visible.length < reply.length);
  const partial = visible;
  for (const fn of timers.values()) fn();
  assert.equal(visible, partial); // RTC captions require audible progress, not wall time.
  captions.endRemote();
  assert.equal(visible, reply);

  captions.reset();
  captions.enqueue('旧的一轮回复');
  captions.tick(0.2);
  const stopped = visible;
  captions.reset();
  captions.tick(30);
  captions.endRemote();
  assert.equal(visible, stopped); // Late events after stop cannot expose unspoken text.

  let position = -0.03;
  captions.play('第一句话。', 3, () => position);
  assert.equal(visible, stopped); // Scheduled audio has not started yet.
  position = 1.5;
  for (const fn of timers.values()) fn();
  assert('第一句话。'.startsWith(visible) && visible.length < 6);
  const paused = visible;
  for (const fn of timers.values()) fn();
  assert.equal(visible, paused); // A paused media clock freezes captions.
  captions.endLocal();
  assert.equal(visible, '第一句话。');
  assert.equal(timers.size, 0);
  position = 0;
  captions.play('第二句。', 2, () => position);
  position = 1;
  for (const fn of timers.values()) fn();
  assert(visible.startsWith('第一句话。') && visible.length < '第一句话。第二句。'.length);
  captions.endLocal();
  assert.equal(visible, '第一句话。第二句。');

  captions.reset();
  position = 0;
  captions.play('这段被取消了。', 4, () => position);
  const beforeCancel = visible;
  captions.reset();
  position = 4;
  for (const fn of timers.values()) fn();
  assert.equal(visible, beforeCancel);
  captions.endLocal();
  assert.equal(visible, beforeCancel);

  captions.enqueue('你好。', 1);
  captions.enqueue('接着说。', 2);
  captions.tick(1.5);
  assert(visible.startsWith('你好。') && visible.length < '你好。接着说。'.length);
  captions.endRemote();
  assert.equal(visible, '你好。接着说。');
  captions.reset();
  console.log('PASS: playback captions wait for sound, follow media clocks, queue segments and cancel cleanly');
})().catch(error => { console.error(error); process.exitCode = 1; });
