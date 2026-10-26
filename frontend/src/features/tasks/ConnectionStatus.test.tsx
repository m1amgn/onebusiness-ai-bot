import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { ConnectionStatus } from './ConnectionStatus';

test.each([
  ['connecting', 'Connecting…'],
  ['live', 'Live'],
  ['reconnecting', 'Reconnecting…'],
] as const)('describes %s in words', (status, label) => {
  render(<ConnectionStatus status={status} />);
  expect(screen.getByRole('status')).toHaveTextContent(label);
});
