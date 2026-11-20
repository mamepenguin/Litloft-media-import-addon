"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronLeft, Film } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";

import { EmptyState } from "@/components/EmptyState";
import { FileContextMenu } from "@/components/FileContextMenu";
import { PageFrame } from "@/components/PageFrame";
import { PageHeader } from "@/components/PageHeader";
import { RetryButton } from "@/components/appshell/RetryButton";
import { useNearEnd } from "@/components/appshell/useNearEnd";
import { VideoGrid } from "@/components/appshell/VideoGrid";
import { SkeletonGrid } from "@/components/videoapp/VideoPagedGrid";
import { VideoCard } from "@/components/videoapp/VideoCard";
import { useContextMenu } from "@/hooks/useContextMenu";
import { getShelves, type ShelvesResponse, type ShelfItem } from "@/lib/api";
import { fileLinkHref } from "@/lib/canonicalFileUrl";
import type { FileItem } from "@/types";

import { listChannelVideos, type ChannelVideoItem } from "./api";
import { formatPublishedDate } from "./lib/publishedDate";

const BATCH = 60;

type Phase = "loading" | "ready" | "empty" | "failed";
type More = "idle" | "loading" | "failed";

interface CardEntry {
  item: ShelfItem;
  publishedAt: string | null;
}

function cardsFrom(
  batch: readonly ChannelVideoItem[],
  answer: ShelvesResponse,
): CardEntry[] {
  const byId = new Map<string, ShelfItem>();
  for (const shelf of answer.shelves) {
    for (const item of shelf.items) byId.set(item.id, item);
  }
  const cards: CardEntry[] = [];
  for (const row of batch) {
    const item = byId.get(row.file_id);
    if (item) cards.push({ item, publishedAt: row.published_at });
  }
  return cards;
}

/**
 * One channel's videos in one drive: the Media Import page opened with
 * `?provider=&channel=`.
 */
