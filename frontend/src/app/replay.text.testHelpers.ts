import { within } from '@testing-library/react';
import type { UserEvent } from '@testing-library/user-event';

import { card, checkRow, fixtureTask } from './replay.testHelpers';

export const checkTextRows = async (first: HTMLElement, user: UserEvent): Promise<void> => {
  await checkRow(
    'V2',
    {
      wholeTitle: true,
      description: false,
      showMore: false,
      sourceOpen: true,
      wholeSource: true,
    },
    async () => {
      const a1 = card(first, 1);
      await user.click(within(a1).getByText('Source message', { selector: 'summary' }));
      return {
        wholeTitle:
          within(a1).getByRole('heading', { level: 3 }).textContent === fixtureTask(1).source_text,
        description: [...a1.querySelectorAll('p[id]')].some(
          (element) => !element.closest('dialog'),
        ),
        showMore: within(a1).queryByRole('button', { name: 'Show more' }) !== null,
        sourceOpen: a1.querySelector('details')?.open,
        wholeSource: a1.querySelector('details p')?.textContent === fixtureTask(1).source_text,
      };
    },
  );

  await checkRow(
    'V3',
    {
      titleCharacters: 118,
      ellipsis: true,
      descriptionCharacters: 2276,
      collapsed: true,
      showMore: true,
      expandedCharacters: 2276,
      expanded: true,
      showLess: true,
      sourceOpen: true,
      wholeSource: true,
    },
    async () => {
      const a8 = card(first, 8);
      const title = within(a8).getByRole('heading', { level: 3 }).textContent;
      const more = within(a8).getByRole('button', { name: 'Show more' });
      const showMore = more.textContent === 'Show more';
      const description = document.getElementById(more.getAttribute('aria-controls') ?? '');
      const collapsed = description?.classList.contains('line-clamp-3');
      const descriptionCharacters = description?.textContent.length;
      await user.click(more);
      const less = within(a8).getByRole('button', { name: 'Show less' });
      await user.click(within(a8).getByText('Source message', { selector: 'summary' }));
      return {
        titleCharacters: title.length,
        ellipsis: title.endsWith('…'),
        descriptionCharacters,
        collapsed,
        showMore,
        expandedCharacters: description?.textContent.length,
        expanded:
          !description?.classList.contains('line-clamp-3') &&
          less.getAttribute('aria-expanded') === 'true',
        showLess: less.textContent === 'Show less',
        sourceOpen: a8.querySelector('details')?.open,
        wholeSource: a8.querySelector('details p')?.textContent === fixtureTask(8).source_text,
      };
    },
  );
};
