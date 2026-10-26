import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer as createHttpServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createServer, loadConfigFromFile } from 'vite';

test('the dev proxy closes an interrupted upstream event stream', async () => {
  const frontend = fileURLToPath(new URL('../frontend', import.meta.url));
  const loaded = await loadConfigFromFile(
    { command: 'serve', mode: 'test' },
    `${frontend}/vite.config.ts`,
  );
  const configured = loaded?.config.server?.proxy?.['/api'];
  assert.ok(configured);
  const options = typeof configured === 'string' ? { target: configured } : configured;
  const ready = Promise.withResolvers();
  const upstream = createHttpServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' });
    response.write(': connected\n\n');
    ready.resolve(response);
  });
  upstream.listen(0, '127.0.0.1');
  await once(upstream, 'listening');
  const address = upstream.address();
  assert.ok(address && typeof address === 'object');
  const controller = new AbortController();
  let deadline;
  let proxy;
  try {
    proxy = await createServer({
      configFile: false,
      root: frontend,
      logLevel: 'silent',
      server: {
        host: '127.0.0.1',
        port: 0,
        hmr: false,
        proxy: {
          '/api': { ...options, target: `http://127.0.0.1:${address.port}` },
        },
      },
    });
    await proxy.listen();
    const proxyAddress = proxy.httpServer.address();
    assert.ok(proxyAddress && typeof proxyAddress === 'object');
    const response = await fetch(`http://127.0.0.1:${proxyAddress.port}/api/events`, {
      signal: controller.signal,
    });
    assert.equal(response.status, 200);
    const reader = response.body.getReader();
    assert.equal((await reader.read()).done, false);
    (await ready.promise).destroy();
    const outcome = await Promise.race([
      reader.read().then(
        () => 'closed',
        () => 'closed',
      ),
      new Promise((resolve) => {
        deadline = setTimeout(() => resolve('still open'), 1000);
      }),
    ]);
    assert.equal(outcome, 'closed', 'the browser must receive EOF or an error after upstream loss');
  } finally {
    clearTimeout(deadline);
    controller.abort();
    proxy?.httpServer?.closeAllConnections();
    await proxy?.close();
    upstream.closeAllConnections();
    await new Promise((resolve, reject) => {
      upstream.close((error) => (error ? reject(error) : resolve()));
    });
  }
});
