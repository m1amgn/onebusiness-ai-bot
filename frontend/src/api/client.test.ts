import { inspect } from 'node:util';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError, createApiClient } from './client';
import type { TaskView } from './index';

const token = 'test-dashboard-token';
const task: TaskView = {
  id: 7,
  inbound_message_id: 3,
  position: 0,
  title: 'Review the proposal',
  description: 'Read the revised proposal.',
  source_text: 'Please review the proposal.',
  status: 'pending',
  created_at: '2026-10-26T08:00:00Z',
  updated_at: '2026-10-26T08:00:00.000001Z',
};

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status });

const readError = async (request: Promise<unknown>): Promise<ApiRequestError> => {
  try {
    await request;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    if (error instanceof ApiRequestError) return error;
    throw error;
  }
  throw new Error('Expected the request to fail.');
};

const pendingFetch = (): typeof fetch =>
  vi.fn<typeof fetch>(
    (_input, init) =>
      new Promise((_resolve, reject) => {
        const signal = init?.signal;
        if (!signal) throw new Error('The request needs an abort signal.');
        const handleAbort = (): void => {
          const reason: unknown = signal.reason;
          reject(reason instanceof Error ? reason : new Error('Request aborted.'));
        };
        if (signal.aborted) {
          handleAbort();
          return;
        }
        signal.addEventListener('abort', handleAbort, { once: true });
      }),
  );

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('createApiClient', () => {
  it('lists tasks with encoded query parameters and validates the page', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ items: [task], next_cursor: 'next-page', unexpected: true }),
      );
    const signal = new AbortController().signal;
    const client = createApiClient({ token, fetchImpl });

    await expect(
      client.listTasks({ limit: 25, cursor: 'page+/=', status: 'in_progress' }, signal),
    ).resolves.toEqual({ items: [task], next_cursor: 'next-page' });
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/tasks?limit=25&cursor=page%2B%2F%3D&status=in_progress',
      expect.objectContaining({ method: 'GET', signal }),
    );
  });

  it('omits absent and null query parameters', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));

    await createApiClient({ token, fetchImpl }).listTasks({ cursor: null, status: null });

    expect(fetchImpl).toHaveBeenCalledWith('/api/tasks', expect.anything());
  });

  it('patches only the status and validates the returned task', async () => {
    const updated = { ...task, status: 'completed' };
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ ...updated, unexpected: true }));
    const signal = new AbortController().signal;

    await expect(
      createApiClient({ token, fetchImpl }).changeStatus(7, 'completed', signal),
    ).resolves.toEqual(updated);
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/tasks/7',
      expect.objectContaining({ method: 'PATCH', body: '{"status":"completed"}', signal }),
    );
    expect(new Headers(fetchImpl.mock.calls[0]?.[1]?.headers).get('Content-Type')).toBe(
      'application/json',
    );
  });

  it('deletes a task without parsing the 204 response', async () => {
    const response = new Response(null, { status: 204 });
    const json = vi.spyOn(response, 'json');
    const text = vi.spyOn(response, 'text');
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response);
    const signal = new AbortController().signal;

    await expect(
      createApiClient({ token, fetchImpl }).deleteTask(7, signal),
    ).resolves.toBeUndefined();
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/tasks/7',
      expect.objectContaining({ method: 'DELETE', signal }),
    );
    expect(json).not.toHaveBeenCalled();
    expect(text).not.toHaveBeenCalled();
  });

  it('posts a ticket request and validates the ticket', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ ticket: 'short-lived-ticket', expires_in: 60, unexpected: true }, 201),
      );

    await expect(createApiClient({ token, fetchImpl }).issueTicket()).resolves.toEqual({
      ticket: 'short-lived-ticket',
      expires_in: 60,
    });
    expect(fetchImpl).toHaveBeenCalledWith(
      '/api/realtime/tickets',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('sends its bearer token on every request', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(jsonResponse(task))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ ticket: 'ticket', expires_in: 60 }, 201));
    const client = createApiClient({ token, fetchImpl });

    await client.listTasks({});
    await client.changeStatus(7, 'pending');
    await client.deleteTask(7);
    await client.issueTicket();

    expect(
      fetchImpl.mock.calls.map(([, init]) => new Headers(init?.headers).get('Authorization')),
    ).toEqual(Array<string>(4).fill(`Bearer ${token}`));
  });

  it('uses the default fetch when no implementation is injected', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ items: [], next_cursor: null }));
    vi.stubGlobal('fetch', fetchImpl);

    await createApiClient({ token }).listTasks({});

    expect(fetchImpl).toHaveBeenCalledWith('/api/tasks', expect.anything());
  });

  it.each([
    ['page', { items: [task], next_cursor: 12 }],
    ['task', { ...task, status: 'unknown' }],
    ['ticket', { ticket: 'ticket', expires_in: '60' }],
  ])('rejects an invalid %s body with status 0', async (kind, body) => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body));
    const client = createApiClient({ token, fetchImpl });
    const request =
      kind === 'page'
        ? client.listTasks({})
        : kind === 'task'
          ? client.changeStatus(7, 'completed')
          : client.issueTicket();

    expect((await readError(request)).status).toBe(0);
  });

  it('rejects malformed success JSON with status 0', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response('not json'));

    expect((await readError(createApiClient({ token, fetchImpl }).listTasks({}))).status).toBe(0);
  });

  it.each([401, 404, 503])('preserves HTTP status %s and the validated detail', async (status) => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: 'Refused' }, status));

    const error = await readError(createApiClient({ token, fetchImpl }).deleteTask(7));

    expect(error.status).toBe(status);
    expect(error.detail).toBe('Refused');
  });

  it.each(['not json', '{"detail":12}'])(
    'preserves HTTP status with an invalid error body: %s',
    async (body) => {
      const fetchImpl = vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(body, { status: 401 }));

      const error = await readError(createApiClient({ token, fetchImpl }).issueTicket());

      expect(error.status).toBe(401);
      expect(error.detail).toEqual(expect.any(String));
    },
  );

  it('maps network failures to status 0 without retaining the original error', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockRejectedValue(new Error(`Network failed: ${token}`));

    const error = await readError(createApiClient({ token, fetchImpl }).listTasks({}));

    expect(error.status).toBe(0);
    expect(error).not.toHaveProperty('cause');
    expect(JSON.stringify(error)).not.toContain(token);
    expect(inspect(error, { showHidden: true })).not.toContain(token);
  });

  it('maps an invalid authorization header to a safe status 0 error', async () => {
    const fetchImpl = vi.fn<typeof fetch>();

    const error = await readError(
      createApiClient({ token: `${token}\ninvalid`, fetchImpl }).listTasks({}),
    );

    expect(error.status).toBe(0);
    expect(inspect(error, { showHidden: true })).not.toContain(token);
  });

  it('redacts the token from server error details', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        jsonResponse({ detail: `Invalid ${token}; Authorization: Bearer ${token}` }, 401),
      );

    const error = await readError(createApiClient({ token, fetchImpl }).issueTicket());

    expect(error.status).toBe(401);
    expect(JSON.stringify(error)).not.toContain(token);
    expect(inspect(error, { showHidden: true })).not.toContain(token);
  });

  it('does not expose invalid response data in its error', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ items: token }));

    const error = await readError(createApiClient({ token, fetchImpl }).listTasks({}));

    expect(error.status).toBe(0);
    expect(JSON.stringify(error)).not.toContain(token);
    expect(inspect(error, { showHidden: true })).not.toContain(token);
  });

  it('times out a pending ticket at 10 seconds', async () => {
    vi.useFakeTimers();
    const client = createApiClient({ token, fetchImpl: pendingFetch() });
    let settled = false;
    const result = readError(client.issueTicket()).then((error) => {
      settled = true;
      return error;
    });

    await vi.advanceTimersByTimeAsync(9_999);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect((await result).status).toBe(0);
  });

  it('issues a ticket when native signal composition is unavailable and cleans up on success', async () => {
    vi.useFakeTimers();
    const nativeAny = Object.getOwnPropertyDescriptor(AbortSignal, 'any');
    Object.defineProperty(AbortSignal, 'any', { configurable: true, value: undefined });
    const controller = new AbortController();
    const removeListener = vi.spyOn(controller.signal, 'removeEventListener');
    const client = createApiClient({
      token,
      fetchImpl: vi
        .fn<typeof fetch>()
        .mockResolvedValue(jsonResponse({ ticket: 'ticket', expires_in: 60 })),
    });
    try {
      await expect(client.issueTicket(controller.signal)).resolves.toEqual({
        ticket: 'ticket',
        expires_in: 60,
      });
      expect(vi.getTimerCount()).toBe(0);
      expect(removeListener).toHaveBeenCalledWith('abort', expect.any(Function));
    } finally {
      // Restore the global even when the request throws synchronously on the old implementation.
      if (nativeAny) Object.defineProperty(AbortSignal, 'any', nativeAny);
    }
  });

  it('allows the caller to abort a ticket before its timeout', async () => {
    const controller = new AbortController();
    const client = createApiClient({ token, fetchImpl: pendingFetch() });
    const result = readError(client.issueTicket(controller.signal));

    controller.abort(new Error(token));

    const error = await result;
    expect(error.status).toBe(0);
    expect(JSON.stringify(error)).not.toContain(token);
    expect(inspect(error, { showHidden: true })).not.toContain(token);
  });
});
