import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

configure({ reactStrictMode: true });
afterEach(cleanup);

if (typeof window !== 'undefined') {
  window.matchMedia = (media: string): MediaQueryList => ({
    media,
    matches: true,
    onchange: null,
    // eslint-disable-next-line @typescript-eslint/no-deprecated -- The native stub includes legacy MediaQueryList methods.
    addListener: () => undefined,
    // eslint-disable-next-line @typescript-eslint/no-deprecated -- The native stub includes legacy MediaQueryList methods.
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => true,
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
