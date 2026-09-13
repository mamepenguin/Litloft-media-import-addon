import type { SubscriptionKind } from "../api";

export function isSubscriptionKind(kind: SubscriptionKind): boolean {
  return kind === "channel" || kind === "playlist" || kind === "feed";
}
