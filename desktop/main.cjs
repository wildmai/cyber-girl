const { app, BrowserWindow, dialog, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { Backend, projectRoot } = require('./backend.cjs');

app.setName('小满');
let window;
let backend;
let quitting = false;
let localURL;
let log = () => {};

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    if (window?.isMinimized()) window.restore();
    window?.show(); window?.focus();
    log('Existing desktop window activated');
  });
  app.on('before-quit', event => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    backend?.stop().finally(() => app.exit());
    if (!backend) app.exit();
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    try {
      const root = projectRoot(app.isPackaged ? path.dirname(process.execPath) : __dirname);
      fs.mkdirSync(path.join(root, 'logs'), { recursive: true });
      log = message => fs.appendFileSync(path.join(root, 'logs', 'desktop.log'), `${new Date().toISOString()} ${message}\n`);
      log('Desktop starting');
      window = new BrowserWindow({ width: 1360, height: 880, minWidth: 960, minHeight: 680,
        title: '小满 · 赛博女友', backgroundColor: '#080b0e', autoHideMenuBar: true,
        icon: path.join(__dirname, 'assets', 'icon.ico'),
        webPreferences: { nodeIntegration: false, contextIsolation: true, sandbox: true },
      });
      window.setMenu(null);
      window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      window.webContents.on('will-navigate', (event, url) => {
        if (!localURL || new URL(url).origin !== localURL) event.preventDefault();
      });
      window.webContents.on('render-process-gone', () => { log('Renderer exited unexpectedly'); app.quit(); });
      const trustedMedia = (contents, permission, details) => Boolean(contents === window?.webContents && permission === 'media'
        && localURL && details.requestingUrl?.startsWith(localURL + '/') && details.isMainFrame === true);
      session.defaultSession.setPermissionCheckHandler((contents, permission, origin, details) =>
        trustedMedia(contents, permission, details) && details.mediaType === 'audio');
      session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) =>
        callback(Boolean(trustedMedia(contents, permission, details) && details.mediaTypes?.length
          && details.mediaTypes.every(type => type === 'audio'))));
      await window.loadFile(path.join(__dirname, 'loading.html'));
      backend = new Backend(root, log);
      localURL = await backend.start();
      if (quitting || window.isDestroyed()) return;
      await window.loadURL(localURL);
      log('Desktop application loaded');
    } catch (error) {
      log('Desktop startup failed: ' + error.message);
      if (!quitting) dialog.showErrorBox('小满暂时无法启动', error.message);
      app.quit();
    }
  });
}
