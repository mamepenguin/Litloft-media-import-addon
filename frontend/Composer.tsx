"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslations } from "next-intl";
import {
  ChevronDown,
  ChevronUp,
  Link as LinkIcon,
  Plus,
} from "lucide-react";

import { FolderPicker } from "@/components/FolderPicker";

import {
  createLoft,
  createSubscription,
  resolveSubscriptionUrl,
  syncSubscription,
  type DisplayMode,
  type SttMode,
  type SubscriptionKind,
} from "./api";
import DisplayModeField from "./DisplayModeField";
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

const STT_MODE_STORAGE_KEY = "media_import.stt_mode_v1";
const STT_MODES: SttMode[] = ["manual", "missing_captions", "always"];

function readStoredSttMode(): SttMode {
  if (typeof window === "undefined") return "manual";
  const raw = window.localStorage.getItem(STT_MODE_STORAGE_KEY);
  return STT_MODES.includes(raw as SttMode) ? (raw as SttMode) : "manual";
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
  const t = useTranslations("mediaImport");
  const [expanded, setExpanded] = useState(initialExpanded);
  const [url, setUrl] = useState("");
  const [kind, setKind] = useState<SubscriptionKind>("unknown");
  const [provider, setProvider] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  const [selectedFolder, setSelectedFolder] = useState("");

  const [advanced, setAdvanced] = useState(false);
  const [backfill, setBackfill] = useState(15);
  // Library-only unless the user says otherwise: subscribing means
  // "make this searchable", not "queue this up to watch".
  const [displayMode, setDisplayMode] = useState<DisplayMode>("library");
  const [includeNoTranscript, setIncludeNoTranscript] = useState(false);
  const [sttMode, setSttMode] = useState<SttMode>("manual");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<PendingItem[]>([]);
  const [dragOver, setDragOver] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSttMode(readStoredSttMode());
  }, []);

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
          if (
            res.provider
            && (res.kind === "channel" || res.kind === "playlist" || res.kind === "video")
            && selectedFolder === ""
          ) {
            const last = getLastFolder(drive, res.provider, res.kind);
            if (last) {
              setSelectedFolder(last);
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
          display_mode: displayMode,
        });
        await syncSubscription(drive, sub.id, backfill);
      } else {
        window.localStorage.setItem(STT_MODE_STORAGE_KEY, sttMode);
        const result = await createLoft(trimmed, drive, selectedFolder, sttMode);
        const next: PendingItem = {
          url: trimmed,
          filename: result.filename,
          fileId: result.file_id,
          createdAt: Date.now(),
        };
        setRecent((prev) => [next, ...prev].slice(0, 5));
      }
      if (provider) {
        rememberFolder(drive, provider, kind, selectedFolder);
      }
      setUrl("");
      setKind("unknown");
      setProvider(null);
      onCreated();
      inputRef.current?.focus();
    } catch (e) {
      setError(e instanceof Error ? e.message : t("composer.errorFallback"));
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

  const showSubscriptionFields = isSubscriptionKind(kind);
  const submitLabel = submitting
    ? t("composer.submitting")
    : showSubscriptionFields
      ? t("composer.submitSubscribe")
      : t("composer.submitImport");

  const kindHint = (() => {
    if (resolving) return t("composer.kind.detecting");
    switch (kind) {
      case "video": return t("composer.kind.video");
      case "channel": return t("composer.kind.channel");
      case "playlist": return t("composer.kind.playlist");
      case "feed": return t("composer.kind.feed");
      default: return t("composer.kind.unknown");
    }
  })();
  const sttModeLabels: Record<SttMode, string> = {
    manual: t("composer.sttMode.manual"),
    missing_captions: t("composer.sttMode.missing_captions"),
    always: t("composer.sttMode.always"),
  };

  if (!expanded) {
    return (
      <div className="mx-auto w-full max-w-3xl" data-testid="composer-collapsed">
        <button
          type="button"
          onClick={() => {
            setExpanded(true);
            setTimeout(() => inputRef.current?.focus(), 0);
          }}
          className="flex w-full items-center gap-2 rounded-2xl border border-bg-border bg-bg-card px-4 py-3 text-sm text-text-muted transition-colors hover:bg-bg-elevated"
          data-testid="composer-expand"
        >
          <Plus size={16} />
          <span>{t("composer.addSourceCollapsed")}</span>
        </button>
      </div>
    );
  }

  return (
    <div
      className="relative mx-auto w-full max-w-3xl"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      data-testid="composer-expanded"
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-xl border-2 border-dashed border-accent bg-accent/10">
          <div className="flex flex-col items-center gap-2 text-accent">
            <LinkIcon size={48} />
            <span className="text-lg font-medium">{t("composer.dropOverlay")}</span>
          </div>
        </div>
      )}

      <div className="space-y-4 rounded-xl border border-bg-border bg-bg-card p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-text-primary">
            {t("composer.heading")}
          </h2>
          <button
            type="button"
            onClick={() => setExpanded(false)}
            className="text-xs text-text-muted hover:text-text-primary"
            data-testid="composer-collapse"
          >
            {t("composer.collapse")}
          </button>
        </div>

        <div>
          <input
            ref={inputRef}
            type="text"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t("composer.urlPlaceholder")}
            className="w-full rounded-2xl border border-bg-border bg-bg-primary px-4 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-focus-ring focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                handleSubmit();
              }
            }}
          />
          {url.trim() && (
            <p
              className="mt-1.5 text-xs text-text-muted"
              data-testid="composer-url-hint"
            >
              {kindHint}
            </p>
          )}
        </div>

        <FolderPicker
          drive={drive}
          value={selectedFolder}
          onChange={setSelectedFolder}
        />

        {!showSubscriptionFields && (
          <div>
            <label className="mb-1.5 block text-xs text-text-muted">
              {t("composer.sttMode.label")}
            </label>
            <div
              className="grid grid-cols-3 rounded-xl border border-bg-border bg-bg-primary p-1"
              data-testid="composer-stt-mode"
            >
              {STT_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => {
                    setSttMode(mode);
                    window.localStorage.setItem(STT_MODE_STORAGE_KEY, mode);
                  }}
                  className={
                    mode === sttMode
                      ? "rounded-lg bg-bg-card px-2 py-1.5 text-xs font-medium text-text-primary shadow-sm"
                      : "rounded-lg px-2 py-1.5 text-xs text-text-muted hover:text-text-primary"
                  }
                  data-testid={`composer-stt-mode-${mode}`}
                >
                  {sttModeLabels[mode]}
                </button>
              ))}
            </div>
          </div>
        )}

        {showSubscriptionFields && (
          <div
            className="rounded-xl border border-bg-border bg-bg-primary p-4"
            data-testid="composer-display-mode"
          >
            <DisplayModeField
              name="composer-display-mode"
              value={displayMode}
              onChange={setDisplayMode}
            />
          </div>
        )}

        {showSubscriptionFields && (
          <div data-testid="composer-advanced-section">
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary"
              data-testid="composer-advanced-toggle"
            >
              {advanced ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              {t("composer.advanced")}
            </button>
            {advanced && (
              <div className="mt-2 space-y-3 rounded-xl border border-bg-border bg-bg-primary p-4">
                <div>
                  <label className="mb-1 block text-xs text-text-muted">
                    {t("composer.backfill")}
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={backfill}
                    onChange={(e) => setBackfill(Math.max(1, Number(e.target.value) || 1))}
                    className="w-full rounded-2xl border border-bg-border bg-bg-primary px-3 py-2 text-sm text-text-primary focus:border-focus-ring focus:outline-none"
                  />
                  <p className="mt-1 text-xs text-text-muted">
                    {kind === "channel"
                      ? t("composer.backfillHintChannel")
                      : t("composer.backfillHintPlaylist")}
                  </p>
                </div>
                <label className="flex items-center gap-2 text-xs text-text-primary">
                  <input
                    type="checkbox"
                    checked={includeNoTranscript}
                    onChange={(e) => setIncludeNoTranscript(e.target.checked)}
                  />
                  {t("composer.includeNoTranscript")}
                </label>
              </div>
            )}
          </div>
        )}

        {error && (
          <div
            className="rounded-2xl bg-danger/10 px-3 py-2 text-sm text-danger"
            data-testid="composer-error"
          >
            {error}
          </div>
        )}

        <div className="flex items-center justify-end">
          <button
            onClick={handleSubmit}
            disabled={!url.trim() || !drive || submitting}
            className="rounded-2xl bg-accent px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:bg-sand disabled:text-warm-silver disabled:cursor-not-allowed"
            data-testid="composer-submit"
          >
            {submitLabel}
          </button>
        </div>
      </div>

      {recent.length > 0 && (
        <div className="mt-5">
          <h3 className="mb-2 text-[11px] font-semibold uppercase text-text-muted">
            {t("composer.justImportedHeading")}
          </h3>
          <ul className="space-y-1.5">
            {recent.map((item) => (
              <li
                key={item.fileId}
                className="rounded-xl border border-bg-border bg-bg-card px-4 py-2.5 text-sm text-text-primary"
              >
                {item.filename.endsWith(".loft")
                  ? item.filename.slice(0, -".loft".length)
                  : item.filename}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
