import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type SessionModule = typeof import('./sessionStore');

let createSessionStore: SessionModule['createSessionStore'];
let storageKey: string;
const subscriptions: (() => void)[] = [];

const createStore = (
  storage: Parameters<SessionModule['createSessionStore']>[0]['storage'] = window.localStorage,
) => createSessionStore({ storage, location: window.location, history: window.history });

const broadcast = (key: string | null, newValue: string | null): void => {
  if (key === null) window.localStorage.clear();
  else if (newValue === null) window.localStorage.removeItem(key);
  else window.localStorage.setItem(key, newValue);
  window.dispatchEvent(
    new StorageEvent('storage', { key, newValue, storageArea: window.localStorage }),
  );
};

beforeEach(async () => {
  vi.resetModules();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/tasks?view=board');
  const module = await import('./sessionStore');
  createSessionStore = module.createSessionStore;
  storageKey = module.SESSION_TOKEN_STORAGE_KEY;
});

afterEach(() => {
  subscriptions.splice(0).forEach((unsubscribe) => {
    unsubscribe();
  });
  vi.restoreAllMocks();
});

describe('session capture', () => {
  it('stores the fragment instead of the older token and removes it without changing the route', () => {
    window.localStorage.setItem(storageKey, 'older-link');
    window.history.replaceState({ old: true }, '', '/tasks?view=board#token=fresh%2Blink');
    const store = createStore();

    store.capture();

    expect(store.getSnapshot()).toEqual({ token: 'fresh+link', rejected: false });
    expect(window.localStorage.getItem(storageKey)).toBe('fresh+link');
    expect(window.localStorage.length).toBe(1);
    expect(window.location.pathname + window.location.search + window.location.hash).toBe(
      '/tasks?view=board',
    );
    expect(window.history.state).toBeNull();
  });

  it('keeps a stable snapshot on repeated capture and when StrictMode creates another store', () => {
    window.history.replaceState(null, '', '/#token=initial-link');
    const replaceState = vi.spyOn(window.history, 'replaceState');
    const store = createStore();
    store.capture();
    const captured = store.getSnapshot();

    store.capture();
    const replacement = createStore();
    replacement.capture();

    expect(store.getSnapshot()).toBe(captured);
    expect(replacement.getSnapshot().token).toBe('initial-link');
    expect(replaceState).toHaveBeenCalledTimes(1);
  });

  it('reads the stored token when the fragment has no token', () => {
    window.localStorage.setItem(storageKey, 'stored-link');
    window.history.replaceState(null, '', '/tasks?view=board#token=');
    const store = createStore();

    store.capture();

    expect(store.getSnapshot()).toEqual({ token: 'stored-link', rejected: false });
    expect(window.location.hash).toBe('');
  });

  it('uses memory when storage access is blocked, including a discarded store', () => {
    window.history.replaceState(null, '', '/#token=memory-link');
    const storage = {
      getItem: () => {
        throw new Error('Blocked memory-link');
      },
      setItem: () => {
        throw new Error('Blocked memory-link');
      },
      removeItem: () => {
        throw new Error('Blocked memory-link');
      },
    };
    const discarded = createStore(storage);

    expect(() => discarded.capture()).not.toThrow();
    discarded.capture();
    const active = createStore(storage);
    expect(() => active.capture()).not.toThrow();

    expect(active.getSnapshot()).toEqual({ token: 'memory-link', rejected: false });
    expect(window.location.hash).toBe('');
  });

  it('does not restore an older readable token when writing the new fragment is blocked', () => {
    window.localStorage.setItem(storageKey, 'older-link');
    window.history.replaceState(null, '', '/#token=memory-link');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage is full');
    });
    const discarded = createStore();
    discarded.capture();
    const active = createStore();

    active.capture();

    expect(active.getSnapshot().token).toBe('memory-link');
  });
});

describe('cross-tab session updates', () => {
  it('adopts a new token, ignores unrelated keys and keeps unchanged snapshots stable', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    const handleChange = vi.fn();
    subscriptions.push(store.subscribe(handleChange));
    const initial = store.getSnapshot();

    broadcast('unrelated', 'value');
    expect(store.getSnapshot()).toBe(initial);
    broadcast(storageKey, 'second-link');
    expect(store.getSnapshot()).toEqual({ token: 'second-link', rejected: false });
    const replaced = store.getSnapshot();
    broadcast(storageKey, 'second-link');
    expect(store.getSnapshot()).toBe(replaced);
    expect(handleChange).toHaveBeenCalledTimes(1);
  });

  it.each(['remove', 'clear'])('signs out after another tab uses storage %s', (operation) => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    subscriptions.push(store.subscribe(() => undefined));

    expect(store.getSnapshot().token).toBe('first-link');

    broadcast(operation === 'clear' ? null : storageKey, null);

    expect(store.getSnapshot()).toEqual({ token: null, rejected: false });
  });

  it('stops receiving cross-tab changes after the last listener unsubscribes', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    const unsubscribe = store.subscribe(() => undefined);

    unsubscribe();
    broadcast(storageKey, 'second-link');

    expect(store.getSnapshot().token).toBe('first-link');
  });

  it('keeps the memory token when storage is blocked', () => {
    window.history.replaceState(null, '', '/#token=memory-link');
    const store = createStore(null);
    store.capture();
    subscriptions.push(store.subscribe(() => undefined));

    broadcast(storageKey, 'another-tab');

    expect(store.getSnapshot().token).toBe('memory-link');
  });
});

describe('clearIfCurrent', () => {
  it('keeps the rejection message when concurrent unauthorized requests reject the same link', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();

    store.clearIfCurrent('first-link');
    const rejected = store.getSnapshot();
    store.clearIfCurrent('first-link');

    expect(store.getSnapshot()).toBe(rejected);
    expect(store.getSnapshot()).toEqual({ token: null, rejected: true });
  });

  it('removes the current token and marks it rejected', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    const handleChange = vi.fn();
    subscriptions.push(store.subscribe(handleChange));

    store.clearIfCurrent('first-link');

    expect(window.localStorage.getItem(storageKey)).toBeNull();
    expect(store.getSnapshot()).toEqual({ token: null, rejected: true });
    expect(handleChange).toHaveBeenCalledTimes(1);
  });

  it('rereads storage and adopts a newer token before the storage event arrives', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    window.localStorage.setItem(storageKey, 'newer-link');

    store.clearIfCurrent('first-link');

    expect(window.localStorage.getItem(storageKey)).toBe('newer-link');
    expect(store.getSnapshot()).toEqual({ token: 'newer-link', rejected: false });
  });

  it('keeps a newer token already observed from another tab', () => {
    window.localStorage.setItem(storageKey, 'first-link');
    const store = createStore();
    store.capture();
    subscriptions.push(store.subscribe(() => undefined));
    broadcast(storageKey, 'newer-link');
    const current = store.getSnapshot();
    expect(current.token).toBe('newer-link');

    store.clearIfCurrent('first-link');

    expect(store.getSnapshot()).toBe(current);
    expect(window.localStorage.getItem(storageKey)).toBe('newer-link');
  });

  it('rejects the in-memory token without reviving the memoized fragment in a new store', () => {
    window.history.replaceState(null, '', '/#token=memory-link');
    const store = createStore(null);
    store.capture();

    store.clearIfCurrent('memory-link');
    const replacement = createStore(null);
    replacement.capture();

    expect(store.getSnapshot()).toEqual({ token: null, rejected: true });
    expect(replacement.getSnapshot()).toEqual({ token: null, rejected: true });
  });
});
