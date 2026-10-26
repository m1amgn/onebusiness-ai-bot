import { useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';

import type { TaskStatus } from '../../api';
import { cn } from '../../lib/cn';

import { TaskColumn } from './TaskColumn';
import { taskStatuses, visibleTasks } from './taskState';
import { statusAppearance } from './taskPresentation';
import type { BoardPresentationProps } from './taskPresentation';

type OwnProps = BoardPresentationProps;

export const PhoneTabs = (props: OwnProps) => {
  const [selected, setSelected] = useState<TaskStatus>('pending');
  const tabButtons = useRef(new Map<TaskStatus, HTMLButtonElement>());
  const tabsId = useId();
  const panelId = `${tabsId}-panel`;
  const handleSelect = (status: TaskStatus): void => {
    setSelected(status);
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>): void => {
    const index = taskStatuses.indexOf(selected);
    let next: TaskStatus | undefined;
    switch (event.key) {
      case 'ArrowRight':
        next = taskStatuses[(index + 1) % 3];
        break;
      case 'ArrowLeft':
        next = taskStatuses[(index + 2) % 3];
        break;
      case 'Home':
        next = taskStatuses[0];
        break;
      case 'End':
        next = taskStatuses[2];
        break;
      default:
        return;
    }
    if (!next) return;
    event.preventDefault();
    setSelected(next);
    tabButtons.current.get(next)?.focus();
  };
  return (
    <div>
      <div
        role="tablist"
        aria-label="Task status"
        className="mb-3 grid grid-cols-3 border-b border-sand-6"
      >
        {taskStatuses.map((status) => (
          <button
            key={status}
            ref={(element) => {
              if (element) tabButtons.current.set(status, element);
              else tabButtons.current.delete(status);
            }}
            type="button"
            role="tab"
            id={`${tabsId}-${status}`}
            aria-controls={panelId}
            aria-selected={selected === status}
            tabIndex={selected === status ? 0 : -1}
            onClick={() => {
              handleSelect(status);
            }}
            onKeyDown={handleKeyDown}
            className={cn(
              'flex min-h-12 items-center justify-center gap-1.5 border-b-2 border-transparent px-1 text-xs font-medium text-sand-11',
              selected === status && statusAppearance[status].tab,
            )}
          >
            {statusAppearance[status].label}{' '}
            <span className="text-[10px] tabular-nums">
              {visibleTasks(props.state, status).length}
              {props.state.columns[status].complete ? '' : '+'}
            </span>
          </button>
        ))}
      </div>
      <div role="tabpanel" id={panelId} aria-labelledby={`${tabsId}-${selected}`}>
        <TaskColumn {...props} status={selected} draggable={false} />
      </div>
    </div>
  );
};
