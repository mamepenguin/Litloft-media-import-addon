"use client";

// Side-effect: ensure media_import's player registrations are evaluated
// when this addon's standalone page is loaded directly.
import "./players/registerMediaImportPlayers";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ChevronRight, FolderIcon, Link as LinkIcon } from "lucide-react";
import { getFolders } from "@/lib/api";
import type { Folder } from "@/types";
import {
  createLoft,
  createSubscription,
  resolveSubscriptionUrl,
  syncSubscription,
  type SubscriptionKind,
} from "./api";
import SubscriptionsList from "./SubscriptionsList";

interface PendingItem {
  url: string;
  filename: string;
  fileId: string;
  createdAt: number;
}

function extractUrl(e: React.DragEvent): string | null {
  const uri = e.dataTransfer.getData("text/uri-list");
  if (uri) {
    const first = uri.split("\n").find((l) => l.trim() && !l.startsWith("#"));
    if (first) return first.trim();
  }
  const text = e.dataTransfer.getData("text/plain");
  if (text) {
    const trimmed = text.trim().split("\n")[0].trim();
    if (/^https?:\/\/.+/.test(trimmed)) return trimmed;
  }
  return null;
}

function isSubscriptionKind(kind: SubscriptionKind): boolean {
  return kind === "channel" || kind === "playlist" || kind === "feed";
}

