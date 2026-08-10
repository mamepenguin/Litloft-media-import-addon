const BASE = "/api/addons/media_import";

// Header values are ISO-8859-1 only, but drive names may contain
// non-ASCII (e.g. Japanese). Percent-encode here so the host's
// addon-proxy convention (X-Lit-Drive) round-trips safely.
function driveHeaders(drive: string): Record<string, string> {
  return { "X-Lit-Drive": encodeURIComponent(drive) };
}

export interface LoftCreateResponse {
  file_id: string;
  filename: string;
}

export type SttMode = "always" | "missing_captions" | "manual";

export type CaptionErrorKind = "rate_limited" | "permanent" | null;

export interface LoftMetadata {
  provider: string;
  url: string;
  description: string | null;
  channel: string | null;
  published_at: string | null;
  language: string | null;
  has_captions: boolean;
  captions_downloaded: boolean;
  caption_error_kind: CaptionErrorKind;
  fetched_at: string | null;
  fetch_error: string | null;
}

export async function createLoft(
  url: string,
  drive: string,
  folder_path: string,
  stt_mode: SttMode = "manual",
): Promise<LoftCreateResponse> {
  const res = await fetch(`${BASE}/link`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...driveHeaders(drive) },
    body: JSON.stringify({ url, drive, folder_path, stt_mode }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
  return res.json();
}

export async function generateLoftStt(
  fileId: string,
  drive: string,
): Promise<{ status: "queued" | "already_queued" }> {
  const res = await fetch(`${BASE}/link/${fileId}/stt`, {
    method: "POST",
    credentials: "include",
    headers: driveHeaders(drive),
  });
  return _json<{ status: "queued" | "already_queued" }>(res);
}

export async function getLoftMetadata(
  fileId: string,
): Promise<LoftMetadata | null> {
  const res = await fetch(`${BASE}/link/${fileId}/metadata`, {
    credentials: "include",
  });
  if (!res.ok) return null;
  return res.json();
}

export async function refreshLoft(fileId: string): Promise<void> {
  await fetch(`${BASE}/link/${fileId}/refresh`, {
    method: "POST",
    credentials: "include",
  });
}

// ---- Subscriptions (Phase 2 Commit 5) ----

export type SubscriptionKind =
  | "video"
  | "channel"
  | "playlist"
  | "feed"
  | "unknown";

export interface SubscriptionResolveResponse {
  kind: SubscriptionKind;
  provider: string | null;
  ref: string | null;
}

/**
 * How prominently a subscription's new videos appear in Watch.
 *
 * `library` is the default and stays the default: importing a video
 * has never meant an intent to watch it, so a source only reaches a
 * Watch lane when the user opts it in. Spec
 * `2026-08-10-media-import-watch-surface.md` §1.
 */
export type DisplayMode = "library" | "feed" | "regular";

export const DISPLAY_MODES: readonly DisplayMode[] = [
  "library",
  "feed",
  "regular",
] as const;

export interface Subscription {
  id: number;
  provider: string;
  source_kind: string;
  source_ref: string;
  drive: string;
  folder_path: string;
  title: string | null;
  is_enabled: boolean;
  cooldown_minutes: number;
  include_no_transcript: boolean;
  last_synced_at: string | null;
  cooldown_until: string | null;
  created_at: string;
  // Derived from SubscriptionWorker.running_ids on the server. True
  // while a sync job for this subscription is currently executing.
  running: boolean;
  // Phase 4 additions; nullable on Phase 2/3 installs that haven't
  // backfilled metadata yet.
  avatar_url: string | null;
  display_title: string | null;
  // Never absent: rows predating the column read back as the DDL
  // default ("library").
  display_mode: DisplayMode;
}

export interface SubscriptionPatch {
  is_enabled?: boolean;
  cooldown_minutes?: number;
  include_no_transcript?: boolean;
  folder_path?: string;
  display_title?: string;
  display_mode?: DisplayMode;
}

export interface SubscriptionSummary {
  total: number;
  paused: number;
  syncing: number;
  healthy: number;
  attention: number;
  imported_count: number;
  failed_count: number;
}

export interface SubscriptionRefreshMetadataResult {
  updated: boolean;
  avatar_url: string | null;
  display_title: string | null;
}

export interface ActivityEntry {
  file_id: string;
  filename: string;
  thumbnail_path: string | null;
  channel: string | null;
  published_at: string | null;
  created_at: string;
  source: "single" | "subscription";
  subscription_id: number | null;
  subscription_title: string | null;
}

export type ConflictAction = "skip" | "rename" | "overwrite";

export interface ResolveConflictResult {
  status: "dismissed" | "requeued";
}

export interface SubscriptionVideo {
  subscription_id: number;
  item_id: string;
  status: "pending" | "imported" | "failed";
  error_kind: string | null;
  file_id: string | null;
  first_seen_at: string;
  last_attempted_at: string | null;
  // Display metadata (Phase 5). Server-side JOIN on files +
  // loft_metadata; nullable for items where the .loft was never
  // produced (e.g. permanent failures before allocation).
  title: string | null;
  thumbnail_path: string | null;
  channel: string | null;
  published_at: string | null;
}

export interface SubscriptionEnqueueResult {
  status: "queued" | "already_queued";
}

async function _json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
  return res.json();
}

