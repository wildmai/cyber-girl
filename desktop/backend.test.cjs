const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Backend, projectRoot } = require('./backend.cjs');

test('locates project from nested packaged executable directory', () => {
  assert.equal(projectRoot(path.join(__dirname, '..', 'output', 'desktop', 'Xiaoman-win32-x64')), path.resolve(__dirname, '..'));
});

test('missing project and runtime fail with actionable messages', async () => {
  assert.throws(() => projectRoot(path.parse(__dirname).root), /找不到项目目录/);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'xiaoman-test-'));
  try { await assert.rejects(new Backend(root, () => {}).start(), /Python 环境/); }
  finally { fs.rmdirSync(root); }
});

test('real desktop backend starts privately and exits on parent pipe close', async () => {
  const backend = new Backend(path.resolve(__dirname, '..'), () => {});
  try {
    const url = await backend.start();
    assert.equal(url, 'http://127.0.0.1:7861');
    const response = await fetch(url + '/api/status');
    assert.equal(response.status, 200);
    const status = await response.json();
    assert.equal(status.budget_gib, 15);
    assert.equal(typeof status.avatar.realtime, 'boolean');
    assert((await (await fetch(url)).text()).includes('赛博女友'));
  } finally { await backend.stop(); }
  assert(backend.exited);
  await assert.rejects(fetch('http://127.0.0.1:7861/api/status', { signal: AbortSignal.timeout(1000) }));
});
