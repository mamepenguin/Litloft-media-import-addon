/**
 * ``error_kind`` 列挙値を i18n key と retryable フラグへマップする辞書。
 *
 * 文言は ``frontend/src/messages/{ja,en}.json`` の
 * ``mediaImport.errorKind.*`` に置き、本モジュールは表示の有無 /
 * retry ボタンの可否などロジック側の振る舞いだけを担う。
 */

export type ErrorKind =
  | "rate_limited"
  | "permanent"
  | "no_transcript"
  | "path_conflict"
  | "dismissed"
  | "fetch_failed"
  | "retry_pending"
  | "upcoming"
  | "unknown";

const RETRYABLE: Record<ErrorKind, boolean> = {
  rate_limited: true,
  permanent: true,
  no_transcript: true,
  path_conflict: true,
  dismissed: false,
  fetch_failed: true,
  retry_pending: true,
  upcoming: true,
  unknown: true,
};

const NOT_NEEDING_ATTENTION: ReadonlySet<string> = new Set([
  "retry_pending",
  "upcoming",
  "permanent",
  "dismissed",
]);

export function normalizeErrorKind(raw: string | null): ErrorKind | null {
  if (!raw) return null;
  if (raw in RETRYABLE) return raw as ErrorKind;
  return "unknown";
}

export function isRetryable(raw: string | null): boolean {
  const kind = normalizeErrorKind(raw);
  // null = cause unknown (legacy rows before fetch_failed was introduced).
  // Treat as retryable; only dismissed should suppress the button.
  if (!kind) return true;
  return RETRYABLE[kind];
}

/** A failed row the user has to act on; retried, unavailable and skipped
 * rows are not. */
export function needsAttention(video: {
  status: string;
  error_kind: string | null;
}): boolean {
  return (
    video.status === "failed" &&
    !NOT_NEEDING_ATTENTION.has(video.error_kind ?? "")
  );
}
