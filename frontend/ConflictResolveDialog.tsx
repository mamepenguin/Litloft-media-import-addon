"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { resolveConflict, type ConflictAction } from "./api";

interface Props {
  drive: string;
  subscriptionId: number;
  itemId: string;
  onClose: () => void;
  onResolved: () => void;
}

const ACTIONS: ConflictAction[] = ["rename", "overwrite", "skip"];

export default function ConflictResolveDialog({
  drive,
  subscriptionId,
  itemId,
  onClose,
  onResolved,
}: Props) {
  const t = useTranslations("mediaImport.conflict");
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
      setError(e instanceof Error ? e.message : t("errorFallback"));
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
        className="w-full max-w-md rounded-2xl border border-bg-border bg-bg-card p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-text-primary">
          {t("title")}
        </h3>
        <p className="mt-1 text-sm text-text-muted">{t("description")}</p>

        <ul className="mt-4 space-y-2">
          {ACTIONS.map((a) => (
            <li key={a}>
              <button
                type="button"
                onClick={() => handle(a)}
                disabled={pending !== null}
                className="flex w-full flex-col gap-1 rounded-xl border border-bg-border px-4 py-3 text-left transition-colors hover:bg-bg-elevated disabled:opacity-50"
                data-testid={`conflict-action-${a}`}
              >
                <span className="text-sm font-medium text-text-primary">
                  {t(`${a}.label`)}
                  {pending === a ? "..." : ""}
                </span>
                <span className="text-xs text-text-muted">
                  {t(`${a}.hint`)}
                </span>
              </button>
            </li>
          ))}
        </ul>

        {error && (
          <div
            className="mt-3 rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger"
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
            {t("cancel")}
          </button>
        </div>
      </div>
    </div>
  );
}
