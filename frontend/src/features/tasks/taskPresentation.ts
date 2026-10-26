import type { TaskStatus, TaskView } from '../../api';

import type { BoardState } from './taskState';

export type BoardPresentationProps = {
  state: BoardState;
  onMove: (id: number, status: TaskStatus) => void;
  onDelete: (task: TaskView) => void;
  onLoadMore: (status: TaskStatus) => void;
  onRetry: (status: TaskStatus) => void;
  onArrivalEnd: (id: number) => void;
};

export const taskDragType = 'application/x-taskbot-task';

export const statusAppearance = {
  pending: {
    label: 'Pending',
    empty: 'No pending tasks.',
    dot: 'bg-wait-9',
    tab: 'border-wait-9 text-wait-11',
    forward: 'border-wait-9 bg-wait-9 text-sand-1',
  },
  in_progress: {
    label: 'In Progress',
    empty: 'No tasks in progress.',
    dot: 'bg-move-9',
    tab: 'border-move-9 text-move-11',
    forward: 'border-move-9 bg-move-9 text-sand-1',
  },
  completed: {
    label: 'Completed',
    empty: 'No completed tasks.',
    dot: 'bg-done-9',
    tab: 'border-done-9 text-done-11',
    forward: 'border-done-9 bg-done-9 text-sand-1',
  },
} as const;
