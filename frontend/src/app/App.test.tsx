import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

const requestUrl = (input: RequestInfo | URL): URL =>
  new URL(
    typeof input === 'string' ? input : input instanceof URL ? input.href : input.url,
    location.origin,
  );

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  history.replaceState(null, '', '/');
  BrowserStream.instances = [];
});

afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
  window.dispatchEvent(new Event('resize'));
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test(
  'switches the rendered board to one non-draggable phone column after resizing',
  { timeout: 30_000 },
  async () => {
    history.replaceState(null, '', '/#token=phone-link');
    vi.stubGlobal('EventSource', BrowserStream);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>((input) => {
        const url = requestUrl(input);
        if (url.pathname === '/api/realtime/tickets')
          return Promise.resolve(Response.json({ ticket: 'phone-ticket', expires_in: 30 }));
        const status = url.searchParams.get('status');
        return Promise.resolve(
          Response.json({
            items:
              status === 'pending'
                ? [taskFor(1, 'Pending phone task')]
                : status === 'completed'
                  ? [{ ...taskFor(2, 'Completed phone task'), status: 'completed' }]
                  : [],
            next_cursor: null,
          }),
        );
      }),
    );
    const { App } = await import('./App');
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Pending phone task' })).toBeInTheDocument();
    expect(screen.getAllByRole('article').every((card) => card.draggable)).toBe(true);

    act(() => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 360 });
      window.dispatchEvent(new Event('resize'));
    });

    const tabs = await screen.findByRole('tablist');
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
    expect(screen.getAllByRole('article').every((card) => !card.draggable)).toBe(true);
    expect(screen.queryByRole('heading', { name: 'Completed phone task' })).not.toBeInTheDocument();
    fireEvent.click(within(tabs).getByRole('tab', { name: /Completed/ }));
    expect(screen.getByRole('heading', { name: 'Completed phone task' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Pending phone task' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
    expect(screen.getAllByRole('article').every((card) => !card.draggable)).toBe(true);
  },
);

test('asks for a bot link when no dashboard token exists', { timeout: 30_000 }, async () => {
  const { App } = await import('./App');
  render(<App />);
  expect(screen.getByRole('heading', { name: 'Tasks', level: 1 })).toBeInTheDocument();
  expect(screen.getByText(/Open your dashboard from the bot/)).toBeInTheDocument();
  expect(screen.getAllByRole('main')).toHaveLength(1);
});

test(
  'opens a fresh link in the same tab after its previous link was rejected',
  { timeout: 30_000 },
  async () => {
    history.replaceState(null, '', '/#token=expired-link');
    vi.stubGlobal('EventSource', BrowserStream);
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>((input, init) => {
        const url = requestUrl(input);
        if (new Headers(init?.headers).get('Authorization') === 'Bearer expired-link')
          return Promise.resolve(Response.json({ detail: 'Unauthorized' }, { status: 401 }));
        if (url.pathname === '/api/realtime/tickets')
          return Promise.resolve(Response.json({ ticket: 'fresh-ticket', expires_in: 30 }));
        return Promise.resolve(
          Response.json({
            items:
              url.searchParams.get('status') === 'pending' ? [taskFor(2, 'Fresh link task')] : [],
            next_cursor: null,
          }),
        );
      }),
    );
    const { App } = await import('./App');
    render(<App />);
    expect(await screen.findByText('This link no longer works.')).toBeInTheDocument();

    act(() => {
      history.replaceState(null, '', '/#token=fresh-link');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    });

    expect(await screen.findByRole('heading', { name: 'Fresh link task' })).toBeInTheDocument();
    expect(localStorage.getItem('dashboardToken')).toBe('fresh-link');
    expect(location.hash).toBe('');
    expect(screen.queryByText('This link no longer works.')).not.toBeInTheDocument();
  },
);

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
