import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { BoardColumns } from './BoardColumns';
import { createBoardState } from './taskState';
import { task } from './taskState.testHelpers';

const props = () => ({
  state: createBoardState(),
  onMove: vi.fn(),
  onDelete: vi.fn(),
  onLoadMore: vi.fn(),
  onRetry: vi.fn(),
  onArrivalEnd: vi.fn(),
});

test('shows three ordered columns and skeletons before deferred loading begins', () => {
  render(<BoardColumns {...props()} />);
  expect(
    screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent),
  ).toEqual(['Pending', 'In Progress', 'Completed']);
  for (const region of screen.getAllByRole('region')) {
    expect(region).toHaveAttribute('aria-busy', 'true');
    expect(within(region).getByRole('status')).toHaveAccessibleName('Loading tasks');
  }
});

test('shows each empty-column message only when loading has completed', () => {
  const handlers = props();
  for (const column of Object.values(handlers.state.columns)) column.complete = true;
  render(<BoardColumns {...handlers} />);
  for (const text of ['No pending tasks.', 'No tasks in progress.', 'No completed tasks.'])
    expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
});

test('keeps rendered cards through resync, retries errors, and loads the next page', async () => {
  const handlers = props();
  handlers.state.byId.set(1, task(1));
  handlers.state.resync = { epoch: 1, frontier: null, log: [] };
  handlers.state.columns.pending.error = true;
  handlers.state.columns.in_progress.cursor = 'next';
  handlers.state.columns.in_progress.edge = { inbound_message_id: 4, position: 0 };
  const user = userEvent.setup();
  const { rerender } = render(<BoardColumns {...handlers} />);
  expect(screen.getByRole('heading', { name: 'Task 1' })).toBeInTheDocument();
  expect(screen.getByText("Couldn't load tasks.")).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Retry' }));
  expect(handlers.onRetry).toHaveBeenCalledWith('pending');
  expect(screen.getByRole('button', { name: 'Load more' })).toBeDisabled();
  handlers.state.resync = null;
  rerender(<BoardColumns {...handlers} />);
  await user.click(screen.getByRole('button', { name: 'Load more' }));
  expect(handlers.onLoadMore).toHaveBeenCalledWith('in_progress');
});

test('accepts typed known cards into a different displayed column and rejects other drops', () => {
  const handlers = props();
  handlers.state.byId.set(1, task(1));
  handlers.state.byId.set(2, task(2));
  handlers.state.byId.set(3, task(3));
  handlers.state.moves.set(1, { to: 'in_progress' });
  handlers.state.deleted.add(2);
  handlers.state.deleting.add(3);
  render(<BoardColumns {...handlers} />);
  const progress = screen.getByRole('region', { name: 'In Progress' });
  const completed = screen.getByRole('region', { name: 'Completed' });
  const transfer = (id: string, types = ['application/x-taskbot-task']) => ({
    types,
    getData: vi.fn(() => id),
    dropEffect: '',
  });
  const accepted = transfer('1');
  expect(fireEvent.dragOver(completed, { dataTransfer: accepted })).toBe(false);
  expect(accepted.dropEffect).toBe('move');
  fireEvent.drop(completed, { dataTransfer: accepted });
  expect(handlers.onMove).toHaveBeenCalledWith(1, 'completed');
  handlers.onMove.mockClear();
  fireEvent.drop(progress, { dataTransfer: transfer('1') });
  for (const id of ['2', '3', '4', '1x', ''])
    fireEvent.drop(completed, { dataTransfer: transfer(id) });
  expect(fireEvent.dragOver(completed, { dataTransfer: transfer('1', ['text/plain']) })).toBe(true);
  fireEvent.drop(completed, { dataTransfer: transfer('1', ['text/plain']) });
  expect(handlers.onMove).not.toHaveBeenCalled();
});
