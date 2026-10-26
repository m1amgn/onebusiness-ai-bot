type OwnProps = { direction: 'left' | 'right' };

export const ChevronIcon = ({ direction }: OwnProps) => (
  <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" className="size-3.5 shrink-0">
    <path
      d={direction === 'left' ? 'm10 4-4 4 4 4' : 'm6 4 4 4-4 4'}
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