export default function MediaImportPage() {
  const router = useRouter();
  const params = useParams();
  // The addon is scope=drive and is mounted under
  // /drive/{name}/addons/media_import — the URL is the single source
  // of truth for which drive we operate on. We never let the user pick
  // another drive from this page (would cross-pollinate drive
  // boundaries and bypass per-drive access control).
  const currentDrive = decodeURIComponent((params?.name as string) ?? "");

  const [url, setUrl] = useState("");
  const [folders, setFolders] = useState<Folder[]>([]);
  const [selectedFolder, setSelectedFolder] = useState("");
  const [folderStack, setFolderStack] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<PendingItem[]>([]);
  const [subsListVersion, setSubsListVersion] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // URL classification state.
  const [kind, setKind] = useState<SubscriptionKind>("unknown");
  const [resolving, setResolving] = useState(false);
  const [backfill, setBackfill] = useState<number>(15);
  const [includeNoTranscript, setIncludeNoTranscript] = useState(false);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!currentDrive) return;
    const currentPath =
      folderStack.length > 0 ? folderStack[folderStack.length - 1] : undefined;
    getFolders(currentDrive, currentPath).then(setFolders);
  }, [currentDrive, folderStack]);

  // Debounced URL classification: when the URL settles, ask backend
  // whether it points at a subscription source so we can swap UI modes.
  useEffect(() => {
    const trimmed = url.trim();
    if (!trimmed) {
      setKind("unknown");
      return;
    }
    if (!currentDrive) return;
    setResolving(true);
    const handle = setTimeout(() => {
      resolveSubscriptionUrl(trimmed, currentDrive)
        .then((res) => setKind(res.kind))
        .catch(() => setKind("unknown"))
        .finally(() => setResolving(false));
    }, 400);
    return () => clearTimeout(handle);
  }, [url, currentDrive]);

  function handleFolderClick(folder: Folder) {
    setFolderStack((prev) => [...prev, folder.path]);
    setSelectedFolder(folder.path);
  }

  function handleBreadcrumbClick(index: number) {
    if (index < 0) {
      setFolderStack([]);
      setSelectedFolder("");
    } else {
      const next = folderStack.slice(0, index + 1);
      setFolderStack(next);
      setSelectedFolder(next[next.length - 1]);
    }
  }

  async function handleSubmit() {
    const trimmed = url.trim();
    if (!trimmed || !currentDrive || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (isSubscriptionKind(kind)) {
        const sub = await createSubscription({
          url: trimmed,
          drive: currentDrive,
          folder_path: selectedFolder,
          include_no_transcript: includeNoTranscript,
        });
        // Fire-and-forget: enqueue the initial backfill and let the
        // worker stream progress via WS. The form unblocks immediately
        // — completion will surface in SubscriptionsList via the
        // ``subscription.sync_completed`` event.
        await syncSubscription(currentDrive, sub.id, backfill);
        setSubsListVersion((v) => v + 1);
      } else {
        const result = await createLoft(
          trimmed, currentDrive, selectedFolder,
        );
        const next: PendingItem = {
          url: trimmed,
          filename: result.filename,
          fileId: result.file_id,
          createdAt: Date.now(),
        };
        setRecent((prev) => [next, ...prev].slice(0, 20));
      }
      setUrl("");
      inputRef.current?.focus();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to import");
    } finally {
      setSubmitting(false);
    }
  }

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    setDragOver(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const dropped = extractUrl(e);
      if (dropped) {
        setUrl(dropped);
        inputRef.current?.focus();
      }
    },
    [],
  );

  const breadcrumbParts = folderStack.map((p) => {
    const parts = p.split("/");
    return parts[parts.length - 1];
  });

  const showSubscriptionFields = isSubscriptionKind(kind);
  const submitLabel = submitting
    ? "..."
    : showSubscriptionFields
      ? "Subscribe"
      : "Import";

  return (
    <div
      className="relative mx-auto max-w-3xl p-6"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-xl border-2 border-dashed border-accent-cta bg-accent-cta/10">
          <div className="flex flex-col items-center gap-2 text-accent-cta">
            <LinkIcon size={48} />
            <span className="text-lg font-medium">Drop URL to import</span>
          </div>
        </div>
      )}

      <h1 className="mb-6 text-2xl font-bold text-text-primary">Media Import</h1>

      <div className="space-y-5">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-text-secondary">URL</label>
          <input
            ref={inputRef}
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://..."
            className="w-full rounded-lg border border-border-primary bg-bg-primary px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-accent-cta focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
          {url.trim() && (
            <p className="mt-1 text-xs text-text-muted" data-testid="url-kind-hint">
              {resolving
                ? "Detecting URL type..."
                : kind === "video"
                  ? "Single video — will create a single .loft"
                  : kind === "channel"
                    ? "YouTube channel — subscription will track new uploads"
                    : kind === "playlist"
                      ? "YouTube playlist — subscription will track all items"
                      : kind === "feed"
                        ? "Feed — subscription"
                        : "Single import (unrecognized provider — will pass through)"}
            </p>
          )}
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-text-secondary">Save to</label>

          <div className="mb-2 flex items-center gap-1 text-sm text-text-secondary">
            <button
              onClick={() => handleBreadcrumbClick(-1)}
              className="hover:text-text-primary"
              data-testid="breadcrumb-drive"
            >
              {currentDrive || "..."}
            </button>
            {breadcrumbParts.map((name, i) => (
              <span key={i} className="flex items-center gap-1">
                <ChevronRight size={14} className="text-text-muted" />
                <button
                  onClick={() => handleBreadcrumbClick(i)}
                  className="hover:text-text-primary"
                >
                  {name}
                </button>
              </span>
            ))}
          </div>

          <div className="max-h-48 overflow-y-auto rounded-lg border border-border-primary bg-bg-primary">
            {folders.length === 0 ? (
              <div className="px-3 py-3 text-sm text-text-muted">No subfolders</div>
            ) : (
              folders.map((f) => (
                <button
                  key={f.path}
                  onClick={() => handleFolderClick(f)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-hover"
                >
                  <FolderIcon size={16} className="shrink-0 text-text-muted" />
                  {f.name}
                </button>
              ))
            )}
          </div>
        </div>

        {showSubscriptionFields && (
          <div
            className="space-y-3 rounded-lg border border-border-primary bg-bg-card p-4"
            data-testid="subscription-fields"
          >
            <div>
              <label className="mb-1.5 block text-sm font-medium text-text-secondary">
                Backfill (initial import count)
              </label>
              <input
                type="number"
                min={1}
                value={backfill}
                onChange={(e) => setBackfill(Math.max(1, Number(e.target.value) || 1))}
                className="w-full rounded-lg border border-border-primary bg-bg-primary px-3 py-2 text-sm text-text-primary focus:border-accent-cta focus:outline-none"
              />
              <p className="mt-1 text-xs text-text-muted">
                {kind === "channel"
                  ? "Most-recent N uploads. RSS handles ≤15 cheaply; larger values fall back to yt-dlp."
                  : "Items from the start of the playlist."}
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm text-text-primary">
              <input
                type="checkbox"
                checked={includeNoTranscript}
                onChange={(e) => setIncludeNoTranscript(e.target.checked)}
              />
              Try to fetch transcripts even when the video reports none
            </label>
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end">
          <button
            onClick={handleSubmit}
            disabled={!url.trim() || !currentDrive || submitting}
            className="rounded-lg bg-accent-cta px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50 hover:opacity-90"
          >
            {submitLabel}
          </button>
        </div>
      </div>

      {recent.length > 0 && (
        <div className="mt-8">
          <h2 className="mb-3 text-sm font-medium text-text-secondary">
            Recent imports
          </h2>
          <ul className="space-y-2">
            {recent.map((item) => (
              <li
                key={item.fileId}
                className="rounded-lg border border-border-primary bg-bg-card px-3 py-2"
              >
                <button
                  onClick={() => router.push(`/files/${item.fileId}`)}
                  className="flex w-full flex-col items-start gap-0.5 text-left"
                >
                  <span className="text-sm font-medium text-text-primary">
                    {item.filename}
                  </span>
                  <span className="truncate text-xs text-text-muted">
                    {item.url}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {currentDrive && (
        <div className="mt-10">
          <h2 className="mb-3 text-sm font-medium text-text-secondary">
            Subscriptions on {currentDrive}
          </h2>
          <SubscriptionsList key={`${currentDrive}-${subsListVersion}`} drive={currentDrive} />
        </div>
      )}

    </div>
  );
}
