export const reconnectDelayMs = (attempt: number, random: () => number): number =>
  Math.max(500, random() * Math.min(30_000, 1_000 * 2 ** (attempt - 1)));
