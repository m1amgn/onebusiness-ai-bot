import type { ServerEvent, TaskPage, TaskStatus, TaskView } from '../../api';

export type TaskKey = Pick<TaskView, 'inbound_message_id' | 'position'>;

export type ColumnLoad = {
  cursor: string | null;
  complete: boolean;
  edge: TaskKey | null;
  loading: boolean;
  error: boolean;
  requestId: number | null;
};

export type BoardChange =
  ServerEvent | { type: 'local.updated'; task: TaskView } | { type: 'local.removed'; id: number };

export type BoardState = {
  byId: Map<number, TaskView>;
  deleted: Set<number>;
  columns: Record<TaskStatus, ColumnLoad>;
  moves: Map<number, { to: TaskStatus }>;
  deleting: Set<number>;
  cardErrors: Map<number, 'move' | 'delete'>;
  arrived: Set<number>;
  epoch: number;
  resync: { epoch: number; frontier: TaskKey | null; log: BoardChange[] } | null;
};

type PageRequest = { status: TaskStatus; epoch: number; requestId: number };

export type BoardAction =
  | BoardChange
  | { type: 'arrival.finished'; id: number }
  | { type: 'move.target'; id: number; to: TaskStatus }
  | { type: 'move.finished' | 'move.failed'; id: number }
  | { type: 'delete.started' | 'delete.failed'; id: number }
  | ({ type: 'page.started' } & PageRequest)
  | ({ type: 'page.received'; cursor: string | null; page: TaskPage } & PageRequest)
  | ({ type: 'page.finished'; error?: boolean } & PageRequest)
  | { type: 'resync.started'; epoch: number; frontier: TaskKey | null }
  | { type: 'resync.completed'; epoch: number; pages: Record<TaskStatus, TaskPage> }
  | { type: 'resync.failed'; epoch: number; status?: TaskStatus };
