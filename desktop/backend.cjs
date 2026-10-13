const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const { setTimeout: delay } = require('node:timers/promises');

function projectRoot(start) {
  let current = path.resolve(start);
  while (true) {
    if (fs.existsSync(path.join(current, 'config', 'models.json')) && fs.existsSync(path.join(current, 'app', 'main.py'))) return current;
    const parent = path.dirname(current);
    if (parent === current) throw new Error('找不到项目目录，请将桌面程序保留在项目目录内。');
    current = parent;
  }
}

class Backend {
  constructor(root, log) { this.root = root; this.log = log; this.child = null; }

  async start() {
    const python = path.join(this.root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    if (!fs.existsSync(python)) throw new Error('未找到项目的 Python 环境，请先按 README 安装依赖。');
    fs.mkdirSync(path.join(this.root, 'logs'), { recursive: true });
    const output = fs.createWriteStream(path.join(this.root, 'logs', 'desktop-server.log'), { flags: 'a' });
    const child = this.child = spawn(python, ['-m', 'app.desktop_server'], {
      cwd: this.root, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, SAIBOGIRL_DESKTOP: '1', PYTHONUNBUFFERED: '1' },
    });
    this.exited = false;
    this.spawnError = null;
    child.on('error', error => { this.spawnError = error; });
    child.once('exit', code => { this.exited = true; this.log(`Backend exited (${code})`); output.end(); });
    child.stdin.on('error', () => {});
    child.stderr.pipe(output, { end: false });
    let buffer = '';
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      let end;
      while ((end = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 1);
        if (/^XIAOMAN_PORT=\d+$/.test(line)) this.url = `http://127.0.0.1:${line.split('=')[1]}`;
        else output.write(line + '\n');
      }
    });
    this.log(`Backend starting (PID ${child.pid})`);
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      if (this.spawnError || this.exited) throw new Error('本地服务启动失败，请查看 logs/desktop-server.log。');
      if (this.url) {
        try {
          const response = await fetch(this.url + '/api/status', { signal: AbortSignal.timeout(1500) });
          if (response.ok) { this.log('Backend ready: ' + this.url); return this.url; }
        } catch { /* The service is still loading local models. */ }
      }
      await delay(200);
    }
    throw new Error('本地模型启动超过三分钟，请检查日志或改用较小模型。');
  }

  async stop() {
    const child = this.child;
    if (!child || this.exited || this.spawnError) return;
    this.log('Stopping owned backend');
    const exited = once(child, 'exit').catch(() => {});
    child.stdin.end(); // EOF also shuts down Python if the desktop process crashes.
    const timeout = new AbortController();
    const stopped = await Promise.race([
      exited.then(() => true), delay(10000, false, { signal: timeout.signal }).catch(() => false),
    ]);
    timeout.abort();
    if (!stopped && !this.exited) {
      this.log('Backend shutdown timeout; terminating owned process');
      if (process.platform === 'win32') {
        await new Promise(resolve => {
          const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true });
          killer.once('exit', resolve); killer.once('error', resolve);
        });
      } else child.kill('SIGKILL');
    }
  }
}

module.exports = { Backend, projectRoot };
