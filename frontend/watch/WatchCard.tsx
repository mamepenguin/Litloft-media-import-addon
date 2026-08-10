"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { ExternalLink, ListPlus, Play } from "lucide-react";

import type { WatchItem } from "../api";

interface Props {
  item: WatchItem;
  onAddToCollection: (item: WatchItem) => void;
}

/** yt-dlp records `upload_date` as a bare `YYYYMMDD` string. */
function formatPublished(raw: string | null): string | null {
  if (!raw) return null;
  const compact = /^(\d{4})(\d{2})(\d{2})$/.exec(raw);
  const iso = compact ? `${compact[1]}-${compact[2]}-${compact[3]}` : raw;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? raw : d.toLocaleDateString();
}

export default function WatchCard({ item, onAddToCollection }: Props) {
  const t = useTranslations("mediaImport.watch");
  const published = formatPublished(item.published_at);
  const playback = item.playback;
  const completed = playback?.state === "completed";
  const inProgress = playback?.state === "in_progress";

  return (
    <article
      className="group flex flex-col overflow-hidden rounded-xl bg-bg-card shadow-card"
      data-testid={`watch-card-${item.file_id}`}
    >
      <Link
        href={`/files/${item.file_id}`}
        className="relative block aspect-video overflow-hidden bg-bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
      >
        {item.thumbnail_path && (
          <img
            src={`/api/files/${item.file_id}/thumbnail`}
            alt=""
            loading="lazy"
            className={[
              "size-full object-cover",
              // Finished videos are quieter, never hidden or reordered
              // — doing nothing about them is a valid response.
              completed ? "opacity-60" : "",
            ].join(" ")}
            onError={(ev) => {
              (ev.target as HTMLImageElement).style.display = "none";
            }}
          />
        )}
        {inProgress && playback && playback.duration > 0 && (
          <div className="absolute inset-x-0 bottom-0 h-[3px] bg-white/20">
            <div
              className="h-full bg-accent"
              data-testid="watch-progress-bar"
              style={{
                width: `${Math.min(
                  (playback.position / playback.duration) * 100,
                  100,
                )}%`,
              }}
            />
          </div>
        )}
        {completed && (
          <span className="absolute right-2 top-2 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-medium text-white">
            {t("state.completed")}
          </span>
        )}
      </Link>

      <div className="flex min-w-0 flex-1 flex-col p-3">
        <h3 className="line-clamp-2 text-sm font-semibold text-text-primary">
          {item.title || item.filename}
        </h3>
        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs text-text-muted">
          {item.channel && <span className="truncate">{item.channel}</span>}
          {item.channel && published && <span>·</span>}
          {published && <span>{published}</span>}
        </div>

        <div className="mt-2.5 flex items-center gap-1">
          <Link
            href={`/files/${item.file_id}`}
            className="inline-flex items-center gap-1 rounded-2xl px-2 py-1 text-xs text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
          >
            <Play size={13} />
            {inProgress ? t("action.resume") : t("action.play")}
          </Link>
          <button
            type="button"
            onClick={() => onAddToCollection(item)}
            className="inline-flex items-center gap-1 rounded-2xl px-2 py-1 text-xs text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
          >
            <ListPlus size={13} />
            {t("action.addToCollection")}
          </button>
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer noopener"
            aria-label={t("action.openSource")}
            className="ml-auto inline-flex items-center rounded-2xl px-2 py-1 text-text-muted transition-colors hover:bg-bg-elevated hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
          >
            <ExternalLink size={13} />
          </a>
        </div>
      </div>
    </article>
  );
}
