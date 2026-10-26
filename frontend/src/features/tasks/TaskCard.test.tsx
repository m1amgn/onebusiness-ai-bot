import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { TaskCard } from './TaskCard';
import { task } from './taskState.testHelpers';

const props = () => ({
  task: task(1, 'pending', {
    title: 'Prepare a report',
    description: 'Report details.',
    source_text: 'Original message text.',
  }),
  status: 'in_progress' as const,
  deleting: false,
  arrived: false,
  draggable: true,
  onMove: vi.fn(),
  onDelete: vi.fn(),
  onArrivalEnd: vi.fn(),
});

test('uses the displayed status, provides named arrow controls, and discloses source text', async () => {
  const handlers = props();
  const user = userEvent.setup();
  render(<TaskCard {...handlers} />);
  expect(screen.getByRole('heading', { name: 'Prepare a report' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Completed' })).toHaveClass('bg-done-9');
  await user.click(screen.getByRole('button', { name: 'Pending' }));
  await user.click(screen.getByRole('button', { name: 'Completed' }));
  expect(handlers.onMove.mock.calls).toEqual([
    [1, 'pending'],
    [1, 'completed'],
  ]);
  const source = screen.getByText('Source message');
  expect(source.tagName).toBe('SUMMARY');
  await user.click(source);
  expect(source.closest('details')).toHaveAttribute('open');
  expect(screen.getByText('Original message text.')).toBeVisible();
}, 15_000);

test('confirms deletion by task title, supports cancellation, and allows deletion during a move', async () => {
  const handlers = props();
  const user = userEvent.setup();
  render(<TaskCard {...handlers} />);
  await user.click(screen.getByRole('button', { name: 'Delete task' }));
  expect(within(screen.getByRole('dialog')).getByText('Prepare a report')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(handlers.onDelete).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Delete task' }));
  await user.click(screen.getByRole('button', { name: 'Delete' }));
  expect(handlers.onDelete).toHaveBeenCalledWith(handlers.task);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('disables card controls and dragging only while deletion is pending', () => {
  const handlers = props();
  render(<TaskCard {...handlers} deleting />);
  for (const button of screen.getAllByRole('button')) expect(button).toBeDisabled();
  expect(screen.getByRole('article')).toHaveAttribute('draggable', 'false');
});

test.each([
  ['move', "Couldn't change the status. Try again."],
  ['delete', "Couldn't delete the task. Try again."],
] as const)('shows the %s failure beside the card', (error, message) => {
  render(<TaskCard {...props()} error={error} />);
  expect(screen.getByRole('alert')).toHaveTextContent(message);
});

test('writes a typed drag payload and cleans the arrival mark after its animation', () => {
  const handlers = props();
  render(<TaskCard {...handlers} arrived />);
  const card = screen.getByRole('article');
  const dataTransfer = { setData: vi.fn(), effectAllowed: '' };
  fireEvent.dragStart(card, { dataTransfer });
  expect(dataTransfer.setData).toHaveBeenCalledWith('application/x-taskbot-task', '1');
  expect(dataTransfer.effectAllowed).toBe('move');
  expect(card).toHaveClass('task-arrival');
  fireEvent.animationEnd(card);
  expect(handlers.onArrivalEnd).toHaveBeenCalledWith(1);
});
