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
  | "unknown";

const RETRYABLE: Record<ErrorKind, boolean> = {
  rate_limited: true,
  permanent: false,
  no_transcript: true,
  path_conflict: true,
  dismissed: false,
  fetch_failed: true,
  unknown: true,
};

export function normalizeErrorKind(raw: string | null): ErrorKind | null {
  if (!raw) return null;
  if (raw in RETRYABLE) return raw as ErrorKind;
  return "unknown";
}

export function isRetryable(raw: string | null): boolean {
  const kind = normalizeErrorKind(raw);
  if (!kind) return false;
  return RETRYABLE[kind];
}
