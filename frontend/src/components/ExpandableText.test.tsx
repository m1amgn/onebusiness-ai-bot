import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';

import { ExpandableText } from './ExpandableText';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test('offers expansion when the loaded font makes the clamped text overflow', async () => {
  let finishFont!: () => void;
  const ready = new Promise<void>((resolve) => {
    finishFont = resolve;
  });
  const fonts = Object.getOwnPropertyDescriptor(document, 'fonts');
  Object.defineProperty(document, 'fonts', { configurable: true, value: { ready } });
  let height = 60;
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => height);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60);
  try {
    render(<ExpandableText text="Description sized with the fallback font." />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    height = 100;
    await act(async () => {
      finishFont();
      await ready;
    });
    expect(screen.getByRole('button', { name: 'Show more' })).toBeInTheDocument();
  } finally {
    if (fonts) Object.defineProperty(document, 'fonts', fonts);
    else Reflect.deleteProperty(document, 'fonts');
  }
});

test('measures overflow, expands the whole description, and collapses it', async () => {
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60);
  const user = userEvent.setup();
  render(<ExpandableText text="A description that needs more than three lines." />);
  const more = screen.getByRole('button', { name: 'Show more' });
  expect(more).toHaveAttribute('aria-expanded', 'false');
  expect(document.getElementById(more.getAttribute('aria-controls') ?? '')).toHaveClass(
    'line-clamp-3',
  );
  await user.click(more);
  expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  expect(screen.getByText('A description that needs more than three lines.')).not.toHaveClass(
    'line-clamp-3',
  );
  await user.click(screen.getByRole('button', { name: 'Show less' }));
  expect(screen.getByRole('button', { name: 'Show more' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
}, 15_000);

test('does not offer a toggle for a short description and measures again after resize', () => {
  let height = 60;
  let handleResize: ResizeObserverCallback | undefined;
  const disconnect = vi.fn();
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(() => height);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        handleResize = callback;
      }
      observe = vi.fn();
      disconnect = disconnect;
    },
  );
  const { unmount } = render(<ExpandableText text="Short description." />);
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  height = 100;
  act(() => handleResize?.([], {} as ResizeObserver));
  expect(screen.getByRole('button', { name: 'Show more' })).toBeInTheDocument();
  height = 60;
  act(() => handleResize?.([], {} as ResizeObserver));
  expect(screen.queryByRole('button')).not.toBeInTheDocument();
  unmount();
  expect(disconnect).toHaveBeenCalled();
  vi.unstubAllGlobals();
});

test('keeps expansion when the expanded text receives a resize notification', () => {
  let handleResize: ResizeObserverCallback | undefined;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: ResizeObserverCallback) {
        handleResize = callback;
      }
      observe = vi.fn();
      disconnect = vi.fn();
    },
  );
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60);
  render(<ExpandableText text="Long description." />);
  fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
  act(() => handleResize?.([], {} as ResizeObserver));
  expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  expect(screen.getByText('Long description.')).not.toHaveClass('line-clamp-3');
});
