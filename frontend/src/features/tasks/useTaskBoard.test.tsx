import { act, renderHook, waitFor as waitForReact } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ServerEvent } from '../../api';

import { page, task } from './taskState.testHelpers';
import { visibleTasks } from './taskState';
import { useTaskBoard } from './useTaskBoard';

const waitFor: typeof waitForReact = (callback, options) =>
  waitForReact(callback, { timeout: 5_000, ...options });

class FakeSource {
  static instances: FakeSource[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSource.instances.push(this);
  }

  close = (): void => {
    this.closed = true;
  };
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status });

const setup = (respond?: (url: URL, init: RequestInit) => Response | undefined) => {
  const fetchImpl = vi.fn<typeof fetch>((input, init) => {
    const url = new URL(
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
      'http://localhost',
    );
    const response = respond?.(url, init ?? {});
    if (response) return Promise.resolve(response);
    if (url.pathname === '/api/realtime/tickets')
      return Promise.resolve(json({ ticket: 'test-ticket', expires_in: 60 }, 201));
    const status = url.searchParams.get('status');
    return Promise.resolve(
      json(
        page(
          [task(3), task(2, 'in_progress'), task(1, 'completed')].filter(
            (row) => row.status === status,
          ),
        ),
      ),
    );
  });
  vi.stubGlobal('fetch', fetchImpl);
  const onUnauthorized = vi.fn();
  const view = renderHook(() => useTaskBoard('mount-token', onUnauthorized));
  const requests = (method: string) =>
    fetchImpl.mock.calls.filter(([, init]) => init?.method === method);
  const ready = async (): Promise<FakeSource> => {
    await waitFor(() => {
      expect(view.result.current.state.byId.size).toBe(3);
    });
    const source = FakeSource.instances[0];
    if (!source) throw new Error('Expected the real stream to create its native source.');
    return source;
  };
  return { ...view, fetchImpl, onUnauthorized, requests, ready };
};

const emit = (source: FakeSource, event: ServerEvent): void => {
  act(() => source.onmessage?.(new MessageEvent('message', { data: JSON.stringify(event) })));
};

