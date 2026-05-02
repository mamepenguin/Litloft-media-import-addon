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
): Promise<LoftCreateResponse> {
  const res = await fetch(`${BASE}/link`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...driveHeaders(drive) },
    body: JSON.stringify({ url, drive, folder_path }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new Error(data?.detail ?? `Error: ${res.status}`);
  }
  return res.json();
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
}

export interface SubscriptionVideo {
  subscription_id: number;
  item_id: string;
  status: "pending" | "imported" | "failed";
  error_kind: string | null;
  file_id: string | null;
  first_seen_at: string;
  last_attempted_at: string | null;
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
