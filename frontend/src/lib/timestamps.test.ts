import { expect, test } from 'vitest';

import { compareTimestamps } from './timestamps';

test.each([
  ['2026-10-26T12:00:00Z', '2026-10-26T12:00:00.000001Z'],
  ['2026-10-26T12:00:00.000001Z', '2026-10-26T12:00:00.000002Z'],
  ['2026-10-26T12:00:00.400000Z', '2026-10-26T12:00:00.5Z'],
  ['2026-10-26T12:00:00.999999Z', '2026-10-26T12:00:01Z'],
  ['2026-12-31T23:59:59.999999Z', '2027-01-01T00:00:00Z'],
])('orders %s before %s with microsecond precision', (earlier, later) => {
  expect(compareTimestamps(earlier, later)).toBeLessThan(0);
  expect(compareTimestamps(later, earlier)).toBeGreaterThan(0);
});

test.each([
  ['2026-10-26T12:00:00Z', '2026-10-26T12:00:00.000000Z'],
  ['2026-10-26T12:00:00.5Z', '2026-10-26T12:00:00.500000Z'],
  ['2026-10-26T12:00:00.123Z', '2026-10-26T12:00:00.123000Z'],
  ['2026-10-26T12:00:00.000001Z', '2026-10-26T12:00:00.000001Z'],
])('treats normalized fractions in %s and %s as equal', (a, b) => {
  expect(compareTimestamps(a, b)).toBe(0);
  expect(compareTimestamps(b, a)).toBe(0);
});
