import { act, renderHook } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { useWideLayout } from './useWideLayout';

test('subscribes to the 768px breakpoint and releases the native listener', () => {
  let matches = true;
  let handleChange: (() => void) | undefined;
  const removeEventListener = vi.fn();
  const matchMedia = vi.spyOn(window, 'matchMedia').mockImplementation(
    (media) =>
      ({
        media,
        get matches() {
          return matches;
        },
        addEventListener: (_type: string, callback: () => void) => {
          handleChange = callback;
        },
        removeEventListener,
      }) as unknown as MediaQueryList,
  );
  const { result, unmount } = renderHook(useWideLayout);
  expect(matchMedia).toHaveBeenCalledWith('(min-width: 768px)');
  expect(result.current).toBe(true);
  matches = false;
  act(() => handleChange?.());
  expect(result.current).toBe(false);
  unmount();
  expect(removeEventListener).toHaveBeenCalledWith('change', expect.any(Function));
  matchMedia.mockRestore();
});
