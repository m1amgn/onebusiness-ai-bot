import type { ConnectionStatus as Status } from '../../api/eventStream';
import { cn } from '../../lib/cn';

type OwnProps = { status: Status };

const labels = { connecting: 'Connecting…', live: 'Live', reconnecting: 'Reconnecting…' } as const;

export const ConnectionStatus = ({ status }: OwnProps) => (
  <span role="status" className="flex items-center gap-2 text-xs text-sand-11">
    <span
      aria-hidden="true"
      className={cn('size-1.5 rounded-full', status === 'live' ? 'bg-done-9' : 'bg-wait-9')}
    />
    {labels[status]}
  </span>
);
