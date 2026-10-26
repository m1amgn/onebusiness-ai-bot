import type { TaskView } from '../../api';

import { BoardColumns } from './BoardColumns';
import { ConnectionStatus } from './ConnectionStatus';
import { PhoneTabs } from './PhoneTabs';
import { useTaskBoard } from './useTaskBoard';
import { useWideLayout } from './useWideLayout';

type PageProps = {
  token: string;
  onUnauthorized: (token: string) => void;
};

export const TaskBoardPage = ({ token, onUnauthorized }: PageProps) => {
  const board = useTaskBoard(token, onUnauthorized);
  const wide = useWideLayout();
  const presentation = {
    state: board.state,
    onMove: board.handleMove,
    onDelete: (task: TaskView): void => {
      board.handleDelete(task.id);
    },
    onLoadMore: board.handleLoadMore,
    onRetry: board.handleRetry,
    onArrivalEnd: board.handleArrivalEnd,
  };

  return (
    <main className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-10 lg:py-9">
      <header className="mb-6 flex items-center justify-between gap-4 lg:mb-8">
        <h1 className="text-[28px] font-semibold tracking-tight text-sand-12">Tasks</h1>
        <ConnectionStatus status={board.connectionStatus} />
      </header>
      {wide ? <BoardColumns {...presentation} /> : <PhoneTabs {...presentation} />}
    </main>
  );
};
