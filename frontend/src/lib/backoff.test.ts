import { expect, test } from 'vitest';

import { reconnectDelayMs } from './backoff';

test.each([
  [1, 1_000],
  [2, 2_000],
  [3, 4_000],
  [4, 8_000],
  [5, 16_000],
  [6, 30_000],
  [7, 30_000],
  [8, 30_000],
  [9, 30_000],
  [10, 30_000],
])('bounds attempt %i between 500 ms and %i ms', (attempt, maximum) => {
  expect(reconnectDelayMs(attempt, () => 0)).toBe(500);
  expect(reconnectDelayMs(attempt, () => 1)).toBe(maximum);
});

test('uses the random draw within the current exponential window', () => {
  expect(reconnectDelayMs(3, () => 0.25)).toBe(1_000);
  expect(reconnectDelayMs(8, () => 0.25)).toBe(7_500);
});

test('keeps the floor when a nonzero draw falls below 500 ms', () => {
  expect(reconnectDelayMs(1, () => 0.25)).toBe(500);
});