beforeEach(() => {
  FakeSource.instances = [];
  vi.stubGlobal('EventSource', FakeSource);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('useTaskBoard lifecycle', { timeout: 15_000 }, () => {
  it('loads independently and creates one ticket and source under StrictMode', async () => {
    const board = setup();
    const source = await board.ready();

    expect(board.requests('POST')).toHaveLength(1);
    expect(FakeSource.instances).toHaveLength(1);
    expect(board.requests('GET')).toHaveLength(3);
    expect(board.result.current.connectionStatus).toBe('connecting');
    expect(visibleTasks(board.result.current.state, 'pending').map((row) => row.id)).toEqual([3]);
    expect(visibleTasks(board.result.current.state, 'in_progress').map((row) => row.id)).toEqual([
      2,
    ]);
    expect(visibleTasks(board.result.current.state, 'completed').map((row) => row.id)).toEqual([1]);

    act(() => source.onopen?.(new Event('open')));
    await waitFor(() => {
      expect(board.result.current.state.resync).toBeNull();
    });
    expect(board.requests('GET')).toHaveLength(6);
    expect(board.result.current.state.epoch).toBe(1);
    expect(board.result.current.state.columns.pending.loading).toBe(false);
    expect(board.result.current.connectionStatus).toBe('live');
    expect(
      board.fetchImpl.mock.calls.every(
        ([, init]) => new Headers(init?.headers).get('Authorization') === 'Bearer mount-token',
      ),
    ).toBe(true);
  });

  it('orders live changes, marks arrivals and keeps deletion tombstones', async () => {
    const board = setup();
    const source = await board.ready();
    emit(source, { type: 'task.created', task: task(4) });
    expect(visibleTasks(board.result.current.state, 'pending').map((row) => row.id)).toEqual([
      4, 3,
    ]);
    expect(board.result.current.state.arrived.has(4)).toBe(true);
    act(() => {
      board.result.current.handleArrivalEnd(4);
    });
    expect(board.result.current.state.arrived.has(4)).toBe(false);

    emit(source, {
      type: 'task.updated',
      task: task(4, 'completed', { updated_at: '2026-10-26T08:00:00.000002Z' }),
    });
    emit(source, {
      type: 'task.updated',
      task: task(4, 'in_progress', { updated_at: '2026-10-26T08:00:00.000001Z' }),
    });
    expect(board.result.current.state.byId.get(4)?.status).toBe('completed');
    emit(source, { type: 'task.deleted', task_id: 4 });
    emit(source, {
      type: 'task.created',
      task: task(4, 'pending', { updated_at: '2026-10-26T09:00:00Z' }),
    });
    expect(board.result.current.state.byId.has(4)).toBe(false);
    expect(board.result.current.state.deleted.has(4)).toBe(true);
  });

  it('loads the next cursor once and retries a failed initial column', async () => {
    let fail = true;
    const board = setup((url) => {
      if (url.searchParams.get('status') !== 'pending') return;
      if (fail) return json({ detail: 'Unavailable' }, 503);
      return json(url.searchParams.has('cursor') ? page([task(0)]) : page([task(3)], 'next'));
    });
    await waitFor(() => {
      expect(board.result.current.state.columns.pending.error).toBe(true);
    });
    fail = false;
    act(() => {
      board.result.current.handleRetry('pending');
    });
    await waitFor(() => {
      expect(board.result.current.state.columns.pending.cursor).toBe('next');
    });
    act(() => {
      board.result.current.handleLoadMore('pending');
      board.result.current.handleLoadMore('pending');
    });
    await waitFor(() => {
      expect(board.result.current.state.byId.has(0)).toBe(true);
    });
    expect(
      board
        .requests('GET')
        .filter(([url]) => typeof url === 'string' && url.includes('cursor=next')),
    ).toHaveLength(1);
  });

  it('reconnects once after a source error and resyncs the new source', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    const board = setup();
    const source = await board.ready();
    act(() => source.onopen?.(new Event('open')));
    await waitFor(() => {
      expect(board.requests('GET')).toHaveLength(6);
    });
    act(() => source.onerror?.(new Event('error')));
    expect(source.closed).toBe(true);
    expect(board.result.current.connectionStatus).toBe('reconnecting');
    await waitFor(() => {
      expect(FakeSource.instances).toHaveLength(2);
    });
    const replacement = FakeSource.instances[1];
    act(() => replacement?.onopen?.(new Event('open')));
    await waitFor(() => {
      expect(board.requests('GET')).toHaveLength(9);
    });
    expect(board.requests('POST')).toHaveLength(2);
  });

  it('requests another connection when a resync fails or the browser returns online', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    let fail = false;
    const board = setup((url) =>
      fail && url.pathname === '/api/tasks' ? json({ detail: 'Unavailable' }, 503) : undefined,
    );
    const source = await board.ready();
    fail = true;
    act(() => source.onopen?.(new Event('open')));
    await waitFor(() => {
      expect(board.result.current.connectionStatus).toBe('reconnecting');
    });
    expect(board.result.current.state.byId.size).toBe(3);
    await waitFor(() => {
      expect(FakeSource.instances).toHaveLength(2);
    });
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    expect(FakeSource.instances[1]?.closed).toBe(true);
    await waitFor(() => {
      expect(FakeSource.instances).toHaveLength(3);
    });
  });

  it('cleans the deferred start and removes the online listener on unmount', async () => {
    const early = setup();
    early.unmount();
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(early.fetchImpl).not.toHaveBeenCalled();
    const board = setup();
    const source = await board.ready();
    board.unmount();
    const count = board.fetchImpl.mock.calls.length;
    act(() => {
      window.dispatchEvent(new Event('online'));
    });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(source.closed).toBe(true);
    expect(source.onmessage).toBeNull();
    expect(board.fetchImpl).toHaveBeenCalledTimes(count);
  });

  it.each(['GET', 'POST'])('reports the mount token for an unauthorized %s', async (method) => {
    const board = setup((_url, init) =>
      init.method === method ? json({ detail: 'Expired' }, 401) : undefined,
    );
    await waitFor(() => {
      expect(board.onUnauthorized).toHaveBeenCalledWith('mount-token');
    });
  });
});
