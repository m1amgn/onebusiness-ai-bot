import { describe, expect, it } from 'vitest';

import { boardFrontier, boardReducer, displayedStatus, visibleTasks } from './taskState';
import { harness, page, task } from './taskState.testHelpers';

describe('board rules', () => {
  it('retains the newest known row when an older logged event is absent from resync pages', () => {
    const board = harness();
    const newest = task(10, 'completed', { updated_at: '2026-10-26T08:00:02Z' });
    board.dispatch({ type: 'local.updated', task: newest });
    board.dispatch({
      type: 'resync.started',
      epoch: 1,
      frontier: { inbound_message_id: 10, position: 0 },
    });
    board.dispatch({ type: 'task.updated', task: task(10) });
    board.dispatch({
      type: 'resync.completed',
      epoch: 1,
      pages: { pending: page(), in_progress: page(), completed: page() },
    });
    expect(board.getState().byId.get(10)).toEqual(newest);
  });
  it('keeps event moves and unknown page rows below a non-null column edge in key order', () => {
    const board = harness();
    board.dispatch({ type: 'page.started', status: 'completed', epoch: 0, requestId: 1 });
    board.dispatch({
      type: 'page.received',
      status: 'completed',
      epoch: 0,
      requestId: 1,
      cursor: null,
      page: page([task(50, 'completed')], 'older'),
    });
    board.dispatch({ type: 'local.updated', task: task(10) });
    board.dispatch({
      type: 'task.updated',
      task: task(10, 'completed', { updated_at: '2026-10-26T08:00:01Z' }),
    });
    expect(board.getState().columns.completed.edge).toEqual({
      inbound_message_id: 50,
      position: 0,
    });
    expect(visibleTasks(board.getState(), 'completed').map(({ id }) => id)).toEqual([50, 10]);
    board.dispatch({ type: 'page.started', status: 'completed', epoch: 0, requestId: 2 });
    board.dispatch({
      type: 'page.received',
      status: 'completed',
      epoch: 0,
      requestId: 2,
      cursor: 'older',
      page: page([task(30, 'completed')], 'more'),
    });
    expect(visibleTasks(board.getState(), 'completed').map(({ id }) => id)).toEqual([50, 30, 10]);
  });
  it('merges strictly newer microseconds and sorts message descending, position ascending', () => {
    const board = harness();
    const old = task(1);
    board.dispatch({ type: 'local.updated', task: old });
    const before = board.getState();
    const newer = task(1, 'completed', { updated_at: '2026-10-26T08:00:00.000001Z' });
    board.dispatch({ type: 'task.updated', task: newer });
    board.dispatch({ type: 'task.updated', task: old });
    board.dispatch({ type: 'task.created', task: newer });
    expect(before.byId.get(1)).toEqual(old);
    expect(board.getState().byId.get(1)).toEqual(newer);
    expect(board.getState().arrived.size).toBe(0);
    for (const row of [
      task(3),
      task(4),
      task(5, 'pending', { inbound_message_id: 4, position: 1 }),
    ]) {
      board.dispatch({ type: 'local.updated', task: row });
    }
    expect(visibleTasks(board.getState(), 'pending').map(({ id }) => id)).toEqual([4, 5, 3]);
  });

  it('keeps lifetime tombstones from events and local 204 or 404 results', () => {
    const board = harness();
    for (const id of [1, 2]) board.dispatch({ type: 'local.updated', task: task(id) });
    board.dispatch({ type: 'task.deleted', task_id: 1 });
    board.dispatch({ type: 'local.removed', id: 2 });
    for (const id of [1, 2]) {
      board.dispatch({ type: 'task.created', task: task(id) });
      board.dispatch({ type: 'local.updated', task: task(id) });
    }
    expect(board.getState().byId.size).toBe(0);
    expect([...board.getState().deleted]).toEqual([1, 2]);
  });

  it('marks only newly created live tasks and removes the mark after animation or deletion', () => {
    const board = harness();
    board.dispatch({ type: 'local.updated', task: task(1) });
    board.dispatch({ type: 'task.created', task: task(1) });
    board.dispatch({ type: 'task.created', task: task(2) });
    board.dispatch({ type: 'task.created', task: task(3) });
    expect([...board.getState().arrived]).toEqual([2, 3]);
    board.dispatch({ type: 'arrival.finished', id: 2 });
    board.dispatch({ type: 'task.deleted', task_id: 3 });
    expect(board.getState().arrived.size).toBe(0);
  });

  it('shows an optimistic card below the loaded range and preserves its newest queued target', () => {
    const board = harness();
    const row = task(1);
    board.dispatch({ type: 'local.updated', task: row });
    board.dispatch({ type: 'move.target', id: 1, to: 'in_progress' });
    board.dispatch({ type: 'move.target', id: 1, to: 'completed' });
    board.dispatch({
      type: 'local.updated',
      task: task(1, 'in_progress', { updated_at: '2026-10-26T08:00:01Z' }),
    });
    board.dispatch({ type: 'move.finished', id: 1 });
    expect(displayedStatus(board.getState(), row)).toBe('completed');
    expect(board.getState().moves.get(1)).toEqual({ to: 'completed' });
    expect(visibleTasks(board.getState(), 'completed')).toHaveLength(1);
    board.dispatch({
      type: 'local.updated',
      task: task(1, 'completed', { updated_at: '2026-10-26T08:00:02Z' }),
    });
    board.dispatch({ type: 'move.finished', id: 1 });
    expect(board.getState().moves.size).toBe(0);
  });

  it('rolls a failed move back, keeps failed deletions, and clears errors on retry', () => {
    const board = harness();
    board.dispatch({ type: 'local.updated', task: task(1) });
    board.dispatch({ type: 'move.target', id: 1, to: 'completed' });
    board.dispatch({ type: 'move.failed', id: 1 });
    expect(visibleTasks(board.getState(), 'pending')).toHaveLength(1);
    expect(board.getState().cardErrors.get(1)).toBe('move');
    board.dispatch({ type: 'delete.started', id: 1 });
    board.dispatch({ type: 'move.target', id: 1, to: 'completed' });
    expect(board.getState().moves.size).toBe(0);
    expect(board.getState().deleting.has(1)).toBe(true);
    board.dispatch({ type: 'delete.failed', id: 1 });
    expect(board.getState().cardErrors.get(1)).toBe('delete');
    expect(board.getState().deleting.size).toBe(0);
    board.dispatch({ type: 'move.target', id: 1, to: 'completed' });
    expect(board.getState().cardErrors.size).toBe(0);
    board.dispatch({ type: 'local.removed', id: 1 });
    expect(board.getState().moves.size).toBe(0);
  });

  it('gives a confirmed delete precedence over an in-flight move and its queued target', () => {
    const board = harness();
    board.dispatch({ type: 'local.updated', task: task(1) });
    board.dispatch({ type: 'move.target', id: 1, to: 'in_progress' });
    board.dispatch({ type: 'move.target', id: 1, to: 'completed' });
    board.dispatch({ type: 'delete.started', id: 1 });
    expect(board.getState().deleting.has(1)).toBe(true);
    expect(board.getState().moves.size).toBe(0);
    board.dispatch({ type: 'local.removed', id: 1 });
    board.dispatch({
      type: 'local.updated',
      task: task(1, 'in_progress', { updated_at: '2026-10-26T08:00:01Z' }),
    });
    board.dispatch({ type: 'move.finished', id: 1 });
    expect(board.getState().byId.size).toBe(0);
    expect(board.getState().deleting.size).toBe(0);
  });

  it('ignores stale epochs and cursors without clearing a newer request owner', () => {
    const board = harness();
    board.dispatch({ type: 'page.started', status: 'pending', epoch: 0, requestId: 1 });
    board.dispatch({ type: 'resync.started', epoch: 1, frontier: null });
    board.dispatch({ type: 'page.started', status: 'pending', epoch: 1, requestId: 2 });
    board.dispatch({
      type: 'page.received',
      status: 'pending',
      epoch: 0,
      requestId: 1,
      cursor: null,
      page: page([task(1)], 'old'),
    });
    board.dispatch({ type: 'page.finished', status: 'pending', epoch: 0, requestId: 1 });
    expect(board.getState().columns.pending.loading).toBe(true);
    board.dispatch({
      type: 'page.received',
      status: 'pending',
      epoch: 1,
      requestId: 2,
      cursor: 'wrong',
      page: page([task(2)], 'wrong'),
    });
    board.dispatch({ type: 'page.finished', status: 'pending', epoch: 1, requestId: 2 });
    expect(board.getState().columns.pending.loading).toBe(false);
    expect(board.getState().columns.pending.cursor).toBeNull();
    expect(board.getState().byId.size).toBe(0);
    expect(boardReducer(board.getState(), { type: 'auth.revoked' })).toBe(board.getState());
  });

  it('captures the oldest edge or shown event card as the common board frontier', () => {
    const board = harness();
    board.dispatch({ type: 'page.started', status: 'pending', epoch: 0, requestId: 1 });
    board.dispatch({
      type: 'page.received',
      status: 'pending',
      epoch: 0,
      requestId: 1,
      cursor: null,
      page: page([task(5)], 'next'),
    });
    board.dispatch({ type: 'task.created', task: task(2, 'completed') });
    expect(boardFrontier(board.getState())).toEqual({ inbound_message_id: 2, position: 0 });
  });

  it('accepts late updates after snapshot pruning while explicit tombstones stay final', () => {
    const board = harness();
    for (const id of [1, 2]) board.dispatch({ type: 'local.updated', task: task(id) });
    board.dispatch({ type: 'resync.started', epoch: 1, frontier: boardFrontier(board.getState()) });
    board.dispatch({ type: 'task.deleted', task_id: 2 });
    board.dispatch({
      type: 'resync.completed',
      epoch: 1,
      pages: { pending: page(), in_progress: page(), completed: page() },
    });
    expect(board.getState().byId.size).toBe(0);
    expect(board.getState().deleted.has(1)).toBe(false);
    for (const id of [1, 2]) {
      board.dispatch({
        type: 'task.updated',
        task: task(id, 'completed', { updated_at: '2026-10-26T08:00:01Z' }),
      });
    }
    expect(visibleTasks(board.getState(), 'completed').map(({ id }) => id)).toEqual([1]);
  });
});
