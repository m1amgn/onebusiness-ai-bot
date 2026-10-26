import type { ReactNode } from 'react';
import { useState, useSyncExternalStore } from 'react';

import { createSessionStore } from './sessionStore';
import { SignedOut } from './SignedOut';

type OwnProps = {
  children: (token: string, clearIfCurrent: (token: string) => void) => ReactNode;
};

export const SessionGate = ({ children }: OwnProps): ReactNode => {
  const [store] = useState(() => {
    let storage: Storage | null = null;
    try {
      storage = window.localStorage;
    } catch {
      // Browser privacy settings can block even reading the localStorage property.
    }
    const sessionStore = createSessionStore({
      storage,
      location: window.location,
      history: window.history,
    });
    sessionStore.capture();
    return sessionStore;
  });
  const session = useSyncExternalStore(store.subscribe, store.getSnapshot);

  return session.token ? (
    children(session.token, store.clearIfCurrent)
  ) : (
    <SignedOut rejected={session.rejected} />
  );
};
