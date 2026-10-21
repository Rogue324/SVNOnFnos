// HTTPS desktop/gateway simulation. Requires Playwright, SVN, and a test TLS certificate.
// FNSVN_TEST_CERT / FNSVN_TEST_KEY point to a self-signed certificate and key.
// This checks HTTPS/path handling; the real fnOS login gateway still requires NAS validation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');

async function freePort() {
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function main() {
  assert.ok(process.env.FNSVN_TEST_CERT && process.env.FNSVN_TEST_KEY, 'test certificate and key required');
  const root = path.resolve(__dirname, '..');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'fnsvn-https-'));
  const port = await freePort();
  const svnPort = await freePort();
  const python = process.env.FNSVN_PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
  const args = python === 'py' ? ['-3.12'] : [];
  // Windows builds without AF_UNIX exercise the same Handler through loopback TCP.
  const backend = spawn(python, [...args, 'server.py', '--host', '127.0.0.1', '--port', String(port),
    '--svn-port', String(svnPort), '--base-path', '/app/fnsvn', '--data', path.join(tmp, 'data')],
    { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = '';
  backend.stdout.on('data', data => { logs += data; });
  backend.stderr.on('data', data => { logs += data; });
  const requests = [];
  const gateway = https.createServer({ key: fs.readFileSync(process.env.FNSVN_TEST_KEY), cert: fs.readFileSync(process.env.FNSVN_TEST_CERT) }, (req, res) => {
    requests.push(req.url);
    if (req.url === '/') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      return res.end('<iframe id="fnos-app" src="/app/fnsvn" style="border:0;width:100%;height:100dvh"></iframe>');
    }
    if (!req.url.startsWith('/app/fnsvn')) { res.writeHead(404); return res.end(); }
    const upstream = http.request({ host: '127.0.0.1', port, method: req.method, path: req.url, headers: req.headers }, reply => {
      res.writeHead(reply.statusCode, reply.headers);
      reply.pipe(res);
    });
    upstream.on('error', error => { res.writeHead(502); res.end(error.message); });
    req.pipe(upstream);
  });
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (backend.exitCode !== null) throw new Error(logs);
      try {
        const response = await fetch(`http://127.0.0.1:${port}/app/fnsvn/api/health`);
        if (response.ok) { ready = true; break; }
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(ready, logs);
    await new Promise(resolve => gateway.listen(0, '127.0.0.1', resolve));
    const desktop = `https://127.0.0.1:${gateway.address().port}`;
    const browser = spawn(process.execPath, [path.join(__dirname, 'browser.cjs')], {
      cwd: root, stdio: 'inherit', env: { ...process.env, FNSVN_TEST_URL: desktop + '/app/fnsvn',
        FNSVN_TEST_DESKTOP_URL: desktop, FNSVN_TEST_IGNORE_HTTPS: '1' }
    });
    const code = await new Promise((resolve, reject) => { browser.on('exit', resolve); browser.on('error', reject); });
    assert.equal(code, 0, logs);
    assert.ok(requests.some(url => url.startsWith('/app/fnsvn/api/backups/')), 'download passed through gateway');
    assert.ok(requests.every(url => url === '/' || url.startsWith('/app/fnsvn')), 'no requests escaped gateway path');
    console.log('HTTPS same-origin iframe gateway workflow passed, including authenticated backup download.');
  } finally {
    gateway.closeAllConnections();
    await new Promise(resolve => gateway.close(resolve));
    if (backend.exitCode === null) {
      if (process.platform === 'win32') execFileSync('taskkill', ['/PID', String(backend.pid), '/T', '/F'], { stdio: 'ignore' });
      else { backend.kill('SIGTERM'); await new Promise(resolve => backend.on('exit', resolve)); }
    }
    const tempRoot = path.resolve(os.tmpdir()) + path.sep;
    assert.ok(path.resolve(tmp).startsWith(tempRoot) && path.basename(tmp).startsWith('fnsvn-https-'));
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exit(1); });
