import type { TaskStatus } from '../../api';
import { ChevronIcon } from '../../components/ChevronIcon';
import { cn } from '../../lib/cn';

import { taskStatuses } from './taskState';
import { statusAppearance } from './taskPresentation';

type OwnProps = { status: TaskStatus; disabled: boolean; onMove: (status: TaskStatus) => void };

export const StatusButtons = ({ status, disabled, onMove }: OwnProps) => {
  const index = taskStatuses.indexOf(status);
  const previous = taskStatuses[index - 1];
  const next = taskStatuses[index + 1];
  const handlePrevious = (): void => {
    if (previous) onMove(previous);
  };
  const handleNext = (): void => {
    if (next) onMove(next);
  };
  return (
    <div className="flex flex-wrap gap-2">
      {previous ? (
        <button
          type="button"
          disabled={disabled}
          onClick={handlePrevious}
          className="flex min-h-9 items-center gap-1 rounded-md border border-sand-7 px-2.5 text-xs font-medium text-sand-11 hover:bg-sand-4 disabled:cursor-wait disabled:opacity-50"
        >
          <ChevronIcon direction="left" />
          {statusAppearance[previous].label}
        </button>
      ) : null}
      {next ? (
        <button
          type="button"
          disabled={disabled}
          onClick={handleNext}
          className={cn(
            'flex min-h-9 items-center gap-1 rounded-md border px-2.5 text-xs font-semibold hover:brightness-110 disabled:cursor-wait disabled:opacity-50',
            statusAppearance[next].forward,
          )}
        >
          {statusAppearance[next].label}
          <ChevronIcon direction="right" />
        </button>
      ) : null}
    </div>
  );
};
