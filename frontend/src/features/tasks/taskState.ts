import type { TaskPage, TaskStatus, TaskView } from '../../api';
import { compareTimestamps } from '../../lib/timestamps';

import { compareTaskKeys, taskKey } from './taskKeys';
import type { BoardAction, BoardChange, BoardState, ColumnLoad } from './taskStateTypes';

export type * from './taskStateTypes';
export { boardFrontier } from './taskKeys';
export const taskStatuses = ['pending', 'in_progress', 'completed'] as const;

const emptyColumn = (): ColumnLoad => ({
  cursor: null,
  complete: false,
  edge: null,
  loading: false,
  error: false,
  requestId: null,
});

export const createBoardState = (): BoardState => ({
  byId: new Map(),
  deleted: new Set(),
  columns: { pending: emptyColumn(), in_progress: emptyColumn(), completed: emptyColumn() },
  moves: new Map(),
  deleting: new Set(),
  cardErrors: new Map(),
  arrived: new Set(),
  epoch: 0,
  resync: null,
});

export const displayedStatus = (state: BoardState, row: TaskView): TaskStatus =>
  state.moves.get(row.id)?.to ?? row.status;

export const visibleTasks = (state: BoardState, status: TaskStatus): TaskView[] =>
  [...state.byId.values()]
    .filter((row) => !state.deleted.has(row.id) && displayedStatus(state, row) === status)
    .sort(compareTaskKeys);

const copyState = (state: BoardState): BoardState => ({
  ...state,
  byId: new Map(state.byId),
  deleted: new Set(state.deleted),
  moves: new Map(state.moves),
  deleting: new Set(state.deleting),
  cardErrors: new Map(state.cardErrors),
  arrived: new Set(state.arrived),
  columns: {
    pending: { ...state.columns.pending },
    in_progress: { ...state.columns.in_progress },
    completed: { ...state.columns.completed },
  },
  resync: state.resync ? { ...state.resync, log: [...state.resync.log] } : null,
});

const mergeTask = (state: BoardState, row: TaskView): void => {
  if (state.deleted.has(row.id)) return;
  const previous = state.byId.get(row.id);
  if (!previous || compareTimestamps(row.updated_at, previous.updated_at) > 0)
    state.byId.set(row.id, row);
};

const forgetTask = (state: BoardState, id: number): void => {
  state.byId.delete(id);
  state.moves.delete(id);
  state.deleting.delete(id);
  state.cardErrors.delete(id);
  state.arrived.delete(id);
};

const removeTask = (state: BoardState, id: number): void => {
  forgetTask(state, id);
  state.deleted.add(id);
};

const changeId = (change: BoardChange): number | undefined => {
  if ('task' in change) return change.task.id;
  if (change.type === 'task.deleted') return change.task_id;
  if (change.type === 'local.removed') return change.id;
  return undefined;
};

const applyChange = (state: BoardState, change: BoardChange): void => {
  if ('task' in change) {
    if (
      change.type === 'task.created' &&
      !state.byId.has(change.task.id) &&
      !state.deleted.has(change.task.id)
    ) {
      state.arrived.add(change.task.id);
    }
    mergeTask(state, change.task);
  } else {
    const id = changeId(change);
    if (id !== undefined) removeTask(state, id);
  }
};

const setPage = (column: ColumnLoad, page: TaskPage): void => {
  column.cursor = page.next_cursor;
  column.complete = page.next_cursor === null;
  const last = page.items.at(-1);
  if (last) column.edge = taskKey(last);
  column.error = false;
};

const finishResync = (state: BoardState, pages: Record<TaskStatus, TaskPage>): void => {
  const resync = state.resync;
  if (!resync) return;
  const returned = new Set<number>();
  for (const status of taskStatuses) {
    for (const row of pages[status].items) {
      returned.add(row.id);
      mergeTask(state, row);
    }
    state.columns[status].edge = null;
    setPage(state.columns[status], pages[status]);
  }
  const mentioned = new Set(resync.log.map(changeId));
  for (const row of state.byId.values()) {
    if (
      (!resync.frontier || compareTaskKeys(row, resync.frontier) <= 0) &&
      !returned.has(row.id) &&
      !state.moves.has(row.id) &&
      !mentioned.has(row.id)
    )
      forgetTask(state, row.id);
  }
  for (const change of resync.log) applyChange(state, change);
  state.resync = null;
};

export const boardReducer = (previous: BoardState, action: BoardAction): BoardState => {
  if (action.type === 'auth.revoked') return previous;
  if ('epoch' in action && action.type !== 'resync.started' && action.epoch !== previous.epoch)
    return previous;
  if (action.type === 'resync.started' && action.epoch <= previous.epoch) return previous;
  if (
    action.type.startsWith('page.') &&
    'status' in action &&
    'requestId' in action &&
    action.type !== 'page.started' &&
    previous.columns[action.status].requestId !== action.requestId
  )
    return previous;
  const state = copyState(previous);
  switch (action.type) {
    case 'task.created':
    case 'task.updated':
    case 'task.deleted':
    case 'local.updated':
    case 'local.removed':
      applyChange(state, action);
      state.resync?.log.push(action);
      break;
    case 'arrival.finished':
      state.arrived.delete(action.id);
      break;
    case 'move.target': {
      if (!state.byId.has(action.id) || state.deleting.has(action.id)) return previous;
      state.moves.set(action.id, {
        to: action.to,
        inFlight: state.moves.get(action.id)?.inFlight ?? false,
      });
      state.cardErrors.delete(action.id);
      break;
    }
    case 'move.started': {
      const move = state.moves.get(action.id);
      if (move) state.moves.set(action.id, { ...move, inFlight: true });
      break;
    }
    case 'move.finished': {
      const move = state.moves.get(action.id);
      if (move?.to === state.byId.get(action.id)?.status) state.moves.delete(action.id);
      else if (move) state.moves.set(action.id, { ...move, inFlight: false });
      break;
    }
    case 'move.failed':
      state.moves.delete(action.id);
      if (state.byId.has(action.id)) state.cardErrors.set(action.id, 'move');
      break;
    case 'delete.started':
      if (!state.byId.has(action.id)) return previous;
      state.moves.delete(action.id);
      state.deleting.add(action.id);
      state.cardErrors.delete(action.id);
      break;
    case 'delete.failed':
      state.deleting.delete(action.id);
      if (state.byId.has(action.id)) state.cardErrors.set(action.id, 'delete');
      break;
    case 'page.started':
      state.columns[action.status].loading = true;
      state.columns[action.status].error = false;
      state.columns[action.status].requestId = action.requestId;
      break;
    case 'page.received':
      if (state.columns[action.status].cursor !== action.cursor) return previous;
      for (const row of action.page.items) mergeTask(state, row);
      setPage(state.columns[action.status], action.page);
      break;
    case 'page.finished':
      state.columns[action.status].loading = false;
      state.columns[action.status].requestId = null;
      if (action.error) state.columns[action.status].error = true;
      break;
    case 'resync.started':
      state.epoch = action.epoch;
      state.resync = { epoch: action.epoch, frontier: action.frontier, log: [] };
      for (const column of Object.values(state.columns)) {
        column.loading = false;
        column.requestId = null;
      }
      break;
    case 'resync.completed':
      finishResync(state, action.pages);
      break;
    case 'resync.failed':
      state.resync = null;
      for (const column of Object.values(state.columns)) {
        column.loading = false;
        column.requestId = null;
      }
      if (action.status) state.columns[action.status].error = true;
      break;
  }
  return state;
};