export default function ChannelVideos({
  drive,
  provider,
  channel,
}: {
  drive: string;
  provider: string;
  channel: string;
}) {
  const t = useTranslations("mediaImport");
  const locale = useLocale();

  const [phase, setPhase] = useState<Phase>("loading");
  const [cards, setCards] = useState<CardEntry[]>([]);
  const [more, setMore] = useState<More>("idle");
  const [exhausted, setExhausted] = useState(false);
  const [target, setTarget] = useState<FileItem | null>(null);

  const idsRef = useRef<readonly ChannelVideoItem[]>([]);
  const positionRef = useRef(0);
  const runRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const stepRef = useRef(false);
  const pressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suppressClick = useRef(false);

  const start = useCallback(() => {
    const run = ++runRef.current;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    idsRef.current = [];
    positionRef.current = 0;
    stepRef.current = false;
    setPhase("loading");
    setCards([]);
    setMore("idle");
    setExhausted(false);

    const current = () => runRef.current === run && !controller.signal.aborted;

    void (async () => {
      let rows: ChannelVideoItem[];
      try {
        rows = await listChannelVideos(
          drive,
          provider,
          channel,
          controller.signal,
        );
      } catch {
        if (current()) setPhase("failed");
        return;
      }
      if (!current()) return;

      if (rows.length === 0) {
        setExhausted(true);
        setPhase("empty");
        return;
      }
      idsRef.current = rows;

      // The first load step keeps going while its batches draw nothing, so a
      // channel whose first 60 files were dropped still shows what is left.
      const drawn: CardEntry[] = [];
      while (positionRef.current < idsRef.current.length) {
        const from = positionRef.current;
        const batch = idsRef.current.slice(from, from + BATCH);
        let answer: ShelvesResponse;
        try {
          answer = await getShelves(
            drive,
            {
              by: "all",
              key: "all",
              per_shelf: BATCH,
              ids: batch.map((row) => row.file_id),
            },
            { signal: controller.signal },
          );
        } catch {
          if (current()) setPhase("failed");
          return;
        }
        if (!current()) return;
        positionRef.current = from + batch.length;
        drawn.push(...cardsFrom(batch, answer));
        if (drawn.length > 0) {
          setCards(drawn);
          setExhausted(positionRef.current >= idsRef.current.length);
          setPhase("ready");
          return;
        }
      }
      setExhausted(true);
      setPhase("empty");
    })();
  }, [drive, provider, channel]);

  const loadMore = useCallback(() => {
    if (phase !== "ready" || exhausted || stepRef.current) return;
    const run = runRef.current;
    const controller = controllerRef.current;
    if (!controller || controller.signal.aborted) return;

    stepRef.current = true;
    setMore("loading");
    const current = () => runRef.current === run && !controller.signal.aborted;

    void (async () => {
      try {
        while (positionRef.current < idsRef.current.length) {
          const from = positionRef.current;
          const batch = idsRef.current.slice(from, from + BATCH);
          const answer = await getShelves(
            drive,
            {
              by: "all",
              key: "all",
              per_shelf: BATCH,
              ids: batch.map((row) => row.file_id),
            },
            { signal: controller.signal },
          );
          if (!current()) return;
          // The position moves only past a batch whose call succeeded, so a
          // retry asks for the batch that failed again.
          positionRef.current = from + batch.length;
          const drawn = cardsFrom(batch, answer);
          if (drawn.length > 0) {
            setCards((prev) => [...prev, ...drawn]);
            break;
          }
        }
        if (!current()) return;
        setExhausted(positionRef.current >= idsRef.current.length);
        setMore("idle");
      } catch {
        if (current()) setMore("failed");
      } finally {
        if (runRef.current === run) stepRef.current = false;
      }
    })();
  }, [drive, phase, exhausted]);

  useEffect(() => {
    start();
    return () => {
      controllerRef.current?.abort();
      if (pressTimer.current) clearTimeout(pressTimer.current);
    };
  }, [start]);

  const sentinel = useNearEnd(
    phase === "ready" && !exhausted && more === "idle",
    loadMore,
  );

  const { menuState, close, handlers } = useContextMenu();

  function clearPress() {
    if (pressTimer.current) {
      clearTimeout(pressTimer.current);
      pressTimer.current = null;
    }
  }

  function cardHandlers(item: ShelfItem) {
    return {
      onContextMenu: (e: React.MouseEvent<HTMLDivElement>) => {
        setTarget(item);
        handlers.onContextMenu(e);
      },
      onTouchStart: (e: React.TouchEvent<HTMLDivElement>) => {
        setTarget(item);
        suppressClick.current = false;
        handlers.onTouchStart(e);
        clearPress();
        pressTimer.current = setTimeout(() => {
          pressTimer.current = null;
          suppressClick.current = true;
        }, 500);
      },
      onTouchEnd: () => {
        handlers.onTouchEnd();
        clearPress();
      },
      onTouchMove: () => {
        handlers.onTouchMove();
        clearPress();
      },
      // The click at the end of a press that opened the menu would otherwise
      // also open the file; capture runs before the card link's own onClick.
      onClickCapture: (e: React.MouseEvent<HTMLDivElement>) => {
        if (!suppressClick.current) return;
        suppressClick.current = false;
        e.preventDefault();
        e.stopPropagation();
      },
    };
  }

  const header = (
    <PageHeader
      breadcrumb={
        <Link
          href={`/drive/${encodeURIComponent(drive)}/addons/media_import`}
          className="inline-flex items-center gap-1 text-[13px] text-text-muted transition-colors hover:text-text-primary hover:underline"
        >
          <ChevronLeft size={16} aria-hidden="true" />
          {t("sidebar.label")}
        </Link>
      }
      title={channel}
      scope={drive || undefined}
    />
  );

  return (
    <PageFrame width="full" header={header}>
      <div className="px-4 pb-6 pt-6">
        {phase === "loading" && <SkeletonGrid rows={2} />}

        {phase === "failed" && <RetryButton onRetry={start} />}

        {phase === "empty" && (
          <EmptyState icon={Film} title={t("channelView.empty")} />
        )}

        {phase === "ready" && (
          <>
            <VideoGrid>
              {() =>
                cards.map(({ item, publishedAt }) => (
                  <li key={item.id} className="min-w-0">
                    <div
                      className="select-none [-webkit-touch-callout:none]"
                      {...cardHandlers(item)}
                    >
                      <VideoCard
                        item={item}
                        byline={
                          formatPublishedDate(publishedAt, locale) ?? undefined
                        }
                      />
                    </div>
                  </li>
                ))
              }
            </VideoGrid>
            <div ref={sentinel} aria-hidden="true" className="h-px" />
            {more === "loading" && (
              <div className="mt-8">
                <SkeletonGrid rows={1} />
              </div>
            )}
            {more === "failed" && (
              <div className="mt-8">
                <RetryButton onRetry={loadMore} />
              </div>
            )}
          </>
        )}
      </div>

      <FileContextMenu
        open={menuState.open}
        position={menuState.position}
        target={target}
        onClose={close}
        onOpenInNewTab={() => {
          if (target) window.open(fileLinkHref(target), "_blank");
        }}
        onUpdate={start}
      />
    </PageFrame>
  );
}
