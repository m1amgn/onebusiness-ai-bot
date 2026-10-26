import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { StatusButtons } from './StatusButtons';

test.each([
  ['pending', 'In Progress', 'in_progress', 'bg-move-9'],
  ['in_progress', 'Completed', 'completed', 'bg-done-9'],
] as const)(
  'colours the %s forward button for its target status',
  async (status, name, target, colour) => {
    const onMove = vi.fn();
    const user = userEvent.setup();
    render(<StatusButtons status={status} disabled={false} onMove={onMove} />);
    const button = screen.getByRole('button', { name });
    expect(button).toHaveClass(colour);
    expect(button.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    await user.click(button);
    expect(onMove).toHaveBeenCalledWith(target);
  },
);

test('completed tasks only have the previous status button', () => {
  render(<StatusButtons status="completed" disabled={false} onMove={vi.fn()} />);
  expect(screen.getAllByRole('button')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'In Progress' })).not.toHaveClass('bg-move-9');
});
