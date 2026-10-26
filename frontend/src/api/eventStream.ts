import { reconnectDelayMs } from '../lib/backoff';
import { ApiRequestError } from './client';
import { zServerEvent } from './index';
import type { RealtimeTicket, ServerEvent } from './index';

export type ConnectionStatus = 'connecting' | 'live' | 'reconnecting';

export type StreamSource = {
  close: () => void;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
};

export type EventStreamOptions = {
  issueTicket: (signal: AbortSignal) => Promise<RealtimeTicket>;
  openSource?: (url: string) => StreamSource;
  sleep?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  random?: () => number;
  onOpen: (generation: number) => void;
  onEvent: (event: ServerEvent) => void;
  onStatus: (status: ConnectionStatus) => void;
  onUnauthorized: () => void;
};

type Attempt = {
  controller: AbortController;
  source?: StreamSource;
  timer?: ReturnType<typeof setTimeout>;
  waiting: boolean;
};

const sleepUntilRetry = (milliseconds: number, signal: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('The wait was aborted.', 'AbortError'));
      return;
    }
    const handleAbort = (): void => {
      clearTimeout(timer);
      signal.removeEventListener('abort', handleAbort);
      reject(new DOMException('The wait was aborted.', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', handleAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener('abort', handleAbort, { once: true });
  });

const parseEvent = (data: string): ServerEvent | undefined => {
  try {
    const body: unknown = JSON.parse(data);
    const event = zServerEvent.safeParse(body);
    if (event.success) return event.data;
  } catch {
    // A malformed frame must not interrupt the connection or expose its payload.
  }
  console.warn('Ignored an invalid real-time event.');
  return undefined;
};

export const createEventStream = ({
  issueTicket,
  openSource = (url) => new EventSource(url),
  sleep = sleepUntilRetry,
  random = Math.random,
  onOpen,
  onEvent,
  onStatus,
  onUnauthorized,
}: EventStreamOptions) => {
  let running = false;
  let current: Attempt | undefined;
  let failures = 0;
  let generation = 0;

  const isCurrent = (attempt: Attempt): boolean =>
    running && current === attempt && !attempt.controller.signal.aborted;

  const disposeCurrent = (): void => {
    const attempt = current;
    current = undefined;
    if (!attempt) return;
    if (attempt.source) {
      attempt.source.onopen = null;
      attempt.source.onmessage = null;
      attempt.source.onerror = null;
      attempt.source.close();
    }
    clearTimeout(attempt.timer);
    attempt.controller.abort();
  };

  const stop = (): void => {
    running = false;
    disposeCurrent();
  };

  const reconnect = (): void => {
    if (!running || current?.waiting) return;
    disposeCurrent();
    const attempt: Attempt = { controller: new AbortController(), waiting: true };
    current = attempt;
    failures += 1;
    onStatus('reconnecting');
    const wait = async (): Promise<void> => {
      if (!isCurrent(attempt)) return;
      try {
        await sleep(reconnectDelayMs(failures, random), attempt.controller.signal);
      } catch {
        return;
      }
      if (isCurrent(attempt)) await connect();
    };
    void wait();
  };

  const connect = async (): Promise<void> => {
    if (!running) return;
    disposeCurrent();
    const attempt: Attempt = { controller: new AbortController(), waiting: false };
    current = attempt;
    let ticket: RealtimeTicket;
    try {
      ticket = await issueTicket(attempt.controller.signal);
    } catch (error) {
      if (!isCurrent(attempt)) return;
      if (error instanceof ApiRequestError && error.status === 401) {
        stop();
        onUnauthorized();
        return;
      }
      reconnect();
      return;
    }
    if (!isCurrent(attempt)) return;
    try {
      attempt.source = openSource(`/api/events?ticket=${encodeURIComponent(ticket.ticket)}`);
    } catch {
      reconnect();
      return;
    }
    attempt.timer = setTimeout(() => {
      if (isCurrent(attempt)) reconnect();
    }, 15_000);
    attempt.source.onopen = () => {
      if (!isCurrent(attempt)) return;
      clearTimeout(attempt.timer);
      attempt.timer = setTimeout(() => {
        if (isCurrent(attempt)) failures = 0;
      }, 30_000);
      generation += 1;
      onStatus('live');
      if (isCurrent(attempt)) onOpen(generation);
    };
    attempt.source.onmessage = (message) => {
      if (!isCurrent(attempt)) return;
      const event = parseEvent(message.data);
      if (!event) return;
      if (event.type === 'auth.revoked') {
        reconnect();
        return;
      }
      onEvent(event);
    };
    attempt.source.onerror = () => {
      if (isCurrent(attempt)) reconnect();
    };
  };

  const start = (): void => {
    if (running) return;
    running = true;
    failures = 0;
    onStatus('connecting');
    void connect();
  };

  return { start, stop, reconnect };
};
