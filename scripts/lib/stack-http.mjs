const REQUEST_TIMEOUT_MS = 10_000;
const RESTART_TIMEOUT_MS = 30_000;
const SECURITY_HEADERS = {
  'content-security-policy':
    "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
  'x-content-type-options': 'nosniff',
  'x-frame-options': 'DENY',
  'referrer-policy': 'no-referrer',
  'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
};

const requireCheck = (condition, message) => {
  if (!condition) throw new Error(message);
};

const originOf = (baseUrl) => {
  let url;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new Error('The stack base URL is invalid');
  }
  requireCheck(
    ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.pathname === '/',
    'The stack base URL must be an HTTP origin without credentials',
  );
  return url.origin;
};

export const checkSeedLink = (baseUrl, link, token) => {
  let url;
  try {
    url = new URL(link);
  } catch {
    throw new Error('Seed link is invalid');
  }
  requireCheck(
    url.origin === originOf(baseUrl).replace('127.0.0.1', 'localhost') &&
      !url.username &&
      !url.password &&
      url.pathname === '/' &&
      !url.search &&
      url.hash === `#token=${token}`,
    'Seed link must use the web root and issued token',
  );
};

// Replace transport errors with fixed messages: a fetch error can contain a ticket URL.
const bounded = async (operation, deadline, label, abort = () => {}) => {
  let timer;
  try {
    requireCheck(Date.now() < deadline, label);
    return await Promise.race([
      Promise.resolve().then(operation),
      new Promise((_, reject) => {
        timer = setTimeout(
          () => {
            abort();
            reject(new Error(label));
          },
          Math.max(1, deadline - Date.now()),
        );
      }),
    ]);
  } catch (error) {
    // nginx closes an interrupted chunked upstream without its final chunk.
    // Preserve only this classification; raw fetch errors can contain ticket URLs.
    throw Object.assign(new Error(label), {
      socketClosed: error?.cause?.code === 'UND_ERR_SOCKET',
    });
  } finally {
    clearTimeout(timer);
  }
};

const request = async (origin, path, label, options = {}, deadline) => {
  const controller = new AbortController();
  try {
    return await bounded(
      async () => {
        const response = await fetch(`${origin}${path}`, {
          ...options,
          redirect: 'error',
          signal: controller.signal,
        });
        return {
          status: response.status,
          headers: response.headers,
          body: await response.text(),
        };
      },
      Math.min(deadline ?? Infinity, Date.now() + REQUEST_TIMEOUT_MS),
      `${label}: request failed or timed out`,
      () => controller.abort(),
    );
  } finally {
    controller.abort();
  }
};

const jsonBody = (response, label) => {
  requireCheck(
    /^application\/json\b/i.test(response.headers.get('content-type') ?? ''),
    `${label}: expected a JSON response`,
  );
  try {
    return JSON.parse(response.body);
  } catch {
    throw new Error(`${label}: invalid JSON response`);
  }
};

const requireStatus = (response, status, label) => {
  requireCheck(response.status === status, `${label}: expected HTTP ${status}`);
};

export const readTasks = async (baseUrl, token) => {
  const response = await request(originOf(baseUrl), '/api/tasks', 'Task list', {
    headers: { Authorization: `Bearer ${token}` },
  });
  requireStatus(response, 200, 'Task list');
  return jsonBody(response, 'Task list');
};

const normalizePolicy = (value) =>
  value
    .split(/[;,]/)
    .map((part) => part.trim())
    .filter(Boolean)
    .join(';');

const checkHeaders = (response, label) => {
  for (const [name, expected] of Object.entries(SECURITY_HEADERS)) {
    requireCheck(
      normalizePolicy(response.headers.get(name) ?? '') === normalizePolicy(expected),
      `${label}: ${name} does not match the required policy`,
    );
  }
  requireCheck(
    !/\d+\.\d+/.test(response.headers.get('server') ?? ''),
    `${label}: Server header exposes a version`,
  );
};

