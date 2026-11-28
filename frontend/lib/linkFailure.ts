import { CreateLoftError } from "../api";

export type NamedLinkErrorKey = "unsupportedUrl" | "unavailable";

/**
 * The message a form shows for a failed `createLoft`: the reason the server
 * named, or the form's own fallback for `failed` and anything else.
 */
export function linkFailureMessage(
  error: unknown,
  named: (key: NamedLinkErrorKey) => string,
  fallback: string,
): string {
  if (error instanceof CreateLoftError) {
    if (error.code === "unsupported_url") return named("unsupportedUrl");
    if (error.code === "unavailable") return named("unavailable");
  }
  return fallback;
}
