/** Browser-native APIs differ from Node/jsdom. Run against a synthetic loopback API. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const browser = process.env.CHROME_BIN || [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
].find(existsSync);
assert.ok(browser, 'Set CHROME_BIN to an installed Chrome or Chromium executable');

const id = '22222222-2222-4222-8222-222222222222';
const receipt = { receiptVersion: '2', submissionId: id, acceptedAt: new Date().toISOString(),
  receiptExpiresAt: new Date(Date.now() + 86400000).toISOString(), revision: '1', userStatus: 'PREPARING',
  contentStatus: 'AVAILABLE', statusUrl: `/api/v2/submissions/${id}` };
const modules = new Set(['/clients/uuid.mjs', '/clients/student/submission-client.mjs', '/clients/display/playback-controller.mjs']);
const requests = [], failures = [];
let report;
let finish;
const completed = new Promise(resolve => { finish = resolve; });
const server = createServer(async (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-store');
    if (req.url === '/') {
      res.setHeader('Content-Type', 'text/html');
      res.end(`<!doctype html><meta charset="utf-8"><script type="module">
import { SubmissionClient } from '/clients/student/submission-client.mjs';
import { FetchPlaybackTransport } from '/clients/display/playback-controller.mjs';
const check = (value, label) => { if (!value) throw Error(label); };
try {
  const client = new SubmissionClient({ csrfToken: () => 'synthetic-csrf' });
  client.begin('Browser integration test');
  const accepted = await client.send();
  check(accepted.state === 'ACCEPTED', 'send: ' + accepted.state + '/' + accepted.code);
  check((await client.recover()).state === 'ACCEPTED', 'lookup');
  check((await client.getReceipt()).state === 'ACCEPTED', 'poll');
  const restored = new SubmissionClient();
  check((await restored.restorePending()).state === 'ACCEPTED', 'restore');
  for (const options of [{}, { fetch: window.fetch }]) {
    const transport = new FetchPlaybackTransport({ ...options, csrfToken: () => 'synthetic-csrf' });
    check((await transport.request({ action: 'state', clientInstanceId: '${id}' })).ok, 'playback state');
    check(await transport.request({ action: 'claim', body: {}, idempotencyKey: '${id}' }) === null, 'claim');
  }
  await fetch('/result', { method: 'POST', body: JSON.stringify({ ok: true }) });
} catch (error) {
  await fetch('/result', { method: 'POST', body: JSON.stringify({ ok: false, error: error.message }) });
}
</script>`);
      return;
    }
    if (modules.has(req.url)) {
      res.setHeader('Content-Type', 'text/javascript');
      res.end(await readFile(join(root, req.url.slice(1))));
      return;
    }
    let body = '';
    for await (const chunk of req) { body += chunk; assert.ok(body.length <= 4096); }
    if (req.url === '/result') {
      report = JSON.parse(body);
      res.end('{}');
      finish();
      return;
    }
    if (req.url === '/favicon.ico') { res.writeHead(204).end(); return; }
    requests.push({ path: req.url, method: req.method, key: req.headers['idempotency-key'] });
    res.setHeader('Content-Type', 'application/json');
    if (req.method === 'POST') assert.equal(req.headers['x-csrf-token'], 'synthetic-csrf');
    if (req.url === '/api/v2/submissions') {
      assert.match(req.headers['idempotency-key'], /^[\da-f-]{36}$/);
      assert.equal(req.headers['x-moodwall-client-features'], 'support-v1');
      res.writeHead(202).end(JSON.stringify(receipt));
    } else if (req.url === '/api/v2/submission-receipts/lookup') {
      assert.equal(JSON.parse(body).idempotencyKey, requests[0].key);
      res.end(JSON.stringify(receipt));
    } else if (req.url === receipt.statusUrl) res.end(JSON.stringify(receipt));
    else if (req.url.startsWith('/api/display/playback?')) res.end('{"ok":true}');
    else if (req.url === '/api/display/playback/claim') res.writeHead(204).end();
    else throw Error('Unexpected route');
  } catch (error) { failures.push(error.message); res.writeHead(500).end('{}'); }
});
const profile = await mkdtemp(join(tmpdir(), 'moodwall-browser-smoke-'));
let processHandle, exited, browserErrors = '';
try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  // Keep this isolated browser alive until the asynchronous fetch chain reports
  // its result. dump-dom/virtual time can exit before those requests finish.
  processHandle = spawn(browser, ['--headless', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${profile}`,
    `http://127.0.0.1:${server.address().port}/`], { stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true });
  processHandle.stderr.on('data', chunk => { browserErrors = (browserErrors + chunk.toString()).slice(-8192); });
  exited = new Promise(resolve => {
    processHandle.once('error', error => resolve({ error }));
    processHandle.once('exit', (code, signal) => resolve({ code, signal }));
  });
  let deadline;
  try {
    await Promise.race([
      completed,
      exited.then(({ error, code, signal }) => {
        throw error || Error(`Browser exited before /result (code=${code}, signal=${signal}): ${browserErrors}`);
      }),
      new Promise((_, reject) => {
        deadline = setTimeout(() => reject(Error('Browser smoke timed out waiting for /result')), 60000);
      }),
    ]);
  } finally { clearTimeout(deadline); }
  assert.deepEqual(failures, []);
  assert.deepEqual(report, { ok: true }, JSON.stringify(report));
  assert.equal(requests.filter(r => r.path === '/api/v2/submissions').length, 1);
  assert.equal(requests.filter(r => r.path === '/api/display/playback/claim').length, 2);
  console.log('PASS: native browser submission, recovery, reload and playback transport (synthetic API)');
} finally {
  if (processHandle?.pid && processHandle.exitCode === null && processHandle.signalCode === null) processHandle.kill();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  if (exited) {
    let deadline;
    try {
      await Promise.race([exited, new Promise((_, reject) => {
        deadline = setTimeout(() => reject(Error('Browser did not exit during cleanup')), 5000);
      })]);
    } finally { clearTimeout(deadline); }
  }
  // Only the freshly-created temporary browser profile belongs to this test.
  assert.equal(dirname(resolve(profile)), resolve(tmpdir()));
  assert.ok(basename(profile).startsWith('moodwall-browser-smoke-'));
  await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
}