export const checkWeb = async (baseUrl) => {
  const origin = originOf(baseUrl);
  const root = await request(origin, '/', 'Root page');
  const index = await request(origin, '/index.html', 'Index page');
  for (const [response, label] of [
    [root, 'Root page'],
    [index, 'Index page'],
  ]) {
    requireStatus(response, 200, label);
    checkHeaders(response, label);
    requireCheck(
      /(?:^|,)\s*no-cache\s*(?:,|$)/i.test(response.headers.get('cache-control') ?? ''),
      `${label}: HTML must use no-cache`,
    );
  }

  const asset = root.body.match(
    /(?:src|href)=["'](\/assets\/[\w./-]+-[\w-]{8,}\.(?:js|css))["']/,
  )?.[1];
  requireCheck(asset, 'Root page: no hashed JavaScript or CSS asset found');
  const assetResponse = await request(origin, asset, 'Hashed asset', {
    headers: { 'Accept-Encoding': 'gzip' },
  });
  requireStatus(assetResponse, 200, 'Hashed asset');
  requireCheck(
    (assetResponse.headers.get('vary') ?? '')
      .split(',')
      .some((name) => name.trim().toLowerCase() === 'accept-encoding'),
    'Hashed asset: Vary must include Accept-Encoding',
  );
  checkHeaders(assetResponse, 'Hashed asset');
  const cache = assetResponse.headers.get('cache-control') ?? '';
  requireCheck(
    /(?:^|,)\s*immutable\s*(?:,|$)/i.test(cache) &&
      /(?:^|,)\s*max-age=31536000\s*(?:,|$)/i.test(cache),
    'Hashed asset: expected a one-year immutable cache policy',
  );
  requireCheck(
    assetResponse.headers.get('content-encoding') === 'gzip' && assetResponse.body.length > 0,
    'Hashed asset: expected a non-empty gzip response',
  );
  requireCheck(
    /(?:javascript|text\/css)/i.test(assetResponse.headers.get('content-type') ?? ''),
    'Hashed asset: expected JavaScript or CSS',
  );

  const missing = await request(origin, '/assets/s8-missing-00000000.js', 'Missing asset');
  requireStatus(missing, 404, 'Missing asset');
  checkHeaders(missing, 'Missing asset');
  const unauthorized = await request(origin, '/api/tasks', 'Unauthenticated task list');
  requireStatus(unauthorized, 401, 'Unauthenticated task list');
  checkHeaders(unauthorized, 'Unauthenticated task list');
  const error = jsonBody(unauthorized, 'Unauthenticated task list');
  requireCheck(
    error !== null && typeof error === 'object' && !Array.isArray(error),
    'Unauthenticated task list: expected a JSON error object',
  );
  const oversized = await request(origin, '/api/tasks', 'Oversized request', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: 'x'.repeat(64 * 1024 + 1),
  });
  requireStatus(oversized, 413, 'Oversized request');
  return { securityResponses: 5, htmlNoCache: true, immutableGzipAsset: true, bodyLimit: true };
};

const closeStream = async (stream) => {
  stream.controller.abort();
  if (!stream.reader) return;
  try {
    await bounded(() => stream.reader.cancel(), Date.now() + 1000, 'Stream cleanup timed out');
  } catch {
    // Aborting a terminated fetch can reject cancellation; the connection is already closed.
  } finally {
    stream.reader.releaseLock();
  }
};

const readStream = (stream, deadline, label) =>
  bounded(
    () => stream.reader.read(),
    deadline,
    label,
    () => stream.controller.abort(),
  );

const waitConnected = async (stream, deadline) => {
  const decoder = new TextDecoder();
  let pending = '';
  while (Date.now() < deadline) {
    const { done, value } = await readStream(stream, deadline, 'SSE connection was not observed');
    requireCheck(!done, 'SSE ended before the connected comment');
    pending += decoder.decode(value, { stream: true });
    requireCheck(pending.length <= 64 * 1024, 'SSE preamble exceeded the size limit');
    const frames = pending.split(/\r?\n\r?\n/);
    pending = frames.pop();
    if (frames.some((frame) => frame.split(/\r?\n/).includes(': connected'))) return;
  }
  throw new Error('SSE connection was not observed');
};

