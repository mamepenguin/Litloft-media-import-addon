"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";

import { Button } from "@/components/Button";
import { useDialogPortalTarget } from "@/components/DialogPortal";
import { FolderPicker } from "@/components/FolderPicker";
import { useImeKeyGuard } from "@/lib/ime";
import { useShortcuts } from "@/hooks/useShortcuts";
import { OVERLAY_PRIORITY } from "@/lib/shortcuts";

import { createLoft, resolveSubscriptionUrl, type SubscriptionKind } from "./api";
import { isSubscriptionKind } from "./lib/subscriptionKind";
import { readStoredSttMode } from "./lib/sttMode";

interface Props {
  drive: string;
  path: string;
  onCancel: () => void;
  onImported: () => void;
}

/**
 * The destination starts at the Add menu's `path` and nowhere else. The
 * Manage composer's per-provider folder memory is deliberately not read:
 * the Add menu is opened from a place that names its destination, and the
 * drive root (`""`) is indistinguishable from "nothing chosen yet" there.
 */
export default function ImportFromUrlDialog({ drive, path, onCancel, onImported }: Props) {
  const t = useTranslations("mediaImport.importFromUrl");
  const tc = useTranslations("common");
  const host = useDialogPortalTarget();
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState(path);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsSubscription, setNeedsSubscription] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const ime = useImeKeyGuard();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useShortcuts(
    "media-import-url-dialog",
    "Dialog",
    [
      {
        key: "escape",
        label: "Cancel",
        editingOnly: false,
        hidden: true,
        handler: onCancel,
      },
    ],
    true,
    OVERLAY_PRIORITY,
  );

  async function handleSubmit() {
    const trimmed = url.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    setError(null);
    setNeedsSubscription(false);
    try {
      // Resolved at submit rather than from a debounced preview, so a
      // channel pasted and submitted at once cannot reach `createLoft`.
      const kind: SubscriptionKind = await resolveSubscriptionUrl(trimmed, drive)
        .then((res) => res.kind)
        .catch(() => "unknown" as const);
      if (isSubscriptionKind(kind)) {
        setNeedsSubscription(true);
        return;
      }
      await createLoft(trimmed, drive, folder, readStoredSttMode());
      onImported();
    } catch (e) {
      setError(e instanceof Error && e.message ? e.message : t("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!host) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center"
      role="dialog"
      aria-modal
      aria-label={t("title")}
      data-testid="import-from-url-dialog"
    >
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm animate-fade-in"
        onClick={onCancel}
      />
      <div className="relative mx-4 w-full max-w-md animate-fade-in-scale">
        <div className="space-y-4 rounded-xl border border-bg-border bg-bg-card p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-primary">{t("title")}</h2>
            <button
              type="button"
              onClick={onCancel}
              className="text-text-muted transition-colors hover:text-text-primary"
              aria-label={tc("close")}
            >
              <X size={16} />
            </button>
          </div>

          <div>
            <label
              htmlFor="media-import-url-input"
              className="mb-1.5 block text-xs text-text-muted"
            >
              {t("url")}
            </label>
            <input
              id="media-import-url-input"
              ref={inputRef}
              type="text"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setNeedsSubscription(false);
              }}
              onCompositionEnd={ime.onCompositionEnd}
              onKeyDown={(e) => {
                if (ime.isImeKeystroke(e)) return;
                if (e.key === "Enter") {
                  e.preventDefault();
                  void handleSubmit();
                }
              }}
              placeholder="https://..."
              className="w-full rounded-2xl border border-bg-border bg-bg-primary px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-focus-ring focus:outline-none"
            />
          </div>

          <div>
            <span className="mb-1.5 block text-xs text-text-muted">{t("folder")}</span>
            <FolderPicker drive={drive} value={folder} onChange={setFolder} />
          </div>

          {needsSubscription && (
            <div
              role="alert"
              className="space-y-1 rounded-2xl bg-bg-elevated px-3 py-2 text-sm text-text-primary"
            >
              <p>{t("subscriptionUrl")}</p>
              <Link
                href={`/drive/${encodeURIComponent(drive)}/addons/media_import`}
                className="text-accent hover:underline"
              >
                {t("openManage")}
              </Link>
            </div>
          )}

          {error && (
            <div role="alert" className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger">
              {error}
            </div>
          )}

          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              disabled={submitting}
              className="rounded-2xl px-4 py-2 text-sm text-text-muted transition-colors hover:text-text-primary disabled:opacity-50"
            >
              {tc("cancel")}
            </button>
            <Button
              variant="primary"
              onClick={() => void handleSubmit()}
              disabled={!url.trim() || submitting}
            >
              {submitting ? t("submitting") : t("submit")}
            </Button>
          </div>
        </div>
      </div>
    </div>,
    host,
  );
}
