import { expect, test } from 'vitest';

import { cn } from './cn';

test('merges conflicting Tailwind classes', () => {
  expect(cn('p-2', 'p-4')).toBe('p-4');
});

test('includes conditional and nested classes', () => {
  expect(cn('flex', { hidden: false, 'items-center': true }, ['gap-2'])).toBe(
    'flex items-center gap-2',
  );
});
