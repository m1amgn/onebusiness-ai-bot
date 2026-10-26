import { TaskColumn } from './TaskColumn';
import { taskStatuses } from './taskState';
import type { BoardPresentationProps } from './taskPresentation';

type OwnProps = BoardPresentationProps;

export const BoardColumns = (props: OwnProps) => (
  <div className="grid min-h-[65vh] grid-cols-3 gap-3">
    {taskStatuses.map((status) => (
      <TaskColumn key={status} {...props} status={status} draggable />
    ))}
  </div>
);
