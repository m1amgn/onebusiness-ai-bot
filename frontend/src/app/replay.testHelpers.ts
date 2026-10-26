import { within } from '@testing-library/react';
import { expect, vi } from 'vitest';

import ownerTasks from './s7Tasks.fixture.json';

export const fixtureTask = (id: number) => {
  const task = ownerTasks.find((row) => row.id === id);
  if (!task) throw new Error(`Missing owner task A${String(id)}`);
  return task;
};

export class BrowserStream {
  static instances: BrowserStream[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor() {
    BrowserStream.instances.push(this);
    queueMicrotask(() => this.onopen?.(new Event('open')));
  }

  close = (): void => {
    this.closed = true;
  };

  static broadcast = (body: unknown): void => {
    for (const source of BrowserStream.instances) {
      if (!source.closed)
        source.onmessage?.(new MessageEvent('message', { data: JSON.stringify(body) }));
    }
  };
}

export class BrowserDataTransfer {
  private data = new Map<string, string>();
  effectAllowed = 'uninitialized';
  dropEffect = 'none';
  get types(): string[] {
    return [...this.data.keys()];
  }
  setData = (format: string, data: string): void => {
    this.data.set(format, data);
  };
  getData = (format: string): string => this.data.get(format) ?? '';
}

export const createReplayServer = () => {
  const tasks = new Map(ownerTasks.map((task) => [task.id, { ...task }]));
  let pendingWrite: (() => void) | undefined;
  let revoked = false;
  const requests = { patch: 0, delete: 0, deniedTickets: 0 };
  const fetchImpl = vi.fn<typeof fetch>((input, init) => {
    const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(path, window.location.origin);
    if (url.pathname === '/api/realtime/tickets') {
      if (!revoked)
        return Promise.resolve(Response.json({ ticket: 'synthetic-ticket', expires_in: 30 }));
      requests.deniedTickets += 1;
      return Promise.resolve(
        Response.json({ detail: 'Send /dashboard to the bot for a new link' }, { status: 401 }),
      );
    }
    if (init?.method === 'GET') {
      const items = [...tasks.values()]
        .filter((task) => task.status === url.searchParams.get('status'))
        .sort((left, right) => right.inbound_message_id - left.inbound_message_id);
      return Promise.resolve(Response.json({ items, next_cursor: null }));
    }
    const id = Number(url.pathname.split('/').at(-1));
    const task = tasks.get(id);
    if (!task) throw new Error(`Unexpected task request ${url.pathname}`);
    if (pendingWrite) throw new Error('A replay write is already pending');
    if (init?.method === 'PATCH') {
      requests.patch += 1;
      if (typeof init.body !== 'string') throw new Error('PATCH requires a JSON string');
      const body: unknown = JSON.parse(init.body);
      if (!body || typeof body !== 'object' || !('status' in body))
        throw new Error('Missing status in PATCH');
      const status = body.status;
      if (status !== 'pending' && status !== 'in_progress' && status !== 'completed')
        throw new Error('Unexpected PATCH status');
      const updated = { ...task, status, updated_at: '2026-10-26T00:00:00.000001Z' };
      return new Promise<Response>((resolve) => {
        pendingWrite = () => {
          tasks.set(id, updated);
          BrowserStream.broadcast({ type: 'task.updated', task: updated });
          resolve(Response.json(updated));
        };
      });
    }
    if (init?.method !== 'DELETE') throw new Error(`Unexpected method ${String(init?.method)}`);
    requests.delete += 1;
    return new Promise<Response>((resolve) => {
      pendingWrite = () => {
        tasks.delete(id);
        BrowserStream.broadcast({ type: 'task.deleted', task_id: id });
        resolve(new Response(null, { status: 204 }));
      };
    });
  });
  const finishWrite = (): void => {
    if (!pendingWrite) throw new Error('No replay write to finish');
    const commit = pendingWrite;
    pendingWrite = undefined;
    commit();
  };
  const createTask = (): void => {
    const task = {
      ...fixtureTask(5),
      id: 9,
      inbound_message_id: 9,
      title: 'Synthetic live dashboard task',
      source_text: 'Synthetic live dashboard task',
    };
    tasks.set(task.id, task);
    BrowserStream.broadcast({ type: 'task.created', task });
  };
  const revoke = (): void => {
    revoked = true;
    BrowserStream.broadcast({ type: 'auth.revoked' });
  };
  return { fetchImpl, requests, finishWrite, createTask, revoke };
};

export const checkRow = async (
  row: string,
  expected: unknown,
  observe: () => unknown,
): Promise<void> => {
  let actual: unknown;
  try {
    actual = await observe();
  } catch (cause) {
    process.stdout.write(
      JSON.stringify({ row, expected, actual: 'Observation failed', passed: false }) + '\n',
    );
    throw new Error(`${row}: replay observation failed`, { cause });
  }
  const passed = JSON.stringify(actual) === JSON.stringify(expected);
  process.stdout.write(JSON.stringify({ row, expected, actual, passed }) + '\n');
  expect(actual, `${row}: differs from the accepted After S7 outcome`).toEqual(expected);
};

export const column = (board: HTMLElement, name: string): HTMLElement =>
  within(board).getByRole('region', { name });
export const card = (board: HTMLElement, id: number): HTMLElement => {
  const element = within(board)
    .getByRole('heading', { level: 3, name: fixtureTask(id).title })
    .closest('article');
  if (!element) throw new Error(`A${String(id)} has no card`);
  return element;
};
export const labels = (board: HTMLElement, name: string): (string | null)[] =>
  within(column(board, name))
    .queryAllByRole('article')
    .map((element) => {
      const title = within(element).getByRole('heading', { level: 3 }).textContent;
      const task = ownerTasks.find((row) => row.title === title);
      return task ? `A${String(task.id)}` : title;
    });
