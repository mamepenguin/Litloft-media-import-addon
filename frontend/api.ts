const BASE = "/api/addons/media_import";

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
    headers: { "Content-Type": "application/json" },
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
