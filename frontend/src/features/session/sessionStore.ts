export const SESSION_TOKEN_STORAGE_KEY = 'dashboardToken';

type SessionSnapshot = Readonly<{ token: string | null; rejected: boolean }>;
type Options = {
  storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null;
  location: Pick<Location, 'hash' | 'pathname' | 'search'>;
  history: Pick<History, 'replaceState'>;
};

// These values outlive a store discarded by StrictMode, including failed storage writes.
let capturedFragment: string | null | undefined;
let memorySnapshot: SessionSnapshot = { token: null, rejected: false };
let storageBlocked = false;

export const createSessionStore = ({ storage, location, history }: Options) => {
  let snapshot = memorySnapshot;
  let captured = false;
  const listeners = new Set<() => void>();
  if (storage === null) storageBlocked = true;

  const updateSnapshot = (token: string | null, rejected = false): void => {
    if (snapshot.token === token && snapshot.rejected === rejected) return;
    snapshot = { token, rejected };
    memorySnapshot = snapshot;
    listeners.forEach((listener) => {
      listener();
    });
  };

  const readStoredToken = (): string | null => {
    if (!storage || storageBlocked) return memorySnapshot.token;
    try {
      return storage.getItem(SESSION_TOKEN_STORAGE_KEY) || null;
    } catch {
      storageBlocked = true;
      return memorySnapshot.token;
    }
  };

  const refreshSnapshot = (): void => {
    const token = readStoredToken();
    updateSnapshot(token, token === snapshot.token && snapshot.rejected);
  };

  const handleStorage = (event: StorageEvent): void => {
    if (storageBlocked || (event.key !== null && event.key !== SESSION_TOKEN_STORAGE_KEY)) return;
    if (event.storageArea !== null && event.storageArea !== storage) return;
    refreshSnapshot();
  };

  const subscribe = (listener: () => void): (() => void) => {
    listeners.add(listener);
    if (listeners.size === 1 && !storageBlocked) {
      window.addEventListener('storage', handleStorage);
      refreshSnapshot();
    }
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) window.removeEventListener('storage', handleStorage);
    };
  };

  const capture = (): SessionSnapshot => {
    if (captured) return snapshot;
    captured = true;
    if (capturedFragment === undefined) {
      capturedFragment = new URLSearchParams(location.hash.slice(1)).get('token') || null;
      if (location.hash) history.replaceState(null, '', location.pathname + location.search);
      if (capturedFragment !== null) {
        if (storage && !storageBlocked) {
          try {
            storage.setItem(SESSION_TOKEN_STORAGE_KEY, capturedFragment);
          } catch {
            storageBlocked = true;
          }
        }
        updateSnapshot(capturedFragment);
        return snapshot;
      }
    }
    refreshSnapshot();
    return snapshot;
  };

  const clearIfCurrent = (token: string): void => {
    const current = readStoredToken();
    if (current !== token) {
      updateSnapshot(current, current === snapshot.token && snapshot.rejected);
      return;
    }
    if (storage && !storageBlocked) {
      try {
        storage.removeItem(SESSION_TOKEN_STORAGE_KEY);
      } catch {
        storageBlocked = true;
      }
    }
    updateSnapshot(null, true);
  };

  return {
    getSnapshot: (): SessionSnapshot => snapshot,
    subscribe,
    capture,
    clearIfCurrent,
  };
};
