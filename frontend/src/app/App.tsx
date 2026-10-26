import { SessionGate } from '../features/session/SessionGate';
import { TaskBoardPage } from '../features/tasks/TaskBoardPage';

export const App = () => (
  <SessionGate>
    {(token, clearIfCurrent) => (
      <TaskBoardPage key={token} token={token} onUnauthorized={clearIfCurrent} />
    )}
  </SessionGate>
);
