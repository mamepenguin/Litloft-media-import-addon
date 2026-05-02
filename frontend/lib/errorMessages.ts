/**
 * Translate ``error_kind`` constants from the backend into
 * user-facing text. The backend stores raw enum values (rate_limited,
 * permanent, no_transcript, path_conflict, dismissed); this module
 * is the single place the UI consults to render them.
 *
 * Per ``.claude/rules/design-decisions.md``, error messages must
 * state the cause AND the user's recovery path. Each entry returns
 * both ``label`` (short status pill) and ``hint`` (one-line
 * explanation). The retry button's affordance is decided here too:
 * permanent / dismissed are terminal, others retryable.
 */

export interface ErrorMessage {
  label: string;
  hint: string;
  /** When false, suppress the retry button. */
  retryable: boolean;
}

const FALLBACK: ErrorMessage = {
  label: "Failed",
  hint: "Import failed. Try retrying — the cause was not recorded.",
  retryable: true,
};

const MESSAGES: Record<string, ErrorMessage> = {
  rate_limited: {
    label: "Rate-limited",
    hint:
      "YouTube returned a rate-limit response. The next sync will retry " +
      "automatically; you can also retry manually.",
    retryable: true,
  },
  permanent: {
    label: "Unavailable",
    hint:
      "The video is private, removed, or geo-blocked. It will not be " +
      "retried automatically.",
    retryable: false,
  },
  no_transcript: {
    label: "No transcript",
    hint:
      "This video has no transcript on YouTube. The .loft file was " +
      "still imported — only captions are missing.",
    retryable: true,
  },
  path_conflict: {
    label: "Path conflict",
    hint:
      "A file with the same name already exists at the destination. " +
      "Choose how to resolve it.",
    retryable: true,
  },
  dismissed: {
    label: "Skipped",
    hint:
      "You chose to skip this item. It will not be retried unless " +
      "you re-enable it.",
    retryable: false,
  },
};

export function describeError(errorKind: string | null): ErrorMessage | null {
  if (!errorKind) return null;
  return MESSAGES[errorKind] ?? FALLBACK;
}

export function isRetryable(errorKind: string | null): boolean {
  if (!errorKind) return false;
  return (MESSAGES[errorKind] ?? FALLBACK).retryable;
}