const openStream = async (origin, token, deadline, streams) => {
  const issued = await request(
    origin,
    '/api/realtime/tickets',
    'Realtime ticket',
    { method: 'POST', headers: { Authorization: `Bearer ${token}` } },
    deadline,
  );
  requireStatus(issued, 201, 'Realtime ticket');
  const ticket = jsonBody(issued, 'Realtime ticket');
  requireCheck(
    ticket !== null &&
      typeof ticket === 'object' &&
      typeof ticket.ticket === 'string' &&
      ticket.ticket.length > 0 &&
      ticket.ticket.length <= 4096 &&
      Number.isFinite(ticket.expires_in) &&
      ticket.expires_in > 0,
    'Realtime ticket: invalid response shape',
  );
  const stream = { controller: new AbortController(), reader: null };
  streams.add(stream);
  try {
    const response = await bounded(
      () =>
        fetch(`${origin}/api/events?ticket=${encodeURIComponent(ticket.ticket)}`, {
          headers: { Accept: 'text/event-stream' },
          redirect: 'error',
          signal: stream.controller.signal,
        }),
      Math.min(deadline, Date.now() + REQUEST_TIMEOUT_MS),
      'SSE request failed or timed out',
      () => stream.controller.abort(),
    );
    requireStatus(response, 200, 'SSE');
    requireCheck(
      /^text\/event-stream\b/i.test(response.headers.get('content-type') ?? '') && response.body,
      'SSE: expected an event stream',
    );
    stream.reader = response.body.getReader();
    await waitConnected(stream, Math.min(deadline, Date.now() + REQUEST_TIMEOUT_MS));
    return stream;
  } catch (error) {
    await closeStream(stream);
    streams.delete(stream);
    throw error;
  }
};

const waitEof = async (stream, deadline) => {
  try {
    while (Date.now() < deadline) {
      const result = await readStream(stream, deadline, 'Restarted SSE did not close');
      if (result.done) return 'eof';
    }
  } catch (error) {
    if (error.socketClosed) return 'socket-closed';
    throw error;
  }
  throw new Error('Restarted SSE did not close');
};

// The shorter timeout is a test seam; production can never widen the 30-second budget.
export const checkStreamRestart = async (baseUrl, token, restart, { timeoutMs = 30_000 } = {}) => {
  requireCheck(
    Number.isInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= RESTART_TIMEOUT_MS,
    'SSE restart timeout must be between 1 and 30000 milliseconds',
  );
  const origin = originOf(baseUrl);
  const streams = new Set();
  try {
    const initial = await openStream(origin, token, Date.now() + timeoutMs, streams);
    const started = Date.now();
    const deadline = started + timeoutMs;
    // Observe closure while the restart command waits for the API to become healthy.
    const eof = waitEof(initial, deadline).then(
      (closure) => closure,
      () => false,
    );
    await bounded(restart, deadline, 'API restart failed or timed out');
    const closure = await eof;
    requireCheck(closure, 'Restarted SSE did not close');
    let attempts = 0;
    while (Date.now() < deadline) {
      attempts += 1;
      try {
        await openStream(origin, token, deadline, streams);
        requireCheck(Date.now() <= deadline, 'SSE did not reconnect within the restart budget');
        return {
          ended: true,
          closure,
          reconnected: true,
          reconnectMs: Date.now() - started,
          attempts,
        };
      } catch {
        const remaining = deadline - Date.now();
        if (remaining > 0) {
          await new Promise((resolve) => setTimeout(resolve, Math.min(250, remaining)));
        }
      }
    }
    throw new Error('SSE did not reconnect within the restart budget');
  } finally {
    for (const stream of streams) await closeStream(stream);
  }
};
