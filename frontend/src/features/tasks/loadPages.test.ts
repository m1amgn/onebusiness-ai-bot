import { describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from '../../api/client';

import { createPageLoader } from './loadPages';
import { visibleTasks } from './taskState';
import { harness, server, task } from './taskState.testHelpers';

const setup = (rows = [task(1)]) => {
  const board = harness();
  const source = server(rows);
  const onUnauthorized = vi.fn<() => void>();
  const onResyncFailure = vi.fn<() => void>();
  const loader = createPageLoader({
    ...board,
    client: source.client,
    onUnauthorized,
    onResyncFailure,
  });
  return { ...board, ...source, loader, onUnauthorized, onResyncFailure };
};

describe('page loading and common board frontier', () => {
  it('loads three first pages independently and loads more from the server cursor', async () => {
    const board = setup(Array.from({ length: 60 }, (_, index) => task(index + 1)));
    await board.loader.initialLoad();
    expect(board.client.listTasks.mock.calls.map(([query]) => query)).toEqual([
      { status: 'pending', limit: 50, cursor: null },
      { status: 'in_progress', limit: 50, cursor: null },
      { status: 'completed', limit: 50, cursor: null },
    ]);
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(50);
    expect(board.getState().arrived.size).toBe(0);
    board.dispatch({ type: 'task.created', task: task(1) });
    await board.loader.loadMore('pending');
    expect(board.client.listTasks).toHaveBeenLastCalledWith(
      { status: 'pending', limit: 50, cursor: '50' },
      expect.any(AbortSignal),
    );
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(60);
    expect(board.getState().columns.pending.complete).toBe(true);
    await board.loader.loadMore('pending');
    expect(board.client.listTasks).toHaveBeenCalledTimes(4);
    board.loader.stop();
  });

  it('G1 removes the offline-deleted oldest card after 50 new cards shift the shown window', async () => {
    const original = Array.from({ length: 50 }, (_, index) => task(index + 1));
    const board = setup(original);
    await board.loader.initialLoad();
    board.loader.captureFrontier();
    board.replace(Array.from({ length: 99 }, (_, index) => task(index + 2)));
    await board.loader.resync();
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(99);
    expect(board.getState().byId.has(1)).toBe(false);
    expect(board.getState().columns.pending.edge).toEqual({ inbound_message_id: 2, position: 0 });
    board.loader.stop();
  });

  it('G4 finds an offline move below more than 200 newer destination tasks', async () => {
    const original = Array.from({ length: 50 }, (_, index) => task(index + 1));
    const board = setup(original);
    await board.loader.initialLoad();
    board.loader.captureFrontier();
    board.replace([
      ...original.slice(1),
      task(1, 'completed', { updated_at: '2026-10-26T08:00:01Z' }),
      ...Array.from({ length: 230 }, (_, index) => task(index + 51, 'completed')),
    ]);
    await board.loader.resync();
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(49);
    expect(visibleTasks(board.getState(), 'completed')).toHaveLength(231);
    expect(board.getState().byId.get(1)?.status).toBe('completed');
    expect(
      board.client.listTasks.mock.calls.filter(([query]) => query.status === 'completed'),
    ).toHaveLength(3);
    board.loader.stop();
  });

  it('continues each chain until a row reaches the frontier, leaving the last opaque cursor', async () => {
    const board = setup([task(250)]);
    await board.loader.initialLoad();
    board.replace(Array.from({ length: 500 }, (_, index) => task(index + 1)));
    await board.loader.resync();
    expect(board.getState().columns.pending.cursor).toBe('400');
    expect(board.getState().columns.pending.edge).toEqual({ inbound_message_id: 101, position: 0 });
    expect(board.getState().columns.pending.complete).toBe(false);
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(400);
    board.loader.stop();
  });

  it('loads one page of 50 per column when no board frontier exists', async () => {
    const board = setup(Array.from({ length: 100 }, (_, index) => task(index + 1)));
    await board.loader.resync();
    expect(board.client.listTasks).toHaveBeenCalledTimes(3);
    expect(board.client.listTasks.mock.calls.every(([query]) => query.limit === 50)).toBe(true);
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(50);
    board.loader.stop();
  });

  it('retains a failed frontier and widens it to cards shown before the next open', async () => {
    const board = setup([task(20)]);
    await board.loader.initialLoad();
    board.loader.captureFrontier();
    board.client.listTasks.mockRejectedValueOnce(new ApiRequestError(503, 'Unavailable'));
    await board.loader.resync();
    expect(board.onResyncFailure).toHaveBeenCalledTimes(1);
    expect(board.getState().resync).toBeNull();
    expect(Object.values(board.getState().columns).every(({ loading }) => !loading)).toBe(true);
    board.dispatch({ type: 'task.created', task: task(1) });
    board.replace(Array.from({ length: 260 }, (_, index) => task(index + 2)));
    await board.loader.resync();
    expect(board.getState().byId.has(1)).toBe(false);
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(260);
    expect(board.getState().columns.pending.complete).toBe(true);
    board.loader.stop();
  });

  it('routes 401 once, retries a failed column, and prevents loading after stop', async () => {
    const board = setup();
    board.client.listTasks.mockRejectedValueOnce(new ApiRequestError(503, 'Unavailable'));
    await board.loader.initialLoad();
    expect(board.getState().columns.pending.error).toBe(true);
    await board.loader.retry('pending');
    expect(board.getState().columns.pending.error).toBe(false);
    board.client.listTasks.mockRejectedValue(new ApiRequestError(401, 'Unauthorized'));
    await board.loader.resync();
    expect(board.onUnauthorized).toHaveBeenCalledTimes(1);
    expect(board.onResyncFailure).not.toHaveBeenCalled();
    board.loader.stop();
    const count = board.client.listTasks.mock.calls.length;
    await board.loader.initialLoad();
    await board.loader.resync();
    await board.loader.retry('pending');
    expect(board.client.listTasks).toHaveBeenCalledTimes(count);
  });
});
