"use client";

/**
 * CaptionStatusBadge — surfaces YouTube caption-download state on a Loft.
 *
 * Spec: docs/superpowers/specs/2026-04-26-loft-caption-state-visibility.md
 *
 * Eval order matters and is intentional:
 *   1. captions_downloaded === true       → render nothing (success is silent)
 *   2. fetched_at === null                → "not attempted yet"
 *   3. has_captions === false             → "YouTube has no captions"
 *   4. caption_error_kind === 'permanent' → permanent failure (danger)
 *   5. caption_error_kind === 'rate_limited' → temporary failure (warning)
 *   6. otherwise (has_captions=true, kind=null, downloaded=false) → generic
 *      failure (warning)
 */

import { useTranslations } from "next-intl";
import { Subtitles, RotateCw, XCircle, Info } from "lucide-react";
import type { ReactNode } from "react";
import type { LoftMetadata } from "./api";

type Tone = "neutral" | "warning" | "danger";

interface BadgeView {
  tone: Tone;
  icon: ReactNode;
  label: string;
  retryable: boolean;
}

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-bg-elevated text-text-muted",
  warning: "bg-accent-amber/10 text-accent-amber",
  danger: "bg-danger/10 text-danger",
};

const RETRYABLE_HOVER: Record<Tone, string> = {
  neutral: "hover:bg-bg-card",
  warning: "hover:bg-accent-amber/15",
  danger: "hover:bg-danger/15",
};

function pickView(
  metadata: LoftMetadata,
  t: (key: string) => string,
): BadgeView | null {
  if (metadata.captions_downloaded) return null;

  if (metadata.fetched_at === null) {
    return {
      tone: "neutral",
      icon: <Info size={14} aria-hidden="true" />,
      label: t("notAttempted"),
      retryable: false,
    };
  }

  if (!metadata.has_captions) {
    return {
      tone: "neutral",
      icon: <Subtitles size={14} aria-hidden="true" />,
      label: t("noCaptions"),
      retryable: false,
    };
  }

  if (metadata.caption_error_kind === "permanent") {
    return {
      tone: "danger",
      icon: <XCircle size={14} aria-hidden="true" />,
      label: t("permanent"),
      retryable: false,
    };
  }

  if (metadata.caption_error_kind === "rate_limited") {
    return {
      tone: "warning",
      icon: <RotateCw size={14} aria-hidden="true" />,
      label: t("rateLimited"),
      retryable: true,
    };
  }

  return {
    tone: "warning",
    icon: <RotateCw size={14} aria-hidden="true" />,
    label: t("failed"),
    retryable: true,
  };
}

const BASE_CLASSES =
  "mt-2 inline-flex items-center gap-1.5 rounded-2xl px-3 py-1.5 text-xs";

export default function CaptionStatusBadge({
  metadata,
  onRetry,
  isRetrying = false,
}: {
  metadata: LoftMetadata;
  onRetry?: () => void;
  isRetrying?: boolean;
}) {
  const t = useTranslations("captionStatus");
  const view = pickView(metadata, t);
  if (view === null) return null;

  const canRetry = view.retryable && typeof onRetry === "function";

  if (canRetry) {
    const label = isRetrying ? t("retrying") : view.label;
    const icon = isRetrying ? (
      <RotateCw size={14} className="animate-spin" aria-hidden="true" />
    ) : (
      view.icon
    );
    return (
      <button
        type="button"
        onClick={onRetry}
        disabled={isRetrying}
        title={t("retryHint")}
        aria-label={`${view.label} — ${t("retryHint")}`}
        className={`${BASE_CLASSES} ${TONE_CLASSES[view.tone]} ${RETRYABLE_HOVER[view.tone]} cursor-pointer transition-colors disabled:cursor-wait disabled:opacity-70`}
      >
        {icon}
        <span>{label}</span>
      </button>
    );
  }

  return (
    <div
      role="status"
      className={`${BASE_CLASSES} ${TONE_CLASSES[view.tone]}`}
    >
      {view.icon}
      <span>{view.label}</span>
    </div>
  );
}
