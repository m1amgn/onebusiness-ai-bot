import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { ConfirmDialog } from './ConfirmDialog';

test('shows the named task and exact confirmation copy in a native dialog', async () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const user = userEvent.setup();
  render(
    <ConfirmDialog
      open
      title="Prepare quarterly report"
      onConfirm={onConfirm}
      onCancel={onCancel}
    />,
  );
  const dialog = screen.getByRole('dialog', { name: 'Delete this task?' });
  expect(dialog.tagName).toBe('DIALOG');
  expect(dialog).toHaveAttribute('open');
  expect(screen.getByText('Prepare quarterly report')).toBeInTheDocument();
  expect(dialog).toHaveAccessibleDescription(
    'This cannot be undone. The message stays in your Telegram chat.',
  );
  await user.click(screen.getByRole('button', { name: 'Delete' }));
  expect(onConfirm).toHaveBeenCalledOnce();
  expect(onCancel).not.toHaveBeenCalled();
});

test('Cancel and native Escape cancellation call onCancel without confirming', async () => {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(
    <ConfirmDialog open title="Keep me" onConfirm={onConfirm} onCancel={onCancel} />,
  );
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
  expect(onCancel).toHaveBeenCalledTimes(2);
  expect(onConfirm).not.toHaveBeenCalled();
  rerender(
    <ConfirmDialog open={false} title="Keep me" onConfirm={onConfirm} onCancel={onCancel} />,
  );
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
