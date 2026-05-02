"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  FolderIcon,
  Link as LinkIcon,
  Plus,
} from "lucide-react";

import { getFolders } from "@/lib/api";
import type { Folder } from "@/types";

import {
  createLoft,
  createSubscription,
  resolveSubscriptionUrl,
  syncSubscription,
  type SubscriptionKind,
} from "./api";
import {
  getLastFolder,
  rememberFolder,
} from "./lib/smartFolderMemory";

/**
 * URL composer for Media Import.
 *
 * Sits at the top of the addon page. When ``initialExpanded`` is false
 * it collapses to a one-line "Add a source" affordance and only opens
 * on focus / drag / button click — keeping the dashboard the visual
 * primary on installs that already have subscriptions.
 *
 * Smart-folder default: the last folder used for a given
 * (drive, provider, kind) tuple is restored from localStorage, so the
 * second YouTube channel a user pastes lands in the same destination
 * by default. The user can still change it for that submission.
 */

interface Props {
  drive: string;
  initialExpanded: boolean;
  onCreated: () => void;
}

interface PendingItem {
  url: string;
  filename: string;
  fileId: string;
  createdAt: number;
}

function isSubscriptionKind(kind: SubscriptionKind): boolean {
  return kind === "channel" || kind === "playlist" || kind === "feed";
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

export default function Composer({
  drive,
  initialExpanded,
  onCreated,
}: Props) {
  const [expanded, setExpanded] = useState(initialExpanded);
  const [url, setUrl] = useState("");
  const [kind, setKind] = useState<SubscriptionKind>("unknown");
  const [provider, setProvider] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderStack, setFolderStack] = useState<string[]>([]);
  const [selectedFolder, setSelectedFolder] = useState("");
  const [folderPickerOpen, setFolderPickerOpen] = useState(false);

  const [advanced, setAdvanced] = useState(false);
  const [backfill, setBackfill] = useState(15);
  const [includeNoTranscript, setIncludeNoTranscript] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<PendingItem[]>([]);
  const [dragOver, setDragOver] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);

  // Keep folder list in sync with the current breadcrumb.
  useEffect(() => {
    if (!drive) return;
    const path = folderStack.length ? folderStack[folderStack.length - 1] : undefined;
    getFolders(drive, path).then(setFolders);
  }, [drive, folderStack]);

  // Debounced classification: as the user pastes, ask the backend
  // whether this URL points at a subscription source.
  useEffect(() => {
    const trimmed = url.trim();
    if (!trimmed) {
      setKind("unknown");
      setProvider(null);
      return;
    }
    if (!drive) return;
    setResolving(true);
    const handle = setTimeout(() => {
      resolveSubscriptionUrl(trimmed, drive)
        .then((res) => {
          setKind(res.kind);
          setProvider(res.provider);
          // Apply smart-folder default whenever the provider/kind
          // changes — but only when the user hasn't already typed
          // a folder for this submission.
          if (
            res.provider
            && (res.kind === "channel" || res.kind === "playlist" || res.kind === "video")
            && selectedFolder === ""
            && folderStack.length === 0
          ) {
            const last = getLastFolder(drive, res.provider, res.kind);
            if (last) {
              setSelectedFolder(last);
              setFolderStack(last ? [last] : []);
            }
          }
        })
        .catch(() => {
          setKind("unknown");
          setProvider(null);
        })
        .finally(() => setResolving(false));
    }, 400);
    return () => clearTimeout(handle);
    // selectedFolder / folderStack intentionally omitted: smart default
    // should fire on URL change, not when the user picks a folder.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, drive]);

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
    if (!trimmed || !drive || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (isSubscriptionKind(kind)) {
        const sub = await createSubscription({
          url: trimmed,
          drive,
          folder_path: selectedFolder,
          include_no_transcript: includeNoTranscript,
        });
        await syncSubscription(drive, sub.id, backfill);
      } else {
        const result = await createLoft(trimmed, drive, selectedFolder);
        const next: PendingItem = {
          url: trimmed,
          filename: result.filename,
          fileId: result.file_id,
          createdAt: Date.now(),
        };
        setRecent((prev) => [next, ...prev].slice(0, 5));
      }
      // Memorize the destination so the next paste of the same kind
      // pre-fills it. Only memorize when the provider was recognised
      // — "unknown" passes through as a single import and we don't
      // know which bucket to file the memory under.
      if (provider) {
        rememberFolder(drive, provider, kind, selectedFolder);
      }
      setUrl("");
      setKind("unknown");
      setProvider(null);
      onCreated();
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
    if (!expanded) setExpanded(true);
  }, [expanded]);

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

  // Collapsed state: render a compact "Add" button.
  if (!expanded) {
    return (
      <div data-testid="composer-collapsed">
        <button
          type="button"
          onClick={() => {
            setExpanded(true);
            setTimeout(() => inputRef.current?.focus(), 0);
          }}
          className="flex w-full items-center gap-2 rounded-lg border border-dashed border-border-primary bg-bg-card px-3 py-2.5 text-sm text-text-secondary hover:bg-bg-hover"
          data-testid="composer-expand"
        >
          <Plus size={16} />
          <span>Add a source — paste a URL or drop a link</span>
        </button>
      </div>
    );
  }

  return (
    <div
      className="relative"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid="composer-expanded"
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-xl border-2 border-dashed border-accent-cta bg-accent-cta/10">
          <div className="flex flex-col items-center gap-2 text-accent-cta">
            <LinkIcon size={48} />
            <span className="text-lg font-medium">Drop URL to import</span>
          </div>
        </div>
      )}

      <div className="space-y-4 rounded-lg border border-border-primary bg-bg-card p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-text-secondary">Add a source</h2>
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="text-xs text-text-muted hover:text-text-primary"
            data-testid="composer-collapse"
          >
            Collapse
          </button>
        </div>

        <div>
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
            <p
              className="mt-1 text-xs text-text-muted"
              data-testid="composer-url-hint"
            >
              {resolving
                ? "Detecting URL type..."
                : kind === "video"
                  ? "Single video — will create a single .loft"
                  : kind === "channel"
                    ? "YouTube channel — subscribe to track new uploads"
                    : kind === "playlist"
                      ? "YouTube playlist — subscribe to track all items"
                      : kind === "feed"
                        ? "Feed — subscribe"
                        : "Single import (unrecognized provider)"}
            </p>
          )}
        </div>

        <div>
          <button
            type="button"
            onClick={() => setFolderPickerOpen((v) => !v)}
            className="flex w-full items-center justify-between rounded-lg border border-border-primary bg-bg-primary px-3 py-2 text-sm text-text-primary hover:bg-bg-hover"
            data-testid="composer-folder-toggle"
          >
            <span className="flex items-center gap-2 text-text-secondary">
              <FolderIcon size={14} className="text-text-muted" />
              <span className="text-text-muted">Save to:</span>
              <span className="text-text-primary">
                {selectedFolder
                  ? `/${selectedFolder}`
                  : <span className="text-text-muted">drive root</span>}
              </span>
            </span>
            {folderPickerOpen
              ? <ChevronUp size={14} />
              : <ChevronDown size={14} />}
          </button>

          {folderPickerOpen && (
            <div className="mt-2 rounded-lg border border-border-primary bg-bg-primary">
              <div className="flex items-center gap-1 border-b border-border-primary px-3 py-2 text-xs text-text-secondary">
                <button
                  type="button"
                  onClick={() => handleBreadcrumbClick(-1)}
                  className="hover:text-text-primary"
                  data-testid="composer-breadcrumb-root"
                >
                  {drive || "..."}
                </button>
                {breadcrumbParts.map((name, i) => (
                  <span key={i} className="flex items-center gap-1">
                    <ChevronRight size={12} className="text-text-muted" />
                    <button
                      type="button"
                      onClick={() => handleBreadcrumbClick(i)}
                      className="hover:text-text-primary"
                    >
                      {name}
                    </button>
                  </span>
                ))}
              </div>
              <div className="max-h-44 overflow-y-auto">
                {folders.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-text-muted">No subfolders</div>
                ) : (
                  folders.map((f) => (
                    <button
                      key={f.path}
                      type="button"
                      onClick={() => handleFolderClick(f)}
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-text-primary hover:bg-bg-hover"
                    >
                      <FolderIcon size={14} className="shrink-0 text-text-muted" />
                      {f.name}
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </div>

        {showSubscriptionFields && (
          <div data-testid="composer-advanced-section">
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary"
              data-testid="composer-advanced-toggle"
            >
              {advanced ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              Advanced
            </button>
            {advanced && (
              <div className="mt-2 space-y-3 rounded-lg border border-border-primary bg-bg-primary p-3">
                <div>
                  <label className="mb-1 block text-xs text-text-secondary">
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
                      ? "Most-recent N uploads."
                      : "Items from the start of the playlist."}
                  </p>
                </div>
                <label className="flex items-center gap-2 text-xs text-text-primary">
                  <input
                    type="checkbox"
                    checked={includeNoTranscript}
                    onChange={(e) => setIncludeNoTranscript(e.target.checked)}
                  />
                  Try to fetch transcripts even when the video reports none
                </label>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-danger/10 px-3 py-2 text-sm text-danger" data-testid="composer-error">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end">
          <button
            onClick={handleSubmit}
            disabled={!url.trim() || !drive || submitting}
            className="rounded-lg bg-accent-cta px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50 hover:opacity-90"
            data-testid="composer-submit"
          >
            {submitLabel}
          </button>
        </div>
      </div>

      {recent.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-medium text-text-muted">
            Just imported
          </h3>
          <ul className="space-y-1">
            {recent.map((item) => (
              <li
                key={item.fileId}
                className="rounded-lg border border-border-primary bg-bg-card px-3 py-2 text-xs"
              >
                <div className="font-medium text-text-primary">
                  {item.filename}
                </div>
                <div className="truncate text-text-muted">{item.url}</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