export async function resolveSubscriptionUrl(
  url: string,
  drive: string,
): Promise<SubscriptionResolveResponse> {
  const res = await fetch(`${BASE}/subscriptions/resolve`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...driveHeaders(drive) },
    body: JSON.stringify({ url }),
  });
  return _json<SubscriptionResolveResponse>(res);
}

export interface SubscriptionCreateInput {
  url: string;
  drive: string;
  folder_path?: string;
  cooldown_minutes?: number;
  include_no_transcript?: boolean;
  display_mode?: DisplayMode;
}

export async function createSubscription(
  input: SubscriptionCreateInput,
): Promise<Subscription> {
  const res = await fetch(`${BASE}/subscriptions`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...driveHeaders(input.drive),
    },
    body: JSON.stringify({
      url: input.url,
      drive: input.drive,
      folder_path: input.folder_path ?? "",
      cooldown_minutes: input.cooldown_minutes ?? 60,
      include_no_transcript: input.include_no_transcript ?? false,
      display_mode: input.display_mode ?? "library",
    }),
  });
  return _json<Subscription>(res);
}

export async function listSubscriptions(
  drive: string,
): Promise<Subscription[]> {
  const res = await fetch(
    `${BASE}/subscriptions?drive=${encodeURIComponent(drive)}`,
    { credentials: "include", headers: driveHeaders(drive) },
  );
  return _json<Subscription[]>(res);
}

