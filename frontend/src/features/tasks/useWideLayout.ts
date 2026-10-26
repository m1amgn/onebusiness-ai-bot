import { useSyncExternalStore } from 'react';

const query = '(min-width: 768px)';

const subscribe = (handleChange: () => void): (() => void) => {
  const media = window.matchMedia(query);
  media.addEventListener('change', handleChange);
  return () => {
    media.removeEventListener('change', handleChange);
  };
};

const getSnapshot = (): boolean => window.matchMedia(query).matches;
const getServerSnapshot = (): boolean => false;

export const useWideLayout = (): boolean =>
  useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
