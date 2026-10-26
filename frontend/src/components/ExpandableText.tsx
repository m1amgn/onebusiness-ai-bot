import { useId, useLayoutEffect, useRef, useState } from 'react';

import { cn } from '../lib/cn';

type OwnProps = { text: string };

export const ExpandableText = ({ text }: OwnProps) => {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const content = useRef<HTMLParagraphElement>(null);
  const contentId = useId();
  useLayoutEffect(() => {
    const element = content.current;
    if (!element) return;
    let active = true;
    const handleMeasureCollapsedOverflow = (): void => {
      element.classList.add('line-clamp-3');
      const overflows = element.scrollHeight > element.clientHeight;
      if (expanded) element.classList.remove('line-clamp-3');
      setOverflowing(overflows);
    };
    handleMeasureCollapsedOverflow();
    const observer = new ResizeObserver(handleMeasureCollapsedOverflow);
    observer.observe(element);
    if ('fonts' in document) {
      void document.fonts.ready.then(() => {
        if (active) handleMeasureCollapsedOverflow();
      });
    }
    return () => {
      active = false;
      observer.disconnect();
    };
  }, [text, expanded]);
  const handleToggle = (): void => {
    setExpanded((previous) => !previous);
  };
  return (
    <div>
      <p
        ref={content}
        id={contentId}
        className={cn(
          'text-sm leading-6 break-words whitespace-pre-wrap text-sand-11',
          !expanded && 'line-clamp-3',
        )}
      >
        {text}
      </p>
      {overflowing ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={contentId}
          onClick={handleToggle}
          className="mt-1 min-h-8 text-xs font-medium text-sand-12 underline decoration-sand-8 underline-offset-4 hover:decoration-sand-12"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      ) : null}
    </div>
  );
};
