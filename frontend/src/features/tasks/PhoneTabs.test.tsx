import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { PhoneTabs } from './PhoneTabs';
import { createBoardState } from './taskState';
import { task } from './taskState.testHelpers';

test('switches a single visible column, counts shown tasks, and never enables dragging', async () => {
  const state = createBoardState();
  state.byId.set(1, task(1));
  state.byId.set(2, task(2, 'in_progress'));
  state.byId.set(3, task(3, 'completed'));
  for (const column of Object.values(state.columns)) column.complete = true;
  const user = userEvent.setup();
  render(
    <PhoneTabs
      state={state}
      onMove={vi.fn()}
      onDelete={vi.fn()}
      onLoadMore={vi.fn()}
      onRetry={vi.fn()}
      onArrivalEnd={vi.fn()}
    />,
  );
  const tabs = within(screen.getByRole('tablist', { name: 'Task status' }));
  expect(tabs.getByRole('tab', { name: 'Pending 1' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getAllByRole('region')).toHaveLength(1);
  expect(screen.getByRole('article')).toHaveAttribute('draggable', 'false');
  await user.click(tabs.getByRole('tab', { name: 'In Progress 1' }));
  expect(screen.queryByRole('heading', { name: 'Task 1' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Task 2' })).toBeInTheDocument();
  expect(screen.getByRole('article')).toHaveAttribute('draggable', 'false');
  await user.keyboard('{ArrowRight}');
  expect(tabs.getByRole('tab', { name: 'Completed 1' })).toHaveFocus();
  expect(screen.getByRole('heading', { name: 'Task 3' })).toBeInTheDocument();
  await user.keyboard('{Home}');
  expect(tabs.getByRole('tab', { name: 'Pending 1' })).toHaveFocus();
}, 15_000);

test('marks each incomplete column count with a plus sign', () => {
  const state = createBoardState();
  state.byId.set(1, task(1));
  state.columns.completed.complete = true;
  render(
    <PhoneTabs
      state={state}
      onMove={vi.fn()}
      onDelete={vi.fn()}
      onLoadMore={vi.fn()}
      onRetry={vi.fn()}
      onArrivalEnd={vi.fn()}
    />,
  );
  expect(screen.getByRole('tab', { name: 'Pending 1+' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'In Progress 0+' })).toBeInTheDocument();
  expect(screen.getByRole('tab', { name: 'Completed 0' })).toBeInTheDocument();
});
