import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from 'node:http';
import test from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { gzipSync } from 'node:zlib';
import { checkSeedLink, checkStreamRestart, checkWeb, readTasks } from './lib/stack-http.mjs';

test('seed links must use the issued token at the web root', () => {
  const origin = 'http://127.0.0.1:18080';
  const valid = 'http://localhost:18080/#token=fixture-token';
  assert.doesNotThrow(() => checkSeedLink(origin, valid, 'fixture-token'));
  for (const link of [
    valid.replace('/#', '/wrong#'),
    valid.replace('fixture-token', 'wrong-token'),
    valid.replace('#token=fixture-token', ''),
    valid.replace('/#', '/?redirect=1#'),
    valid.replace('localhost', 'other-host'),
    'invalid',
  ]) {
    assert.throws(() => checkSeedLink(origin, link, 'fixture-token'), /Seed link/);
  }
});

const TOKEN = 'synthetic-test-bearer';
const ASSET = '/assets/index-ABcd1234.js';
const MISSING = '/assets/s8-missing-00000000.js';
const POLICIES = {
  'content-security-policy':
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
  'referrer-policy': 'no-referrer',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  server: 'nginx',
};

const serve = async (t, { change = () => {}, connected = true } = {}) => {
  const streams = [];
  const closures = [];
  const requests = [];
  const issued = new Set();
  let nextTicket = 0;
  let restarted = false;
  let deniedTickets = 0;
  const tasks = { items: [{ id: 42, title: 'Packaged-stack task' }], has_more: false };
  const server = createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const path = url.pathname;
    requests.push({ path, method: request.method, headers: request.headers });
    const json = (status, body) => ({
      status,
      headers: { ...POLICIES, 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    let output;
    if (path === '/' || path === '/index.html') {
      output = {
        status: 200,
        headers: { ...POLICIES, 'content-type': 'text/html', 'cache-control': 'no-cache' },
        body: `<html><script type="module" src="${ASSET}"></script></html>`,
      };
    } else if (path === ASSET) {
      output = {
        status: 200,
        headers: {
          ...POLICIES,
          'content-type': 'application/javascript',
          'content-encoding': 'gzip',
          vary: 'Accept-Encoding',
          'cache-control': 'public, max-age=31536000, immutable',
        },
        body: gzipSync('export const taskbot = true;\n'.repeat(100)),
      };
    } else if (path === '/api/tasks') {
      if (request.method === 'POST') {
        output = json(Number(request.headers['content-length']) > 64 * 1024 ? 413 : 400, {});
      } else {
        output =
          request.headers.authorization === `Bearer ${TOKEN}`
            ? json(200, tasks)
            : json(401, { code: 'unauthorized' });
      }
    } else if (path === '/api/realtime/tickets') {
      if (request.method !== 'POST' || request.headers.authorization !== `Bearer ${TOKEN}`) {
        output = json(401, { code: 'unauthorized' });
      } else if (restarted && deniedTickets-- > 0) {
        output = json(503, { code: 'unavailable' });
      } else {
        const ticket = `synthetic-ticket-${nextTicket++}`;
        issued.add(ticket);
        output = json(201, { ticket, expires_in: 30 });
      }
    } else if (path === '/api/events' && issued.delete(url.searchParams.get('ticket'))) {
      response.writeHead(200, { 'content-type': 'text/event-stream' });
      response.write(connected ? ': con' : ': waiting\n\n');
      if (connected) setImmediate(() => response.write('nected\r\n\r\n'));
      streams.push(response);
      closures.push(once(response, 'close'));
      return;
    } else {
      output = {
        status: 404,
        headers: { ...POLICIES, 'content-type': 'text/html' },
        body: '<html>Not found</html>',
      };
    }
    change(path, output, request);
    response.writeHead(output.status, output.headers);
    response.end(output.body);
  });
  t.after(async () => {
    server.closeAllConnections();
    await new Promise((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    tasks,
    streams,
    closures,
    requests,
    restart: (denials = 0) => {
      assert.equal(streams.length, 1, 'restart must follow the initial connected stream');
      restarted = true;
      deniedTickets = denials;
      streams[0].end();
    },
  };
};

test('gzip asset responses must vary on Accept-Encoding', async (t) => {
  const site = await serve(t, {
    change: (path, output) => {
      if (path === ASSET) delete output.headers.vary;
    },
  });
  await assert.rejects(checkWeb(site.baseUrl), /Vary/);
});

test('readTasks sends the bearer and returns parsed API JSON', async (t) => {
  const site = await serve(t);
  assert.deepEqual(await readTasks(site.baseUrl, TOKEN), site.tasks);
});

test('readTasks rejects unauthorized and malformed JSON without exposing the body', async (t) => {
  const site = await serve(t, {
    change: (_path, output) => {
      output.body = `secret ${TOKEN}`;
    },
  });
  await assert.rejects(readTasks(site.baseUrl, 'wrong'), {
    message: 'Task list: expected HTTP 200',
  });
  await assert.rejects(readTasks(site.baseUrl, TOKEN), {
    message: 'Task list: invalid JSON response',
  });
});

test('checkWeb verifies HTML, hashed gzip assets, 404, JSON 401, and the request limit', async (t) => {
  const site = await serve(t);
  assert.deepEqual(await checkWeb(site.baseUrl), {
    securityResponses: 5,
    htmlNoCache: true,
    immutableGzipAsset: true,
    bodyLimit: true,
  });
  assert.equal(site.requests.find(({ path }) => path === ASSET).headers['accept-encoding'], 'gzip');
  const oversized = site.requests.find(({ method }) => method === 'POST');
  assert.ok(Number(oversized.headers['content-length']) > 64 * 1024);
});

test('checkWeb rejects missing security headers on each response class', async (t) => {
  for (const path of ['/', '/index.html', ASSET, MISSING, '/api/tasks']) {
    await t.test(path, async (subtest) => {
      const site = await serve(subtest, {
        change: (actualPath, output) => {
          if (actualPath === path) delete output.headers['x-content-type-options'];
        },
      });
      await assert.rejects(checkWeb(site.baseUrl), /x-content-type-options/);
    });
  }
});

test('checkWeb rejects weakened values for every required security policy', async (t) => {
  for (const header of Object.keys(POLICIES).filter((name) => name !== 'server')) {
    await t.test(header, async (subtest) => {
      const site = await serve(subtest, {
        change: (path, output) => {
          if (path === '/index.html') output.headers[header] = 'unsafe';
        },
      });
      await assert.rejects(checkWeb(site.baseUrl), new RegExp(header));
    });
  }
});

const invalidWebResponses = [
  [
    'server version',
    '/',
    (output) => (output.headers.server = 'nginx/1.30.6'),
    /exposes a version/,
  ],
  ['HTML caching', '/', (output) => (output.headers['cache-control'] = 'public'), /HTML.*no-cache/],
  [
    'mutable asset',
    ASSET,
    (output) => (output.headers['cache-control'] = 'max-age=60'),
    /immutable cache policy/,
  ],
  [
    'uncompressed asset',
    ASSET,
    (output) => {
      delete output.headers['content-encoding'];
      output.body = 'export const uncompressed = true;';
    },
    /gzip response/,
  ],
  ['SPA fallback for a missing asset', MISSING, (output) => (output.status = 200), /HTTP 404/],
  [
    'HTML unauthorized response',
    '/api/tasks',
    (output) => (output.headers['content-type'] = 'text/html'),
    /JSON response/,
  ],
];

for (const [name, path, mutate, expected] of invalidWebResponses) {
  test(`checkWeb rejects ${name}`, async (t) => {
    const site = await serve(t, {
      change: (actualPath, output) => {
        if (actualPath === path) mutate(output);
      },
    });
    await assert.rejects(checkWeb(site.baseUrl), expected);
  });
}

test('checkWeb rejects a missing request-size limit', async (t) => {
  const site = await serve(t, {
    change: (_path, output, request) => {
      if (request.method === 'POST') output.status = 401;
    },
  });
  await assert.rejects(checkWeb(site.baseUrl), /Oversized request: expected HTTP 413/);
});

test(
  'restart observes EOF and retries fresh authenticated tickets until connected',
  { timeout: 15000 },
  async (t) => {
    const site = await serve(t);
    const result = await checkStreamRestart(
      site.baseUrl,
      TOKEN,
      async () => {
        site.restart(1);
        // Model a restart longer than the old fixture budget; R3 checks real latency.
        await delay(2500);
      },
      { timeoutMs: 10000 },
    );
    assert.equal(result.ended, true);
    assert.equal(result.closure, 'eof');
    assert.equal(result.reconnected, true);
    assert.equal(result.attempts, 2);
    assert.equal(site.streams.length, 2);
    assert.equal(site.requests.filter(({ path }) => path === '/api/realtime/tickets').length, 3);
    assert.ok(
      site.requests
        .filter(({ path }) => path === '/api/events')
        .every(({ headers }) => headers.authorization === undefined),
    );
    await Promise.all(site.closures);
  },
);

test(
  'restart fails when the original stream stays open and closes its reader',
  { timeout: 5000 },
  async (t) => {
    const site = await serve(t);
    let restarted = false;
    await assert.rejects(
      checkStreamRestart(site.baseUrl, TOKEN, async () => (restarted = true), { timeoutMs: 200 }),
      { message: 'Restarted SSE did not close' },
    );
    assert.equal(restarted, true);
    await Promise.all(site.closures);
  },
);

test(
  'restart requires the initial connected comment before invoking restart',
  { timeout: 5000 },
  async (t) => {
    const site = await serve(t, { connected: false });
    let restarted = false;
    await assert.rejects(
      checkStreamRestart(site.baseUrl, TOKEN, async () => (restarted = true), { timeoutMs: 200 }),
      { message: 'SSE connection was not observed' },
    );
    assert.equal(restarted, false);
    await Promise.all(site.closures);
  },
);

test(
  'restart accepts a closed upstream socket and still requires a fresh stream',
  { timeout: 5000 },
  async (t) => {
    const site = await serve(t);
    const result = await checkStreamRestart(
      site.baseUrl,
      TOKEN,
      async () => site.streams[0].destroy(),
      { timeoutMs: 2000 },
    );
    assert.equal(result.ended, true);
    assert.equal(result.closure, 'socket-closed');
    assert.equal(result.reconnected, true);
    assert.equal(site.streams.length, 2);
    await Promise.all(site.closures);
  },
);

test(
  'restart callback failures are sanitized and close the original reader',
  { timeout: 5000 },
  async (t) => {
    const site = await serve(t);
    await assert.rejects(
      checkStreamRestart(site.baseUrl, TOKEN, async () => {
        throw new Error(`secret ${TOKEN} with a synthetic-ticket`);
      }),
      { message: 'API restart failed or timed out' },
    );
    await Promise.all(site.closures);
  },
);

test(
  'restart fails if a new connected stream does not return within the budget',
  { timeout: 5000 },
  async (t) => {
    const site = await serve(t);
    await assert.rejects(
      checkStreamRestart(site.baseUrl, TOKEN, async () => site.restart(Infinity), {
        timeoutMs: 200,
      }),
      { message: 'SSE did not reconnect within the restart budget' },
    );
    await Promise.all(site.closures);
  },
);
