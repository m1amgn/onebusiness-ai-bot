import { act, renderHook, waitFor as waitForReact } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { TaskStatus } from '../../api';

import { deferred, page, task } from './taskState.testHelpers';
import { displayedStatus } from './taskState';
import { useTaskBoard } from './useTaskBoard';

const waitFor: typeof waitForReact = (callback, options) =>
  waitForReact(callback, { timeout: 5_000, ...options });

class FakeSource {
  static current: FakeSource | undefined;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  constructor() {
    FakeSource.current = this;
  }
  close = (): void => undefined;
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status });
const responseTask = (status: TaskStatus, updated_at = '2026-10-26T08:00:00.000001Z'): Response =>
  json(task(1, status, { updated_at }));
const reply = async (
  pending: ReturnType<typeof deferred<Response>>,
  response: Response,
): Promise<void> => {
  await act(async () => {
    pending.resolve(response);
    await pending.promise;
  });
};
const setup = (write: (method: string, init: RequestInit) => Promise<Response>) => {
  const fetchImpl = vi.fn<typeof fetch>((input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url === '/api/realtime/tickets')
      return Promise.resolve(json({ ticket: 'ticket', expires_in: 60 }, 201));
    if (init?.method === 'GET')
      return Promise.resolve(json(page(url.includes('status=pending') ? [task(1)] : [])));
    return write(init?.method ?? '', init ?? {});
  });
  vi.stubGlobal('fetch', fetchImpl);
  const onUnauthorized = vi.fn();
  const view = renderHook(() => useTaskBoard('mount-token', onUnauthorized));
  const requests = (method: string) =>
    fetchImpl.mock.calls.filter(([, init]) => init?.method === method);
  const ready = async (): Promise<void> => {
    await waitFor(() => {
      expect(view.result.current.state.byId.has(1)).toBe(true);
    });
  };
  const status = () => {
    const row = view.result.current.state.byId.get(1);
    return row && displayedStatus(view.result.current.state, row);
  };
  return { ...view, onUnauthorized, requests, ready, status };
};

