import { vi } from 'vitest';

import type { ApiClient } from '../../api/client';
import type { TaskPage, TaskStatus, TaskView } from '../../api';

import { boardReducer, createBoardState } from './taskState';
import type { BoardAction } from './taskState';

export const task = (
  id: number,
  status: TaskStatus = 'pending',
  overrides: Partial<TaskView> = {},
): TaskView => ({
  id,
  inbound_message_id: id,
  position: 0,
  title: `Task ${String(id)}`,
  description: '',
  source_text: 'A test task.',
  status,
  created_at: '2026-10-26T08:00:00Z',
  updated_at: '2026-10-26T08:00:00Z',
  ...overrides,
});

export const page = (items: TaskView[] = [], next_cursor: string | null = null): TaskPage => ({
  items,
  next_cursor,
});

export const harness = () => {
  let state = createBoardState();
  const dispatch = (action: BoardAction): void => {
    state = boardReducer(state, action);
  };
  return { getState: () => state, dispatch };
};

export const server = (initial: TaskView[] = []) => {
  let tasks = initial;
  const listTasks = vi.fn<ApiClient['listTasks']>((query) => {
    const items = tasks
      .filter((item) => item.status === query.status)
      .sort((a, b) => b.inbound_message_id - a.inbound_message_id || a.position - b.position);
    const start = Number(query.cursor ?? 0);
    const end = start + (query.limit ?? 50);
    return Promise.resolve(page(items.slice(start, end), end < items.length ? String(end) : null));
  });
  return { client: { listTasks }, replace: (rows: TaskView[]) => (tasks = rows) };
};

export const deferred = <T>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((accept, decline) => {
    resolve = accept;
    reject = decline;
  });
  return { promise, resolve, reject };
};
