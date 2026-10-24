import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { App } from './App';

test('shows the application name as the page heading', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: 'AI Task Bot', level: 1 })).toBeInTheDocument();
});

test('starts another render with a clean document', () => {
  render(<App />);
  expect(screen.getAllByRole('main')).toHaveLength(1);
  expect(screen.getByRole('heading', { name: 'AI Task Bot', level: 1 })).toBeInTheDocument();
});
