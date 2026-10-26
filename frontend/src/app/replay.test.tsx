import { act, cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, test, vi } from 'vitest';

import {
  BrowserDataTransfer,
  BrowserStream,
  card,
  column,
  labels,
  checkRow,
  createReplayServer,
  fixtureTask,
} from './replay.testHelpers';
import { checkTextRows } from './replay.text.testHelpers';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

test('replays accepted owner outcomes V1–V7 and V10 through App', { timeout: 60_000 }, async () => {
  vi.resetModules();
  localStorage.clear();
  history.replaceState(null, '', '/#token=synthetic-dashboard-token');
  BrowserStream.instances = [];
  const server = createReplayServer();
  vi.stubGlobal('fetch', server.fetchImpl);
  vi.stubGlobal('EventSource', BrowserStream);
  // jsdom has no layout: model three clamped lines while retaining the real measurement code.
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.textContent.length > 200 ? 240 : 24;
  });
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (
    this: HTMLElement,
  ) {
    return this.classList.contains('line-clamp-3') ? 72 : this.scrollHeight;
  });
  vi.spyOn(Math, 'random').mockReturnValue(0);
  const user = userEvent.setup();
  const { App } = await import('./App');
  const first = render(<App />).container;
  const second = render(<App />).container;
  const boards = [first, second];
  const firstMain = first.querySelector('main');

  await checkRow(
    'V1',
    {
      pending: ['A8', 'A7', 'A6', 'A5', 'A4', 'A3', 'A2', 'A1'],
      inProgress: [],
      completed: [],
      hash: '',
      liveBoards: 2,
      secondPendingCount: 8,
    },
    async () => {
      await waitFor(
        () => {
          for (const board of boards) {
            expect(within(board).getByText('Live')).toBeInTheDocument();
            expect(labels(board, 'Pending')).toHaveLength(8);
            for (const region of within(board).getAllByRole('region'))
              expect(region).toHaveAttribute('aria-busy', 'false');
          }
        },
        { timeout: 5_000 },
      );
      return {
        pending: labels(first, 'Pending'),
        inProgress: labels(first, 'In Progress'),
        completed: labels(first, 'Completed'),
        hash: location.hash,
        liveBoards: boards.filter((board) => within(board).queryByText('Live')).length,
        secondPendingCount: labels(second, 'Pending').length,
      };
    },
  );

  await checkTextRows(first, user);

  const finishWrite = async (): Promise<void> => {
    await act(async () => {
      server.finishWrite();
      await Promise.resolve();
    });
  };
  await checkRow(
    'V4',
    {
      optimistic: true,
      secondBeforeReply: ['A8', 'A7', 'A6', 'A5', 'A4', 'A3', 'A2', 'A1'],
      patchRequests: 1,
      inProgress: [['A6'], ['A6']],
    },
    async () => {
      await user.click(within(card(first, 6)).getByRole('button', { name: 'In Progress' }));
      const optimistic =
        labels(first, 'In Progress').includes('A6') && !labels(first, 'Pending').includes('A6');
      const secondBeforeReply = labels(second, 'Pending');
      await finishWrite();
      await waitFor(() => {
        expect(labels(second, 'In Progress')).toEqual(['A6']);
      });
      return {
        optimistic,
        secondBeforeReply,
        patchRequests: server.requests.patch,
        inProgress: boards.map((board) => labels(board, 'In Progress')),
      };
    },
  );

  await checkRow(
    'V5',
    {
      types: ['application/x-taskbot-task'],
      payload: '3',
      effectAllowed: 'move',
      accepted: true,
      dropEffect: 'move',
      optimistic: true,
      secondBeforeReply: [],
      patchRequests: 2,
      completed: [['A3'], ['A3']],
    },
    async () => {
      const dataTransfer = new BrowserDataTransfer();
      fireEvent.dragStart(card(first, 3), { dataTransfer });
      const target = column(first, 'Completed');
      const accepted = !fireEvent.dragOver(target, { dataTransfer });
      fireEvent.drop(target, { dataTransfer });
      const optimistic = labels(first, 'Completed').includes('A3');
      const secondBeforeReply = labels(second, 'Completed');
      await finishWrite();
      await waitFor(() => {
        expect(labels(second, 'Completed')).toEqual(['A3']);
      });
      return {
        types: dataTransfer.types,
        payload: dataTransfer.getData('application/x-taskbot-task'),
        effectAllowed: dataTransfer.effectAllowed,
        accepted,
        dropEffect: dataTransfer.dropEffect,
        optimistic,
        secondBeforeReply,
        patchRequests: server.requests.patch,
        completed: boards.map((board) => labels(board, 'Completed')),
      };
    },
  );

  await checkRow(
    'V6',
    {
      confirmation: true,
      cancelKeeps: true,
      deletesAfterCancel: 0,
      keptWhileWaiting: true,
      disabledWhileWaiting: true,
      deletesAfterConfirm: 1,
      removedFromBoth: true,
    },
    async () => {
      await user.click(within(card(first, 5)).getByRole('button', { name: 'Delete task' }));
      const dialog = within(first).getByRole('dialog', { name: 'Delete this task?' });
      const confirmation =
        within(dialog).getByText(fixtureTask(5).title).textContent === fixtureTask(5).title;
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      const cancelKeeps = boards.every((board) => labels(board, 'Pending').includes('A5'));
      const deletesAfterCancel = server.requests.delete;
      await user.click(within(card(first, 5)).getByRole('button', { name: 'Delete task' }));
      await user.click(
        within(within(first).getByRole('dialog')).getByRole('button', { name: 'Delete' }),
      );
      const waiting = card(first, 5);
      const keptWhileWaiting = boards.every((board) => labels(board, 'Pending').includes('A5'));
      const disabledWhileWaiting =
        waiting.getAttribute('aria-busy') === 'true' &&
        waiting.getAttribute('draggable') === 'false' &&
        within(waiting)
          .getAllByRole('button')
          .every((button) => (button as HTMLButtonElement).disabled);
      await finishWrite();
      await waitFor(() => {
        for (const board of boards)
          expect(
            within(board).queryByRole('heading', { level: 3, name: fixtureTask(5).title }),
          ).not.toBeInTheDocument();
      });
      return {
        confirmation,
        cancelKeeps,
        deletesAfterCancel,
        keptWhileWaiting,
        disabledWhileWaiting,
        deletesAfterConfirm: server.requests.delete,
        removedFromBoth: boards.every((board) => !labels(board, 'Pending').includes('A5')),
      };
    },
  );

  await checkRow(
    'V7',
    {
      pending: [
        ['Synthetic live dashboard task', 'A8', 'A7', 'A4', 'A2', 'A1'],
        ['Synthetic live dashboard task', 'A8', 'A7', 'A4', 'A2', 'A1'],
      ],
      arrivalMarks: [true, true],
      sameBoard: true,
      storedSession: true,
      liveBoards: 2,
    },
    () => {
      act(server.createTask);
      return {
        pending: boards.map((board) => labels(board, 'Pending')),
        arrivalMarks: boards.map((board) =>
          column(board, 'Pending').querySelector('article')?.classList.contains('task-arrival'),
        ),
        sameBoard: first.querySelector('main') === firstMain,
        storedSession: localStorage.getItem('dashboardToken') === 'synthetic-dashboard-token',
        liveBoards: boards.filter((board) => within(board).queryByText('Live')).length,
      };
    },
  );

  // V8 needs a real outage; V9 needs real 360 px layout. Their proof is the dev-server check.
  await checkRow(
    'V10',
    {
      signedInBeforeTicket: true,
      activeRejected: true,
      activeInstructions: true,
      cardCounts: [0, 0],
      storedSession: null,
      ticketDenied: true,
      closedStreams: true,
    },
    async () => {
      act(server.revoke);
      const signedInBeforeTicket = boards.every(
        (board) =>
          within(board).queryAllByRole('article').length === 8 &&
          within(board).queryByText('Reconnecting…'),
      );
      await waitFor(
        () => {
          expect(within(first).getByText('This link no longer works.')).toBeInTheDocument();
          for (const board of boards)
            expect(within(board).queryAllByRole('article')).toHaveLength(0);
        },
        { timeout: 5_000 },
      );
      return {
        signedInBeforeTicket,
        activeRejected: within(first).queryByText('This link no longer works.') !== null,
        activeInstructions:
          within(first).queryByText('Send /dashboard to the bot for a new link') !== null,
        cardCounts: boards.map((board) => within(board).queryAllByRole('article').length),
        storedSession: localStorage.getItem('dashboardToken'),
        ticketDenied: server.requests.deniedTickets > 0,
        closedStreams: BrowserStream.instances.every((source) => source.closed),
      };
    },
  );
});
