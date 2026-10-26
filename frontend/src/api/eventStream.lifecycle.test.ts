import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiRequestError } from './client';
import { createEventStream } from './eventStream';
import type { EventStreamOptions, StreamSource } from './eventStream';
import type { RealtimeTicket } from './index';

const ticket: RealtimeTicket = { ticket: 'new-ticket', expires_in: 60 };
const flush = async (): Promise<void> => {
  await Promise.resolve();
  await Promise.resolve();
};
const setup = (overrides: Partial<EventStreamOptions> = {}) => {
  const sources: StreamSource[] = [];
  const waits: { milliseconds: number; signal: AbortSignal; resolve: () => void }[] = [];
  const issueTicket = vi.fn<EventStreamOptions['issueTicket']>().mockResolvedValue(ticket);
  const openSource = vi.fn((): StreamSource => {
    const source: StreamSource = { close: vi.fn(), onopen: null, onmessage: null, onerror: null };
    sources.push(source);
    return source;
  });
  const onOpen = vi.fn<EventStreamOptions['onOpen']>();
  const onUnauthorized = vi.fn<EventStreamOptions['onUnauthorized']>();
  const stream = createEventStream({
    issueTicket,
    openSource,
    sleep: (milliseconds, signal) =>
      new Promise<void>((resolve) => waits.push({ milliseconds, signal, resolve })),
    random: () => 1,
    onOpen,
    onEvent: vi.fn(),
    onStatus: vi.fn(),
    onUnauthorized,
    ...overrides,
  });
  const source = (): StreamSource => {
    const value = sources.at(-1);
    if (!value) throw new Error('Expected a source.');
    return value;
  };
  return { stream, source, sources, waits, issueTicket, openSource, onOpen, onUnauthorized };
};

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('event stream lifecycle', () => {
  it('replaces a source that never opens after exactly 15 seconds', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    const first = test.source();
    await vi.advanceTimersByTimeAsync(14_999);
    expect(first.close).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(first.close).toHaveBeenCalledOnce();
    expect(test.waits.map((wait) => wait.milliseconds)).toEqual([1_000]);
    test.waits[0]?.resolve();
    await flush();
    expect(test.sources).toHaveLength(2);
    test.stream.stop();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('grows the delay through ten flapping connections', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    for (let index = 0; index < 10; index += 1) {
      test.source().onopen?.(new Event('open'));
      test.source().onerror?.(new Event('error'));
      expect(vi.getTimerCount()).toBe(0);
      test.waits[index]?.resolve();
      await flush();
    }
    expect(test.waits.map((wait) => wait.milliseconds)).toEqual([
      1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000, 30_000, 30_000,
    ]);
    expect(test.onOpen.mock.calls.map(([generation]) => generation)).toEqual(
      Array.from({ length: 10 }, (_, index) => index + 1),
    );
    test.stream.stop();
  });

  it('resets the delay only after 30 seconds continuously open', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.start();
    await flush();
    test.source().onerror?.(new Event('error'));
    test.waits[0]?.resolve();
    await flush();
    test.source().onopen?.(new Event('open'));
    await vi.advanceTimersByTimeAsync(29_999);
    test.stream.reconnect();
    expect(test.waits[1]?.milliseconds).toBe(2_000);
    test.waits[1]?.resolve();
    await flush();
    test.source().onopen?.(new Event('open'));
    await vi.advanceTimersByTimeAsync(30_000);
    test.stream.reconnect();
    expect(test.waits[2]?.milliseconds).toBe(1_000);
    test.stream.stop();
  });

  it('coalesces reconnect requests while waiting and stops a pending wait', async () => {
    vi.useFakeTimers();
    const test = setup();
    test.stream.reconnect();
    expect(test.issueTicket).not.toHaveBeenCalled();
    test.stream.start();
    await flush();
    test.stream.reconnect();
    test.stream.reconnect();
    expect(test.source().close).toHaveBeenCalledOnce();
    expect(test.waits).toHaveLength(1);
    expect(test.issueTicket).toHaveBeenCalledOnce();
    test.stream.stop();
    expect(test.waits[0]?.signal.aborted).toBe(true);
    test.waits[0]?.resolve();
    await flush();
    expect(test.issueTicket).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('aborts a pending ticket and ignores its late resolution after restart', async () => {
    vi.useFakeTimers();
    let resolveTicket: ((value: RealtimeTicket) => void) | undefined;
    const issueTicket = vi
      .fn<EventStreamOptions['issueTicket']>()
      .mockImplementationOnce(() => new Promise((resolve) => (resolveTicket = resolve)))
      .mockResolvedValue(ticket);
    const test = setup({ issueTicket });
    test.stream.start();
    const firstSignal = issueTicket.mock.calls[0]?.[0];
    test.stream.stop();
    expect(firstSignal?.aborted).toBe(true);
    test.stream.start();
    await flush();
    resolveTicket?.(ticket);
    await flush();
    expect(test.openSource).toHaveBeenCalledOnce();
    test.stream.stop();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ignores a late 401 from an aborted ticket attempt', async () => {
    let rejectTicket: ((reason: unknown) => void) | undefined;
    const issueTicket = vi.fn<EventStreamOptions['issueTicket']>(
      () => new Promise((_resolve, reject) => (rejectTicket = reject)),
    );
    const test = setup({ issueTicket });
    test.stream.start();
    test.stream.stop();
    rejectTicket?.(new ApiRequestError(401, 'Expired'));
    await flush();
    expect(test.onUnauthorized).not.toHaveBeenCalled();
    expect(test.waits).toHaveLength(0);
  });

  it.each([false, true])(
    'stop clears the %s opening/live timer and source callbacks',
    async (open) => {
      vi.useFakeTimers();
      const test = setup();
      test.stream.start();
      await flush();
      const source = test.source();
      if (open) source.onopen?.(new Event('open'));
      expect(vi.getTimerCount()).toBe(1);
      test.stream.stop();
      test.stream.stop();
      expect(source.close).toHaveBeenCalledOnce();
      expect(source.onopen).toBeNull();
      expect(source.onmessage).toBeNull();
      expect(source.onerror).toBeNull();
      expect(vi.getTimerCount()).toBe(0);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(test.waits).toHaveLength(0);
    },
  );

  it('uses native EventSource and aborts its default sleep timer', async () => {
    vi.useFakeTimers();
    const source: StreamSource = { close: vi.fn(), onopen: null, onmessage: null, onerror: null };
    const constructor = vi.fn(function () {
      return source;
    });
    vi.stubGlobal('EventSource', constructor);
    const stream = createEventStream({
      issueTicket: () => Promise.resolve(ticket),
      onOpen: vi.fn(),
      onEvent: vi.fn(),
      onStatus: vi.fn(),
      onUnauthorized: vi.fn(),
      random: () => 1,
    });
    stream.start();
    await flush();
    expect(constructor).toHaveBeenCalledExactlyOnceWith('/api/events?ticket=new-ticket');
    source.onerror?.(new Event('error'));
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(constructor).toHaveBeenCalledTimes(2);
    source.onerror?.(new Event('error'));
    stream.stop();
    expect(vi.getTimerCount()).toBe(0);
    await flush();
  });
});
