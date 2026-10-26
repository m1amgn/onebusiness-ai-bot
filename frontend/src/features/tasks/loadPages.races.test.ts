import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../../api/client';
import type { TaskPage } from '../../api';

import { createPageLoader } from './loadPages';
import { visibleTasks } from './taskState';
import { deferred, harness, page, server, task } from './taskState.testHelpers';

const pendingSetup = () => {
  const board = harness();
  const requests: { result: ReturnType<typeof deferred<TaskPage>>; signal?: AbortSignal }[] = [];
  const listTasks = vi.fn<ApiClient['listTasks']>((_query, signal) => {
    const result = deferred<TaskPage>();
    requests.push({ result, signal });
    return result.promise;
  });
  const loader = createPageLoader({
    ...board,
    client: { listTasks },
    onUnauthorized: vi.fn(),
    onResyncFailure: vi.fn(),
  });
  const resolve = (start: number, pending: TaskPage = page()): void => {
    requests[start]?.result.resolve(pending);
    requests[start + 1]?.result.resolve(page());
    requests[start + 2]?.result.resolve(page());
  };
  return { ...board, loader, requests, listTasks, resolve };
};

describe('page request races', () => {
  it('blocks load-more during resync even when its column has a usable cursor', async () => {
    const board = pendingSetup();
    const initial = board.loader.initialLoad();
    board.resolve(0, page([task(50)], 'older'));
    await initial;
    const sync = board.loader.resync();
    expect(board.getState().columns.pending.cursor).toBe('older');
    const more = board.loader.loadMore('pending');
    expect(board.listTasks).toHaveBeenCalledTimes(6);
    board.resolve(3, page([task(50)]));
    await sync;
    await more;
    board.loader.stop();
  });
  it('G2 ignores a delayed initial page after resync deletes a task before subscription', async () => {
    const board = pendingSetup();
    const initial = board.loader.initialLoad();
    const sync = board.loader.resync();
    expect(board.requests.slice(0, 3).every(({ signal }) => signal?.aborted)).toBe(true);
    board.resolve(3);
    await sync;
    board.resolve(0, page([task(1)], 'obsolete'));
    await initial;
    expect(board.getState().byId.size).toBe(0);
    expect(board.getState().columns.pending.cursor).toBeNull();
    expect(Object.values(board.getState().columns).every(({ loading }) => !loading)).toBe(true);
    board.loader.stop();
  });

  it('ignores superseded resyncs and prevents old finalizers from clearing new loading owners', async () => {
    const board = pendingSetup();
    const old = board.loader.resync();
    const latest = board.loader.resync();
    board.resolve(0, page([task(1)]));
    await old;
    expect(board.getState().columns.pending.loading).toBe(true);
    board.resolve(3, page([task(2)]));
    await latest;
    expect([...board.getState().byId.keys()]).toEqual([2]);
    expect(board.getState().columns.pending.loading).toBe(false);
    board.loader.stop();
  });

  it('replays events and local writes, retains moves, and reconciles event cards outside pages', async () => {
    const board = pendingSetup();
    for (const row of [task(10), task(9), task(8), task(2)]) {
      board.dispatch({ type: 'local.updated', task: row });
    }
    board.dispatch({ type: 'task.created', task: task(3) });
    board.dispatch({ type: 'task.created', task: task(1) });
    board.dispatch({ type: 'move.target', id: 8, to: 'completed' });
    const sync = board.loader.resync();
    expect(board.listTasks).toHaveBeenCalledTimes(3);
    board.dispatch({ type: 'task.created', task: task(20) });
    board.dispatch({ type: 'task.deleted', task_id: 10 });
    board.dispatch({
      type: 'local.updated',
      task: task(9, 'completed', { updated_at: '2026-10-26T08:00:02Z' }),
    });
    board.dispatch({ type: 'local.removed', id: 2 });
    board.resolve(0, page([task(10), task(9), task(3), task(2)]));
    await sync;
    expect(visibleTasks(board.getState(), 'pending').map(({ id }) => id)).toEqual([20, 3]);
    expect(visibleTasks(board.getState(), 'completed').map(({ id }) => id)).toEqual([9, 8]);
    expect(board.getState().byId.has(1)).toBe(false);
    expect(board.getState().arrived.has(1)).toBe(false);
    expect(board.getState().arrived.has(20)).toBe(true);
    expect(board.getState().moves.get(8)?.to).toBe('completed');
    board.loader.stop();
  });

  it('does not let an aborted load-more restore a tombstone or roll its cursor back', async () => {
    const board = pendingSetup();
    const initial = board.loader.initialLoad();
    board.resolve(0, page([task(3)], 'next'));
    await initial;
    const more = board.loader.loadMore('pending');
    const sync = board.loader.resync();
    board.requests[4]?.result.resolve(page([task(4)]));
    board.requests[5]?.result.resolve(page());
    board.requests[6]?.result.resolve(page());
    await sync;
    board.requests[3]?.result.resolve(page([task(3)], 'obsolete'));
    await more;
    expect(board.getState().byId.has(3)).toBe(false);
    expect(board.getState().columns.pending.cursor).toBeNull();
    expect(board.getState().columns.pending.loading).toBe(false);
    board.loader.stop();
  });

  it('clears owned loading immediately on stop even when fetch ignores abort', async () => {
    const board = pendingSetup();
    const initial = board.loader.initialLoad();
    board.loader.stop();
    expect(board.requests.every(({ signal }) => signal?.aborted)).toBe(true);
    expect(Object.values(board.getState().columns).every(({ loading }) => !loading)).toBe(true);
    board.resolve(0, page([task(1)]));
    await initial;
    expect(board.getState().byId.size).toBe(0);
  });

  it('starts independent status requests together before any list promise settles', async () => {
    const board = harness();
    const source = server();
    const pending = deferred<TaskPage>();
    source.client.listTasks.mockImplementation(() => pending.promise);
    const loader = createPageLoader({
      ...board,
      client: source.client,
      onUnauthorized: vi.fn(),
      onResyncFailure: vi.fn(),
    });
    const initial = loader.initialLoad();
    expect(source.client.listTasks).toHaveBeenCalledTimes(3);
    pending.resolve(page());
    await initial;
    loader.stop();
  });
});
