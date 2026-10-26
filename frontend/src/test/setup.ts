import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

configure({ reactStrictMode: true });
afterEach(cleanup);

if (typeof window !== 'undefined') {
  const matchesWidth = (media: string): boolean => {
    const minimum = /min-width:\s*(\d+)px/.exec(media)?.[1];
    const maximum = /max-width:\s*(\d+)px/.exec(media)?.[1];
    return (
      (!minimum || window.innerWidth >= Number(minimum)) &&
      (!maximum || window.innerWidth <= Number(maximum))
    );
  };
  const queries = new Map<string, MediaQueryList>();
  window.matchMedia = (media: string): MediaQueryList => {
    const existing = queries.get(media);
    if (existing) return existing;
    const query = Object.assign(new EventTarget(), {
      media,
      matches: matchesWidth(media),
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
    });
    Object.defineProperty(query, 'matches', { get: () => matchesWidth(media) });
    queries.set(media, query);
    return query;
  };
  window.addEventListener('resize', () => {
    for (const query of queries.values()) {
      query.dispatchEvent(
        Object.assign(new Event('change'), { matches: query.matches, media: query.media }),
      );
    }
  });
  afterEach(() => {
    queries.clear();
  });
  globalThis.ResizeObserver = class implements ResizeObserver {
    observe = (): void => undefined;
    unobserve = (): void => undefined;
    disconnect = (): void => undefined;
  };
  // Native methods need their dynamic receiver to modify the dialog being opened.
  const handleDialogOpen = function (this: HTMLDialogElement): void {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.showModal = handleDialogOpen;
  HTMLDialogElement.prototype.show = handleDialogOpen;
  HTMLDialogElement.prototype.close = function (this: HTMLDialogElement): void {
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}