beforeEach(() => {
  FakeSource.current = undefined;
  vi.stubGlobal('EventSource', FakeSource);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('useTaskBoard actions', { timeout: 15_000 }, () => {
  it('moves immediately and serializes only the latest queued target', async () => {
    const first = deferred<Response>();
    const second = deferred<Response>();
    const writes = [first, second];
    const board = setup(
      () => writes.shift()?.promise ?? Promise.reject(new Error('Unexpected write')),
    );
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
      board.result.current.handleMove(1, 'completed');
      board.result.current.handleMove(1, 'pending');
    });
    expect(board.status()).toBe('pending');
    expect(board.requests('PATCH')).toHaveLength(1);
    expect(board.requests('PATCH')[0]?.[1]?.body).toBe('{"status":"in_progress"}');
    await reply(first, responseTask('in_progress'));
    expect(board.requests('PATCH')).toHaveLength(2);
    expect(board.requests('PATCH')[1]?.[1]?.body).toBe('{"status":"pending"}');
    await reply(second, responseTask('pending', '2026-10-26T08:00:00.000002Z'));
    expect(board.status()).toBe('pending');
    expect(board.result.current.state.moves.size).toBe(0);
  });

  it('uses the current merged task to decide whether a queued write is needed', async () => {
    const pending = deferred<Response>();
    const board = setup(() => pending.promise);
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
      board.result.current.handleMove(1, 'completed');
    });
    act(() =>
      FakeSource.current?.onmessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'task.updated',
            task: task(1, 'completed', { updated_at: '2026-10-26T08:00:00.000003Z' }),
          }),
        }),
      ),
    );
    await reply(pending, responseTask('in_progress'));
    expect(board.status()).toBe('completed');
    expect(board.requests('PATCH')).toHaveLength(1);
    expect(board.result.current.state.moves.size).toBe(0);
  });

  it('rolls back a failed move and clears the error when the user tries again', async () => {
    const retry = deferred<Response>();
    let first = true;
    const board = setup(() => {
      if (!first) return retry.promise;
      first = false;
      return Promise.resolve(json({ detail: 'Unavailable' }, 503));
    });
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'completed');
    });
    expect(board.status()).toBe('completed');
    await waitFor(() => {
      expect(board.result.current.state.cardErrors.get(1)).toBe('move');
    });
    expect(board.status()).toBe('pending');
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
    });
    expect(board.result.current.state.cardErrors.has(1)).toBe(false);
    await reply(retry, responseTask('in_progress'));
    expect(board.status()).toBe('in_progress');
  });

  it('removes a missing task after PATCH and ignores later move requests', async () => {
    const board = setup(() => Promise.resolve(json({ detail: 'Missing' }, 404)));
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'completed');
    });
    await waitFor(() => {
      expect(board.result.current.state.deleted.has(1)).toBe(true);
    });
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
    });
    expect(board.result.current.state.byId.has(1)).toBe(false);
    expect(board.requests('PATCH')).toHaveLength(1);
  });

  it.each([204, 404])(
    'waits for DELETE %s, disables the card and discards queued moves',
    async (status) => {
      const patch = deferred<Response>();
      const deletion = deferred<Response>();
      const board = setup((method) => (method === 'PATCH' ? patch.promise : deletion.promise));
      await board.ready();
      act(() => {
        board.result.current.handleMove(1, 'in_progress');
        board.result.current.handleMove(1, 'completed');
        board.result.current.handleDelete(1);
        board.result.current.handleDelete(1);
        board.result.current.handleMove(1, 'completed');
      });
      expect(board.result.current.state.deleting.has(1)).toBe(true);
      expect(board.result.current.state.byId.has(1)).toBe(true);
      expect(board.result.current.state.moves.size).toBe(0);
      expect(board.requests('DELETE')).toHaveLength(1);
      await reply(patch, responseTask('in_progress'));
      expect(board.requests('PATCH')).toHaveLength(1);
      expect(board.result.current.state.deleting.has(1)).toBe(true);
      await reply(
        deletion,
        status === 204 ? new Response(null, { status }) : json({ detail: 'Missing' }, status),
      );
      expect(board.result.current.state.byId.has(1)).toBe(false);
      expect(board.result.current.state.deleted.has(1)).toBe(true);
    },
  );

  it('keeps a failed delete and does not overlap the previous active PATCH', async () => {
    const patch = deferred<Response>();
    const second = deferred<Response>();
    const patches = [patch, second];
    const board = setup((method) =>
      method === 'PATCH'
        ? (patches.shift()?.promise ?? Promise.reject(new Error('Unexpected PATCH')))
        : Promise.resolve(json({ detail: 'Unavailable' }, 503)),
    );
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
      board.result.current.handleDelete(1);
    });
    await waitFor(() => {
      expect(board.result.current.state.cardErrors.get(1)).toBe('delete');
    });
    expect(board.result.current.state.deleting.has(1)).toBe(false);
    expect(board.result.current.state.byId.has(1)).toBe(true);
    act(() => {
      board.result.current.handleMove(1, 'completed');
    });
    expect(board.requests('PATCH')).toHaveLength(1);
    expect(board.status()).toBe('completed');
    await reply(patch, responseTask('in_progress'));
    expect(board.requests('PATCH')).toHaveLength(2);
    await reply(second, responseTask('completed', '2026-10-26T08:00:00.000002Z'));
    expect(board.status()).toBe('completed');
  });

  it('does not resurrect a deleted task when its PATCH settles later', async () => {
    const patch = deferred<Response>();
    const board = setup((method) =>
      method === 'PATCH' ? patch.promise : Promise.resolve(new Response(null, { status: 204 })),
    );
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
      board.result.current.handleDelete(1);
    });
    await waitFor(() => {
      expect(board.result.current.state.deleted.has(1)).toBe(true);
    });
    await reply(patch, responseTask('in_progress'));
    expect(board.result.current.state.byId.has(1)).toBe(false);
    expect(board.requests('PATCH')).toHaveLength(1);
  });

  it.each(['PATCH', 'DELETE'])('reports the mount token for unauthorized %s', async (method) => {
    const board = setup(() => Promise.resolve(json({ detail: 'Expired' }, 401)));
    await board.ready();
    act(() => {
      if (method === 'PATCH') board.result.current.handleMove(1, 'completed');
      else board.result.current.handleDelete(1);
    });
    await waitFor(() => {
      expect(board.onUnauthorized).toHaveBeenCalledWith('mount-token');
    });
  });

  it('aborts writes and ignores late authorization failures after unmount', async () => {
    const pending = deferred<Response>();
    const board = setup(() => pending.promise);
    await board.ready();
    act(() => {
      board.result.current.handleMove(1, 'in_progress');
      board.result.current.handleDelete(1);
    });
    const signals = [...board.requests('PATCH'), ...board.requests('DELETE')].map(
      ([, init]) => init?.signal,
    );
    board.unmount();
    expect(signals.every((signal) => signal?.aborted)).toBe(true);
    await reply(pending, json({ detail: 'Expired' }, 401));
    expect(board.onUnauthorized).not.toHaveBeenCalled();
    expect(board.requests('PATCH')).toHaveLength(1);
  });

  it('ignores unknown tasks and moves to the displayed column', async () => {
    const board = setup(() => Promise.reject(new Error('Unexpected write')));
    await board.ready();
    act(() => {
      board.result.current.handleMove(99, 'completed');
      board.result.current.handleDelete(99);
      board.result.current.handleMove(1, 'pending');
    });
    expect(board.requests('PATCH')).toHaveLength(0);
    expect(board.requests('DELETE')).toHaveLength(0);
  });
});
