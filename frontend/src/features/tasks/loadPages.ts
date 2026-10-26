import { ApiRequestError } from '../../api/client';
import type { ApiClient } from '../../api/client';
import type { TaskPage, TaskStatus } from '../../api';

import { compareTaskKeys, oldestKey } from './taskKeys';
import { boardFrontier, taskStatuses } from './taskState';
import type { BoardAction, BoardState, TaskKey } from './taskState';

type PageLoaderOptions = {
  client: Pick<ApiClient, 'listTasks'>;
  getState: () => BoardState;
  dispatch: (action: BoardAction) => void;
  onUnauthorized: () => void;
  onResyncFailure: () => void;
};

type Request = {
  status: TaskStatus;
  epoch: number;
  requestId: number;
  controller: AbortController;
};

export const createPageLoader = ({
  client,
  getState,
  dispatch,
  onUnauthorized,
  onResyncFailure,
}: PageLoaderOptions) => {
  let stopped = false;
  let sequence = 0;
  let retainedFrontier: TaskKey | null = null;
  let retryResync = false;
  const pending = new Map<TaskStatus, Request>();

  const currentEpoch = (epoch: number): boolean => !stopped && getState().epoch === epoch;

  const current = (request: Request): boolean =>
    currentEpoch(request.epoch) && !request.controller.signal.aborted;

  const begin = (status: TaskStatus): Request => {
    const request = {
      status,
      epoch: getState().epoch,
      requestId: ++sequence,
      controller: new AbortController(),
    };
    pending.set(status, request);
    dispatch({ type: 'page.started', ...request });
    return request;
  };

  const finish = (request: Request, error = false): void => {
    if (pending.get(request.status) === request) pending.delete(request.status);
    dispatch({ type: 'page.finished', ...request, error });
  };

  const abortAll = (): void => {
    for (const request of [...pending.values()]) {
      request.controller.abort();
      finish(request);
    }
  };

  const stop = (): void => {
    if (stopped) return;
    stopped = true;
    abortAll();
    if (getState().resync) dispatch({ type: 'resync.failed', epoch: getState().epoch });
  };

  const unauthorized = (): void => {
    if (stopped) return;
    stop();
    onUnauthorized();
  };

  const captureFrontier = (): TaskKey | null => {
    retainedFrontier = oldestKey(retainedFrontier, boardFrontier(getState()));
    return retainedFrontier;
  };

  const load = async (status: TaskStatus): Promise<void> => {
    const state = getState();
    if (stopped || state.resync || pending.has(status)) return;
    const cursor = state.columns[status].cursor;
    const request = begin(status);
    let failed = false;
    try {
      const page = await client.listTasks({ status, limit: 50, cursor }, request.controller.signal);
      if (current(request)) dispatch({ type: 'page.received', ...request, cursor, page });
    } catch (error) {
      if (!current(request)) return;
      if (error instanceof ApiRequestError && error.status === 401) unauthorized();
      else failed = true;
    } finally {
      finish(request, failed);
    }
  };

  const initialLoad = async (): Promise<void> => {
    if (stopped) return;
    await Promise.all(taskStatuses.map(load));
  };

  const chain = async (request: Request, frontier: TaskKey | null): Promise<TaskPage> => {
    const items: TaskPage['items'] = [];
    let cursor: string | null = null;
    while (current(request)) {
      const page = await client.listTasks(
        { status: request.status, limit: frontier ? 200 : 50, cursor },
        request.controller.signal,
      );
      if (!current(request)) return { items: [], next_cursor: null };
      items.push(...page.items);
      cursor = page.next_cursor;
      if (
        !frontier ||
        cursor === null ||
        page.items.some((row) => compareTaskKeys(row, frontier) >= 0)
      )
        break;
    }
    return { items, next_cursor: cursor };
  };

  const resync = async (): Promise<void> => {
    if (stopped) return;
    const frontier = captureFrontier();
    abortAll();
    const epoch = getState().epoch + 1;
    dispatch({ type: 'resync.started', epoch, frontier });
    const requests = taskStatuses.map(begin);
    let failedStatus: TaskStatus | undefined;
    try {
      const pages = await Promise.all(
        requests.map(async (request) => {
          try {
            return await chain(request, frontier);
          } catch (error) {
            failedStatus ??= request.status;
            throw error;
          }
        }),
      );
      if (!currentEpoch(epoch)) return;
      const [pendingPage, inProgressPage, completedPage] = pages;
      if (!pendingPage || !inProgressPage || !completedPage) return;
      dispatch({
        type: 'resync.completed',
        epoch,
        pages: {
          pending: pendingPage,
          in_progress: inProgressPage,
          completed: completedPage,
        },
      });
      retainedFrontier = null;
      retryResync = false;
    } catch (error) {
      if (!currentEpoch(epoch)) return;
      abortAll();
      dispatch({ type: 'resync.failed', epoch, status: failedStatus });
      retryResync = true;
      if (error instanceof ApiRequestError && error.status === 401) unauthorized();
      else onResyncFailure();
    } finally {
      for (const request of requests) finish(request);
    }
  };

  const loadMore = async (status: TaskStatus): Promise<void> => {
    const column = getState().columns[status];
    if (column.complete || column.cursor === null) return;
    await load(status);
  };

  const retry = async (status: TaskStatus): Promise<void> => {
    if (retryResync) await resync();
    else await load(status);
  };

  return { initialLoad, resync, loadMore, retry, captureFrontier, stop };
};
