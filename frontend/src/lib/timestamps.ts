export const compareTimestamps = (a: string, b: string): number => {
  const aSeconds = a.slice(0, 19);
  const bSeconds = b.slice(0, 19);

  if (aSeconds !== bSeconds) {
    return aSeconds < bSeconds ? -1 : 1;
  }

  const aMicroseconds = Number(a.slice(20, -1).padEnd(6, '0'));
  const bMicroseconds = Number(b.slice(20, -1).padEnd(6, '0'));

  return aMicroseconds - bMicroseconds;
};
