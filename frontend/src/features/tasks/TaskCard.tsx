import { useEffect, useRef, useState } from 'react';
import type { DragEvent } from 'react';

import type { TaskStatus, TaskView } from '../../api';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { ExpandableText } from '../../components/ExpandableText';
import { cn } from '../../lib/cn';

import { StatusButtons } from './StatusButtons';
import { taskDragType } from './taskPresentation';

type OwnProps = {
  task: TaskView;
  status: TaskStatus;
  deleting: boolean;
  error?: 'move' | 'delete';
  arrived: boolean;
  draggable: boolean;
  onMove: (id: number, status: TaskStatus) => void;
  onDelete: (task: TaskView) => void;
  onArrivalEnd: (id: number) => void;
};

export const TaskCard = ({
  task,
  status,
  deleting,
  error,
  arrived,
  draggable,
  onMove,
  onDelete,
  onArrivalEnd,
}: OwnProps) => {
  const [confirming, setConfirming] = useState(false);
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!arrived) return;
    // Reduced-motion users do not emit animationend, so the mark still needs a deadline.
    const timer = setTimeout(() => {
      onArrivalEnd(task.id);
    }, 1800);
    const element = card.current;
    const handleAnimationEnd = (event: Event): void => {
      if (event.target !== element) return;
      clearTimeout(timer);
      onArrivalEnd(task.id);
    };
    element?.addEventListener('animationend', handleAnimationEnd);
    return () => {
      clearTimeout(timer);
      element?.removeEventListener('animationend', handleAnimationEnd);
    };
  }, [arrived, onArrivalEnd, task.id]);
  const handleDragStart = (event: DragEvent<HTMLElement>): void => {
    if (!draggable || deleting) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(taskDragType, String(task.id));
    event.dataTransfer.effectAllowed = 'move';
  };
  const handleDelete = (): void => {
    setConfirming(true);
  };
  const handleCancel = (): void => {
    setConfirming(false);
  };
  const handleConfirm = (): void => {
    setConfirming(false);
    onDelete(task);
  };
  const handleMove = (target: TaskStatus): void => {
    onMove(task.id, target);
  };
  return (
    <article
      ref={card}
      draggable={draggable && !deleting}
      onDragStart={handleDragStart}
      aria-busy={deleting}
      className={cn(
        'min-w-0 rounded-lg border border-l-[3px] border-sand-6 bg-sand-3 p-3.5',
        arrived && 'task-arrival',
        deleting && 'opacity-50',
      )}
    >
      <div className="flex items-start gap-2">
        <h3 className="min-w-0 flex-1 text-sm leading-5 font-semibold break-words text-sand-12">
          {task.title}
        </h3>
        <button
          type="button"
          aria-label="Delete task"
          disabled={deleting}
          onClick={handleDelete}
          className="-mt-1 -mr-1 flex size-8 shrink-0 items-center justify-center rounded text-sand-11 hover:bg-danger-3 hover:text-danger-11 disabled:cursor-wait"
        >
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" className="size-4">
            <path
              d="m4 4 8 8m0-8-8 8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>
      </div>
      {task.description ? (
        <div className="mt-2">
          <ExpandableText text={task.description} />
        </div>
      ) : null}
      <details className="mt-2 text-xs leading-5 text-sand-11">
        <summary className="w-fit rounded py-1 hover:text-sand-12">Source message</summary>
        <p className="mt-1 border-l border-sand-7 pl-3 break-words whitespace-pre-wrap">
          {task.source_text}
        </p>
      </details>
      <div className="mt-2.5">
        <StatusButtons status={status} disabled={deleting} onMove={handleMove} />
      </div>
      {error ? (
        <p role="alert" className="mt-3 text-xs leading-5 text-danger-11">
          {error === 'move'
            ? "Couldn't change the status. Try again."
            : "Couldn't delete the task. Try again."}
        </p>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={task.title}
        onConfirm={handleConfirm}
        onCancel={handleCancel}
      />
    </article>
  );
};
