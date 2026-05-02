"use client";

import { useState } from "react";

import { resolveConflict, type ConflictAction } from "./api";

interface Props {
  drive: string;
  subscriptionId: number;
  itemId: string;
  onClose: () => void;
  onResolved: () => void;
}

const ACTIONS: { value: ConflictAction; label: string; hint: string }[] = [
  {
    value: "rename",
    label: "Rename",
    hint:
      "Add a (1) suffix to the new file so both copies coexist. Recommended.",
  },
  {
    value: "overwrite",
    label: "Overwrite",
    hint:
      "Accept overwriting if the conflict re-occurs. Today this behaves the same as Rename.",
  },
  {
    value: "skip",
    label: "Skip",
    hint:
      "Stop trying. The item is marked dismissed and will not be retried unless you re-enable it.",
  },
];

export default function ConflictResolveDialog({
  drive,
  subscriptionId,
  itemId,
  onClose,
  onResolved,
}: Props) {
  const [pending, setPending] = useState<ConflictAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handle(action: ConflictAction) {
    setPending(action);
    setError(null);
    try {
      await resolveConflict(drive, subscriptionId, itemId, action);
      onResolved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to resolve");
    } finally {
      setPending(null);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      data-testid="conflict-dialog"
    >
      <div
        className="w-full max-w-md rounded-xl bg-bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-text-primary">
          Resolve path conflict
        </h3>
        <p className="mt-1 text-sm text-text-secondary">
          A file with the same name already exists at the destination.
          Choose how to proceed.
        </p>

        <ul className="mt-4 space-y-2">
          {ACTIONS.map((a) => (
            <li key={a.value}>
              <button
                type="button"
                onClick={() => handle(a.value)}
                disabled={pending !== null}
                className="flex w-full flex-col gap-1 rounded-lg border border-border-primary px-3 py-2.5 text-left hover:bg-bg-hover disabled:opacity-50"
                data-testid={`conflict-action-${a.value}`}
              >
                <span className="text-sm font-medium text-text-primary">
                  {a.label}{pending === a.value ? "..." : ""}
                </span>
                <span className="text-xs text-text-muted">{a.hint}</span>
              </button>
            </li>
          ))}
        </ul>

        {error && (
          <div
            className="mt-3 rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger"
            data-testid="conflict-error"
          >
            {error}
          </div>
        )}

        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-text-muted hover:text-text-primary"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
