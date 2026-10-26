import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

class BrowserStream {
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
}

const taskFor = (id: number, title: string) => ({
  id,
  inbound_message_id: id,
  position: 0,
  title,
  description: '',
  source_text: title,
  status: 'pending',
  created_at: '2026-10-26T12:00:00Z',
  updated_at: '2026-10-26T12:00:00Z',
});

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  history.replaceState(null, '', '/');
  BrowserStream.instances = [];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test('asks for a bot link when no dashboard token exists', { timeout: 30_000 }, async () => {
  const { App } = await import('./App');
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Tasks', level: 1 })).toBeInTheDocument();
  expect(screen.getByText(/Open your dashboard from the bot/)).toBeInTheDocument();
  expect(screen.getAllByRole('main')).toHaveLength(1);
});

test(
  'replaces the whole board and its stream when another tab stores a new token',
  { timeout: 30_000 },
  async () => {
    history.replaceState(null, '', '/#token=alpha');
    vi.stubGlobal('EventSource', BrowserStream);
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input, init) => {
      const path =
        typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(path, window.location.origin);
      const second = new Headers(init?.headers).get('Authorization') === 'Bearer beta';
      if (url.pathname === '/api/realtime/tickets') {
        return Promise.resolve(
          Response.json({ ticket: second ? 'ticket-b' : 'ticket-a', expires_in: 30 }),
        );
      }
      const items =
        url.searchParams.get('status') === 'pending'
          ? [taskFor(second ? 2 : 1, second ? 'Second account task' : 'First account task')]
          : [];
      return Promise.resolve(Response.json({ items, next_cursor: null }));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { App } = await import('./App');
    render(<App />);
    expect(await screen.findByText('First account task', { selector: 'h3' })).toBeInTheDocument();
    await waitFor(() => {
      expect(BrowserStream.instances).toHaveLength(1);
    });
    const oldStream = BrowserStream.instances[0];
    const oldMessage = oldStream?.onmessage;
    const previousRequests = fetchMock.mock.calls.length;

    act(() => {
      localStorage.setItem('dashboardToken', 'beta');
      window.dispatchEvent(
        new StorageEvent('storage', {
          key: 'dashboardToken',
          newValue: 'beta',
          storageArea: localStorage,
        }),
      );
    });
    expect(await screen.findByText('Second account task', { selector: 'h3' })).toBeInTheDocument();
    expect(screen.queryByText('First account task')).not.toBeInTheDocument();
    expect(oldStream?.closed).toBe(true);
    expect(window.location.hash).toBe('');
    for (const [, init] of fetchMock.mock.calls.slice(previousRequests)) {
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer beta');
    }
    act(() => {
      oldMessage?.(
        new MessageEvent('message', {
          data: JSON.stringify({
            type: 'task.created',
            task: taskFor(99, 'Late first-account task'),
          }),
        }),
      );
    });
    expect(screen.queryByText('Late first-account task')).not.toBeInTheDocument();
  },
);
