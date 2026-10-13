const assert = require('node:assert/strict');
const test = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const { EventEmitter } = require('node:events');

async function launch(locked = true) {
  const app = new EventEmitter();
  let quitCalls = 0;
  let starts = 0;
  let stops = 0;
  let exits = 0;
  Object.assign(app, { setName() {}, requestSingleInstanceLock: () => locked,
    quit() { quitCalls++; app.emit('before-quit', { preventDefault() {} }); },
    exit() { exits++; }, whenReady: () => Promise.resolve(), isPackaged: false });
  const ses = { setPermissionCheckHandler(fn) { this.check = fn; }, setPermissionRequestHandler(fn) { this.request = fn; } };
  let window;
  class Window {
    constructor(options) {
      window = this; this.options = options; this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = fn => { this.popup = fn; };
    }
    setMenu() {} async loadFile() {} async loadURL(url) { this.url = url; }
    isDestroyed() { return false; } isMinimized() { return true; }
    restore() { this.restored = true; } show() {} focus() { this.focused = true; }
  }
  class Backend {
    async start() { starts++; return 'http://127.0.0.1:7861'; }
    async stop() { stops++; }
  }
  vm.runInNewContext(fs.readFileSync(require.resolve('./main.cjs'), 'utf8'), {
    __dirname, process, URL,
    require(name) {
      if (name === 'electron') return { app, BrowserWindow: Window, session: { defaultSession: ses }, dialog: { showErrorBox: assert.fail } };
      if (name === './backend.cjs') return { Backend, projectRoot: () => __dirname };
      if (name === 'node:fs') return { mkdirSync() {}, appendFileSync() {} };
      return require(name);
    },
  });
  await new Promise(setImmediate);
  return { app, ses, window, counts: () => ({ quitCalls, starts, stops, exits }) };
}

test('desktop microphone is limited to local main frame; camera and other permissions denied', async () => {
  const { ses, window } = await launch();
  assert.equal(window.options.webPreferences.nodeIntegration, false);
  assert.equal(window.options.webPreferences.sandbox, true);
  const details = { requestingUrl: 'http://127.0.0.1:7861/', isMainFrame: true, mediaType: 'audio', mediaTypes: ['audio'] };
  assert.equal(ses.check(window.webContents, 'media', '', details), true);
  for (const change of [{ mediaType: 'video' }, { requestingUrl: 'https://example.com/' }, { isMainFrame: false }]) {
    assert.equal(ses.check(window.webContents, 'media', '', { ...details, ...change }), false);
  }
  let granted;
  ses.request(window.webContents, 'media', value => granted = value, details);
  assert.equal(granted, true);
  ses.request(window.webContents, 'media', value => granted = value, { ...details, mediaTypes: ['audio', 'video'] });
  assert.equal(granted, false);
  ses.request(null, 'media', value => granted = value, details);
  assert.equal(granted, false);
  assert.equal(ses.check(window.webContents, 'geolocation', '', details), false);
  assert.equal(window.popup().action, 'deny');
});

test('second launch focuses existing window and window close stops owned service once', async () => {
  const desktop = await launch();
  desktop.app.emit('second-instance');
  assert(desktop.window.focused && desktop.window.restored);
  assert.equal(desktop.counts().starts, 1);
  desktop.app.emit('window-all-closed');
  await new Promise(setImmediate);
  assert.equal(desktop.counts().stops, 1);
  assert.equal(desktop.counts().exits, 1);
});

test('duplicate process exits without starting another service', async () => {
  const desktop = await launch(false);
  assert.equal(desktop.counts().quitCalls, 1);
  assert.equal(desktop.counts().starts, 0);
});
