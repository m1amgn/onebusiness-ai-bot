import { useId } from 'react';
import type { DragEvent } from 'react';

import type { TaskStatus } from '../../api';
import { cn } from '../../lib/cn';

import { TaskCard } from './TaskCard';
import { displayedStatus, visibleTasks } from './taskState';
import { statusAppearance, taskDragType } from './taskPresentation';
import type { BoardPresentationProps } from './taskPresentation';

type OwnProps = BoardPresentationProps & { status: TaskStatus; draggable: boolean };

export const TaskColumn = ({
  state,
  status,
  draggable,
  onMove,
  onDelete,
  onLoadMore,
  onRetry,
  onArrivalEnd,
}: OwnProps) => {
  const titleId = useId();
  const appearance = statusAppearance[status];
  const rows = visibleTasks(state, status);
  const column = state.columns[status];
  const skeleton = rows.length === 0 && !column.error && !column.complete && column.edge === null;
  const handleDragOver = (event: DragEvent<HTMLElement>): void => {
    if (!draggable || !event.dataTransfer.types.includes(taskDragType)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  };
  const handleDrop = (event: DragEvent<HTMLElement>): void => {
    if (!draggable || !event.dataTransfer.types.includes(taskDragType)) return;
    event.preventDefault();
    const payload = event.dataTransfer.getData(taskDragType);
    if (!/^\d+$/.test(payload)) return;
    const id = Number(payload);
    const row = state.byId.get(id);
    if (
      !row ||
      state.deleted.has(id) ||
      state.deleting.has(id) ||
      displayedStatus(state, row) === status
    )
      return;
    onMove(id, status);
  };
  const handleLoadMore = (): void => {
    onLoadMore(status);
  };
  const handleRetry = (): void => {
    onRetry(status);
  };
  return (
    <section
      aria-labelledby={titleId}
      aria-busy={skeleton || column.loading}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      className="min-w-0 flex-1 rounded-xl bg-sand-1 p-3"
    >
      <div className="mb-3 flex min-h-6 items-center gap-2 px-1">
        <span aria-hidden="true" className={cn('size-2.5 rounded-xs', appearance.dot)} />
        <h2 id={titleId} className="text-sm font-semibold">
          {appearance.label}
        </h2>
        <span className="text-xs text-sand-11">
          {rows.length}
          {column.complete ? '' : '+'}
        </span>
      </div>
      <div className="space-y-2">
        {rows.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            status={status}
            deleting={state.deleting.has(task.id)}
            error={state.cardErrors.get(task.id)}
            arrived={state.arrived.has(task.id)}
            draggable={draggable}
            onMove={onMove}
            onDelete={onDelete}
            onArrivalEnd={onArrivalEnd}
          />
        ))}
        {skeleton ? (
          <div role="status" aria-label="Loading tasks" className="space-y-2">
            {[0, 1, 2].map((index) => (
              <div
                key={index}
                aria-hidden="true"
                className="space-y-3 rounded-lg border border-sand-6 bg-sand-3 p-4 motion-safe:animate-pulse"
              >
                <div className="h-4 w-3/4 rounded bg-sand-6" />
                <div className="h-3 w-full rounded bg-sand-4" />
                <div className="h-3 w-2/3 rounded bg-sand-4" />
                <div className="h-7 w-24 rounded bg-sand-6" />
              </div>
            ))}
          </div>
        ) : null}
        {!skeleton && !column.error && column.complete && rows.length === 0 ? (
          <p className="px-1 py-8 text-center text-sm text-sand-11">{appearance.empty}</p>
        ) : null}
        {column.error ? (
          <div className="rounded-lg border border-sand-6 p-4 text-sm">
            <p role="alert" className="text-sand-11">
              Couldn't load tasks.
            </p>
            <button
              type="button"
              onClick={handleRetry}
              disabled={column.loading}
              className="mt-2 min-h-9 rounded-md border border-sand-7 px-3 font-medium hover:bg-sand-4 disabled:cursor-wait disabled:opacity-50"
            >
              Retry
            </button>
          </div>
        ) : null}
        {!skeleton && !column.error && !column.complete && column.cursor !== null ? (
          <button
            type="button"
            onClick={handleLoadMore}
            disabled={column.loading || state.resync !== null}
            className="min-h-11 w-full rounded-lg border border-sand-6 text-xs font-medium text-sand-11 hover:bg-sand-3 disabled:cursor-wait disabled:opacity-50"
          >
            {column.loading ? 'Loading…' : 'Load more'}
          </button>
        ) : null}
      </div>
    </section>
  );
};
