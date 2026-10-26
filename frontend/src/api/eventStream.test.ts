import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from './client';
import { createEventStream } from './eventStream';
import type { EventStreamOptions, StreamSource } from './eventStream';
import type { RealtimeTicket, ServerEvent } from './index';

const ticket: RealtimeTicket = { ticket: 'single-use+/=', expires_in: 60 };
const deleted: ServerEvent = { type: 'task.deleted', task_id: 7 };
const flush = async (): Promise<void> => {
  await Promise.resolve();
  await Promise.resolve();
};

const setup = (overrides: Partial<EventStreamOptions> = {}) => {
  const sources: StreamSource[] = [];
  const waits: { milliseconds: number; signal: AbortSignal; resolve: () => void }[] = [];
  const issueTicket = vi.fn<EventStreamOptions['issueTicket']>().mockResolvedValue(ticket);
  const openSource = vi.fn<NonNullable<EventStreamOptions['openSource']>>(() => {
    const source: StreamSource = { close: vi.fn(), onopen: null, onmessage: null, onerror: null };
    sources.push(source);
    return source;
  });
  const sleep = vi.fn(
    (milliseconds: number, signal: AbortSignal) =>
      new Promise<void>((resolve) => waits.push({ milliseconds, signal, resolve })),
  );
  const onOpen = vi.fn<EventStreamOptions['onOpen']>();
  const onEvent = vi.fn<EventStreamOptions['onEvent']>();
  const onStatus = vi.fn<EventStreamOptions['onStatus']>();
  const onUnauthorized = vi.fn<EventStreamOptions['onUnauthorized']>();
  const stream = createEventStream({
    issueTicket,
    openSource,
    sleep,
    random: () => 1,
    onOpen,
    onEvent,
    onStatus,
    onUnauthorized,
    ...overrides,
  });
  const source = (index = sources.length - 1): StreamSource => {
    const value = sources[index];
    if (!value) throw new Error('Expected a source.');
    return value;
  };
  return {
    stream,
    source,
    sources,
    waits,
    issueTicket,
    openSource,
    sleep,
    onOpen,
    onEvent,
    onStatus,
    onUnauthorized,
  };
};

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('createEventStream', () => {
  it('starts once, encodes the ticket and confirms each subscription generation', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    test.stream.start();
    expect(test.onStatus).toHaveBeenCalledExactlyOnceWith('connecting');
    await flush();
    expect(test.issueTicket).toHaveBeenCalledExactlyOnceWith(expect.any(AbortSignal));
    expect(test.openSource).toHaveBeenCalledExactlyOnceWith(
      '/api/events?ticket=single-use%2B%2F%3D',
    );
    test.source().onopen?.(new Event('open'));
    expect(test.onStatus).toHaveBeenLastCalledWith('live');
    expect(test.onOpen).toHaveBeenLastCalledWith(1);
    test.source().onerror?.(new Event('error'));
    test.waits[0]?.resolve();
    await flush();
    test.source().onopen?.(new Event('open'));
    expect(test.onOpen).toHaveBeenLastCalledWith(2);
    test.stream.stop();
  });

  it('closes a failed source before the backoff and buys one new ticket afterward', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    const first = test.source();
    first.onerror?.(new Event('error'));
    expect(first.close).toHaveBeenCalledOnce();
    expect(test.sleep).toHaveBeenCalledExactlyOnceWith(1_000, expect.any(AbortSignal));
    expect(test.onStatus).toHaveBeenLastCalledWith('reconnecting');
    expect(test.issueTicket).toHaveBeenCalledTimes(1);
    expect(vi.mocked(first.close).mock.invocationCallOrder[0]).toBeLessThan(
      test.sleep.mock.invocationCallOrder[0] ?? 0,
    );
    test.waits[0]?.resolve();
    await flush();
    expect(test.issueTicket).toHaveBeenCalledTimes(2);
    expect(test.openSource).toHaveBeenCalledTimes(2);
    test.stream.stop();
  });

  it('validates frames with the discriminated schema and logs no received content', async () => {
    vi.useFakeTimers();
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const test = setup();
    test.stream.start();
    await flush();
    const source = test.source();
    source.onopen?.(new Event('open'));
    source.onmessage?.(new MessageEvent('message', { data: JSON.stringify(deleted) }));
    expect(test.onEvent).toHaveBeenCalledExactlyOnceWith(deleted);
    for (const data of ['not json secret', '{"task_id":7}', '{"type":"unknown","secret":1}']) {
      source.onmessage?.(new MessageEvent('message', { data }));
    }
    expect(test.onEvent).toHaveBeenCalledOnce();
    expect(warning).toHaveBeenCalledTimes(3);
    expect(warning.mock.calls.every((call) => call.length === 1)).toBe(true);
    expect(JSON.stringify(warning.mock.calls)).not.toContain('secret');
    expect(test.sleep).not.toHaveBeenCalled();
    test.stream.stop();
  });

  it('reconnects on auth.revoked without dispatching it or signing out', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    const first = test.source();
    first.onmessage?.(new MessageEvent('message', { data: '{"type":"auth.revoked"}' }));
    expect(first.close).toHaveBeenCalledOnce();
    expect(test.onUnauthorized).not.toHaveBeenCalled();
    expect(test.onEvent).not.toHaveBeenCalled();
    test.waits[0]?.resolve();
    await flush();
    expect(test.sources).toHaveLength(2);
    test.stream.stop();
  });

  it('stops and signs out only for a ticket ApiRequestError with status 401', async () => {
    vi.useFakeTimers();
    const issueTicket = vi
      .fn<EventStreamOptions['issueTicket']>()
      .mockRejectedValue(new ApiRequestError(401, 'Request a new dashboard link.'));
    const test = setup({ issueTicket });
    test.stream.start();
    await flush();
    expect(test.onUnauthorized).toHaveBeenCalledOnce();
    expect(test.openSource).not.toHaveBeenCalled();
    expect(test.sleep).not.toHaveBeenCalled();
    test.stream.reconnect();
    await flush();
    expect(issueTicket).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('backs off if creating the native source fails', async () => {
    const test = setup({
      openSource: () => {
        throw new Error('EventSource could not be created.');
      },
    });
    test.stream.start();
    await flush();
    expect(test.sleep).toHaveBeenCalledExactlyOnceWith(1_000, expect.any(AbortSignal));
    expect(test.onUnauthorized).not.toHaveBeenCalled();
    test.stream.stop();
  });

  it.each([new ApiRequestError(503, 'Unavailable'), { status: 401 }, new Error('Timeout')])(
    'retries a non-authoritative ticket failure: %s',
    async (error) => {
      const issueTicket = vi.fn<EventStreamOptions['issueTicket']>().mockRejectedValueOnce(error);
      issueTicket.mockResolvedValue(ticket);
      const test = setup({ issueTicket });
      test.stream.start();
      await flush();
      expect(test.sleep).toHaveBeenCalledExactlyOnceWith(1_000, expect.any(AbortSignal));
      expect(test.onUnauthorized).not.toHaveBeenCalled();
      test.waits[0]?.resolve();
      await flush();
      expect(test.sources).toHaveLength(1);
      test.stream.stop();
    },
  );

  it('ignores saved callbacks from closed sources', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    const first = test.source();
    const handleOpen = first.onopen;
    const handleMessage = first.onmessage;
    const handleError = first.onerror;
    test.stream.reconnect();
    test.waits[0]?.resolve();
    await flush();
    handleOpen?.(new Event('open'));
    handleMessage?.(new MessageEvent('message', { data: JSON.stringify(deleted) }));
    handleError?.(new Event('error'));
    expect(test.onOpen).not.toHaveBeenCalled();
    expect(test.onEvent).not.toHaveBeenCalled();
    expect(test.sleep).toHaveBeenCalledOnce();
    expect(test.source().close).not.toHaveBeenCalled();
    test.stream.stop();
  });
});