export async function deleteSubscription(
  drive: string,
  id: number,
): Promise<void> {
  const res = await fetch(`${BASE}/subscriptions/${id}`, {
    method: "DELETE",
    credentials: "include",
    headers: driveHeaders(drive),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
}

export async function syncSubscription(
  drive: string,
  id: number,
  backfill?: number,
): Promise<SubscriptionEnqueueResult> {
  const qs =
    typeof backfill === "number" ? `?backfill=${backfill}` : "";
  const res = await fetch(`${BASE}/subscriptions/${id}/sync${qs}`, {
    method: "POST",
    credentials: "include",
    headers: driveHeaders(drive),
  });
  return _json<SubscriptionEnqueueResult>(res);
}

export async function extendBackfill(
  drive: string,
  id: number,
  count: number,
): Promise<SubscriptionEnqueueResult> {
  const res = await fetch(`${BASE}/subscriptions/${id}/backfill`, {
    method: "POST",
    credentials: "include",
    headers: { ...driveHeaders(drive), "Content-Type": "application/json" },
    body: JSON.stringify({ count }),
  });
  return _json<SubscriptionEnqueueResult>(res);
}

export async function listSubscriptionVideos(
  drive: string,
  id: number,
): Promise<SubscriptionVideo[]> {
  const res = await fetch(`${BASE}/subscriptions/${id}/videos`, {
    credentials: "include",
    headers: driveHeaders(drive),
  });
  return _json<SubscriptionVideo[]>(res);
}

export async function retrySubscriptionVideo(
  drive: string,
  id: number,
  itemId: string,
): Promise<SubscriptionEnqueueResult> {
  const res = await fetch(
    `${BASE}/subscriptions/${id}/videos/${encodeURIComponent(itemId)}/retry`,
    {
      method: "POST",
      credentials: "include",
      headers: driveHeaders(drive),
    },
  );
  return _json<SubscriptionEnqueueResult>(res);
}

export async function dismissSubscriptionVideo(
  drive: string,
  id: number,
  itemId: string,
): Promise<ResolveConflictResult> {
  const res = await fetch(
    `${BASE}/subscriptions/${id}/videos/${encodeURIComponent(itemId)}/dismiss`,
    {
      method: "POST",
      credentials: "include",
      headers: driveHeaders(drive),
    },
  );
  return _json<ResolveConflictResult>(res);
}

// ---- Phase 4 additions: PATCH / summary / refresh / activity / conflict ----

export async function patchSubscription(
  drive: string,
  id: number,
  patch: SubscriptionPatch,
): Promise<Subscription> {
  const res = await fetch(`${BASE}/subscriptions/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...driveHeaders(drive) },
    body: JSON.stringify(patch),
  });
  return _json<Subscription>(res);
}

export async function getSubscriptionSummary(
  drive: string,
): Promise<SubscriptionSummary> {
  const res = await fetch(
    `${BASE}/subscriptions/summary?drive=${encodeURIComponent(drive)}`,
    { credentials: "include", headers: driveHeaders(drive) },
  );
  return _json<SubscriptionSummary>(res);
}

export async function refreshSubscriptionMetadata(
  drive: string,
  id: number,
): Promise<SubscriptionRefreshMetadataResult> {
  const res = await fetch(
    `${BASE}/subscriptions/${id}/refresh-metadata`,
    {
      method: "POST",
      credentials: "include",
      headers: driveHeaders(drive),
    },
  );
  return _json<SubscriptionRefreshMetadataResult>(res);
}

export function subscriptionAvatarUrl(id: number): string {
  // Endpoint planned for D-3; here so callers can pre-bind the URL.
  return `${BASE}/subscriptions/${id}/avatar`;
}

export async function listActivity(
  drive: string,
  limit = 50,
): Promise<ActivityEntry[]> {
  const res = await fetch(
    `${BASE}/activity?drive=${encodeURIComponent(drive)}&limit=${limit}`,
    { credentials: "include", headers: driveHeaders(drive) },
  );
  return _json<ActivityEntry[]>(res);
}

// ---- Watch surface -------------------------------------------------

/**
 * Which slice of the library to ask for. One lane per request so each
 * paginates independently — they grow at very different rates.
 */
export type WatchLane = "continue" | "regular" | "feed";

export type PlaybackState = "not_started" | "in_progress" | "completed";

export interface WatchPlayback {
  position: number;
  duration: number;
  state: PlaybackState;
}

export interface WatchItem {
  file_id: string;
  filename: string;
  title: string | null;
  thumbnail_path: string | null;
  channel: string | null;
  published_at: string | null;
  created_at: string;
  /** Media length in seconds, from import metadata. */
  duration: number | null;
  /** Provider URL, for "open on YouTube". */
  url: string;
  subscription_id: number | null;
  subscription_title: string | null;
  /**
   * Null when this viewer has no history for the file — and also when
   * reading playback state failed. Either way the item still renders,
   * just without a badge.
   */
  playback: WatchPlayback | null;
}

export const WATCH_PAGE_SIZE = 24;

/**
 * Fetch one bounded page of one Watch lane.
 *
 * There is deliberately no total: Watch is a lens over the library,
 * not an inbox, and must never show a backlog count. A full page back
 * is the only "there may be more" signal.
 */
export async function listWatch(
  drive: string,
  lane: WatchLane,
  { limit = WATCH_PAGE_SIZE, offset = 0 } = {},
): Promise<WatchItem[]> {
  const params = new URLSearchParams({
    lane,
    drive,
    limit: String(limit),
    offset: String(offset),
  });
  const res = await fetch(`${BASE}/watch?${params}`, {
    credentials: "include",
    headers: driveHeaders(drive),
  });
  return _json<WatchItem[]>(res);
}

export async function resolveConflict(
  drive: string,
  id: number,
  itemId: string,
  action: ConflictAction,
): Promise<ResolveConflictResult> {
  const res = await fetch(
    `${BASE}/subscriptions/${id}/videos/${encodeURIComponent(itemId)}/resolve-conflict`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...driveHeaders(drive) },
      body: JSON.stringify({ action }),
    },
  );
  return _json<ResolveConflictResult>(res);
}
