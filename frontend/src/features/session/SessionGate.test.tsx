import type { ReactNode } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type GateModule = typeof import('./SessionGate');

let SessionGate: GateModule['SessionGate'];

beforeEach(async () => {
  vi.resetModules();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/');
  SessionGate = (await import('./SessionGate')).SessionGate;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const renderBoard = (token: string, clearIfCurrent: (token: string) => void): ReactNode => (
  <section aria-label="Board">
    <h1>Board for {token}</h1>
    <button
      onClick={() => {
        clearIfCurrent(token);
      }}
    >
      Reject this link
    </button>
  </section>
);

describe('SessionGate', () => {
  it('shows bot instructions when no token exists', () => {
    render(<SessionGate>{renderBoard}</SessionGate>);

    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(
      screen.getByText('Open your dashboard from the bot: send /dashboard to get a link.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Board' })).not.toBeInTheDocument();
    expect(screen.queryByText('This link no longer works.')).not.toBeInTheDocument();
  });

  it('captures the fragment in StrictMode and renders the signed-in child', () => {
    window.history.replaceState(null, '', '/?view=board#token=first-link');

    render(<SessionGate>{renderBoard}</SessionGate>);

    expect(screen.getByRole('heading', { name: 'Board for first-link' })).toBeInTheDocument();
    expect(window.localStorage.getItem('dashboardToken')).toBe('first-link');
    expect(window.location.href).not.toContain('#');
    expect(window.location.search).toBe('?view=board');
  });

  it('renders rejection instructions after the current child rejects its link', () => {
    window.localStorage.setItem('dashboardToken', 'first-link');
    render(<SessionGate>{renderBoard}</SessionGate>);

    fireEvent.click(screen.getByRole('button', { name: 'Reject this link' }));

    expect(screen.getByText('This link no longer works.')).toBeInTheDocument();
    expect(screen.getByText('Send /dashboard to the bot for a new link')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Board' })).not.toBeInTheDocument();
  });

  it('passes a stable clear callback while another tab replaces the token', () => {
    window.localStorage.setItem('dashboardToken', 'first-link');
    const children = vi.fn(renderBoard);
    const { rerender } = render(<SessionGate>{children}</SessionGate>);
    const initialCallback = children.mock.lastCall?.[1];

    rerender(<SessionGate>{children}</SessionGate>);
    act(() => {
      window.localStorage.setItem('dashboardToken', 'second-link');
      window.dispatchEvent(new StorageEvent('storage', { key: 'dashboardToken' }));
    });

    expect(screen.getByRole('heading', { name: 'Board for second-link' })).toBeInTheDocument();
    expect(children.mock.lastCall?.[1]).toBe(initialCallback);
  });

  it('captures and rejects a link when accessing localStorage throws', () => {
    window.history.replaceState(null, '', '/#token=memory-link');
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new Error('Storage blocked');
    });

    render(<SessionGate>{renderBoard}</SessionGate>);

    expect(screen.getByRole('heading', { name: 'Board for memory-link' })).toBeInTheDocument();
    expect(window.location.hash).toBe('');
    fireEvent.click(screen.getByRole('button', { name: 'Reject this link' }));
    expect(screen.getByText('This link no longer works.')).toBeInTheDocument();
  });
});
