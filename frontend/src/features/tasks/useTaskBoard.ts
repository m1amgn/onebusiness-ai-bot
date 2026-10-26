import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import type { TaskStatus } from '../../api';
import { ApiRequestError, createApiClient } from '../../api/client';
import { createEventStream } from '../../api/eventStream';
import type { ConnectionStatus } from '../../api/eventStream';

import { createPageLoader } from './loadPages';
import { boardReducer, createBoardState, displayedStatus } from './taskState';
import type { BoardAction, BoardState } from './taskState';

type BoardHandlers = {
  handleMove: (id: number, to: TaskStatus) => void;
  handleDelete: (id: number) => void;
  handleLoadMore: (status: TaskStatus) => void;
  handleRetry: (status: TaskStatus) => void;
  handleArrivalEnd: (id: number) => void;
};

type TaskBoard = BoardHandlers & { state: BoardState; connectionStatus: ConnectionStatus };

export const useTaskBoard = (token: string, onUnauthorized: (token: string) => void): TaskBoard => {
  const [state, reactDispatch] = useReducer(boardReducer, undefined, createBoardState);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('connecting');
  const stateRef = useRef(state);
  const handlersRef = useRef<BoardHandlers | null>(null);
  const dispatch = useCallback((action: BoardAction): void => {
    // Loader chains and queued writes must see each action before React renders it.
    stateRef.current = boardReducer(stateRef.current, action);
    reactDispatch(action);
  }, []);

  useEffect(() => {
    const lifetime = new AbortController();
    const active = (): boolean => !lifetime.signal.aborted;
    const client = createApiClient({ token });
    const activeMoves = new Set<number>();
    const apply = (action: BoardAction): void => {
      if (active()) dispatch(action);
    };
    const unauthorized = (): void => {
      if (active()) onUnauthorized(token);
    };
    const loader = createPageLoader({
      client,
      getState: () => stateRef.current,
      dispatch: apply,
      onUnauthorized: unauthorized,
      onResyncFailure: () => {
        stream.reconnect();
      },
    });
    const stream = createEventStream({
      issueTicket: client.issueTicket,
      onOpen: () => {
        void loader.resync();
      },
      onEvent: apply,
      onStatus: (status) => {
        if (!active()) return;
        if (status === 'reconnecting') loader.captureFrontier();
        setConnectionStatus(status);
      },
      onUnauthorized: unauthorized,
    });

    const pumpMove = async (id: number): Promise<void> => {
      const current = stateRef.current;
      const move = current.moves.get(id);
      const row = current.byId.get(id);
      if (!active() || activeMoves.has(id) || !move || !row || current.deleting.has(id)) return;
      if (move.to === row.status) {
        apply({ type: 'move.finished', id });
        return;
      }
      activeMoves.add(id);
      try {
        const task = await client.changeStatus(id, move.to, lifetime.signal);
        if (!active()) return;
        apply({ type: 'local.updated', task });
        apply({ type: 'move.finished', id });
      } catch (error) {
        if (!active()) return;
        if (error instanceof ApiRequestError && error.status === 404)
          apply({ type: 'local.removed', id });
        else {
          if (stateRef.current.moves.has(id)) apply({ type: 'move.failed', id });
          if (error instanceof ApiRequestError && error.status === 401) unauthorized();
        }
      } finally {
        activeMoves.delete(id);
        if (active()) void pumpMove(id);
      }
    };

    const deleteTask = async (id: number): Promise<void> => {
      try {
        await client.deleteTask(id, lifetime.signal);
        apply({ type: 'local.removed', id });
      } catch (error) {
        if (!active()) return;
        if (error instanceof ApiRequestError && error.status === 404)
          apply({ type: 'local.removed', id });
        else {
          apply({ type: 'delete.failed', id });
          if (error instanceof ApiRequestError && error.status === 401) unauthorized();
        }
      }
    };

    handlersRef.current = {
      handleMove: (id, to) => {
        const current = stateRef.current;
        const row = current.byId.get(id);
        if (
          !active() ||
          !row ||
          current.deleted.has(id) ||
          current.deleting.has(id) ||
          displayedStatus(current, row) === to
        )
          return;
        apply({ type: 'move.target', id, to });
        void pumpMove(id);
      },
      handleDelete: (id) => {
        const current = stateRef.current;
        if (
          !active() ||
          !current.byId.has(id) ||
          current.deleted.has(id) ||
          current.deleting.has(id)
        )
          return;
        apply({ type: 'delete.started', id });
        void deleteTask(id);
      },
      handleLoadMore: (status) => {
        void loader.loadMore(status);
      },
      handleRetry: (status) => {
        void loader.retry(status);
      },
      handleArrivalEnd: (id) => {
        apply({ type: 'arrival.finished', id });
      },
    };
    const handleOnline = (): void => {
      stream.reconnect();
    };
    window.addEventListener('online', handleOnline);
    const start = setTimeout(() => {
      void loader.initialLoad();
      stream.start();
    }, 0);
    return () => {
      clearTimeout(start);
      lifetime.abort();
      handlersRef.current = null;
      loader.stop();
      stream.stop();
      window.removeEventListener('online', handleOnline);
    };
  }, [dispatch, onUnauthorized, token]);

  const handleMove = useCallback(
    (id: number, to: TaskStatus): void => handlersRef.current?.handleMove(id, to),
    [],
  );
  const handleDelete = useCallback((id: number): void => handlersRef.current?.handleDelete(id), []);
  const handleLoadMore = useCallback(
    (status: TaskStatus): void => handlersRef.current?.handleLoadMore(status),
    [],
  );
  const handleRetry = useCallback(
    (status: TaskStatus): void => handlersRef.current?.handleRetry(status),
    [],
  );
  const handleArrivalEnd = useCallback(
    (id: number): void => handlersRef.current?.handleArrivalEnd(id),
    [],
  );

  return {
    state,
    connectionStatus,
    handleMove,
    handleDelete,
    handleLoadMore,
    handleRetry,
    handleArrivalEnd,
  };
};
