import { useEffect, useId, useRef } from 'react';
import type { SyntheticEvent } from 'react';

type OwnProps = { open: boolean; title: string; onConfirm: () => void; onCancel: () => void };

export const ConfirmDialog = ({ open, title, onConfirm, onCancel }: OwnProps) => {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const warningId = useId();
  useEffect(() => {
    if (open && !dialog.current?.open) {
      dialog.current?.showModal();
      cancelButton.current?.focus();
    } else if (!open && dialog.current?.open) {
      dialog.current.close();
    }
  }, [open]);
  const handleCancel = (event: SyntheticEvent<HTMLDialogElement>): void => {
    event.preventDefault();
    onCancel();
  };
  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      aria-describedby={warningId}
      onCancel={handleCancel}
      className="m-auto w-[calc(100%_-_2rem)] max-w-md rounded-xl border border-sand-7 bg-sand-3 p-6 text-sand-12"
    >
      <h2 id={titleId} className="text-lg font-semibold">
        Delete this task?
      </h2>
      <p className="mt-4 font-medium break-words">{title}</p>
      <p id={warningId} className="mt-3 text-sm leading-6 text-sand-11">
        This cannot be undone. The message stays in your Telegram chat.
      </p>
      <div className="mt-6 flex justify-end gap-3">
        <button
          ref={cancelButton}
          type="button"
          onClick={onCancel}
          className="min-h-11 rounded-md border border-sand-7 px-4 text-sm font-medium hover:bg-sand-4"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          className="min-h-11 rounded-md border border-danger-9 bg-danger-3 px-4 text-sm font-medium text-danger-11 hover:bg-sand-4"
        >
          Delete
        </button>
      </div>
    </dialog>
  );
};
