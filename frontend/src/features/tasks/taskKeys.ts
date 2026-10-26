import type { TaskView } from '../../api';

import type { BoardState, TaskKey } from './taskStateTypes';

export const taskKey = ({ inbound_message_id, position }: TaskView): TaskKey => ({
  inbound_message_id,
  position,
});

// Negative means a precedes b in the API's newest-first order.
export const compareTaskKeys = (a: TaskKey, b: TaskKey): number =>
  b.inbound_message_id - a.inbound_message_id || a.position - b.position;

export const oldestKey = (a: TaskKey | null, b: TaskKey | null): TaskKey | null =>
  !a ? b : !b || compareTaskKeys(a, b) >= 0 ? a : b;

export const boardFrontier = (state: BoardState): TaskKey | null => {
  let frontier: TaskKey | null = null;
  for (const column of Object.values(state.columns)) frontier = oldestKey(frontier, column.edge);
  for (const row of state.byId.values()) {
    if (!state.deleted.has(row.id)) frontier = oldestKey(frontier, taskKey(row));
  }
  return frontier;
};
