"use client";

// Side-effect: ensure media_import's player registrations are evaluated
// when this addon's standalone page is loaded directly.
import "./players/registerMediaImportPlayers";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { listSubscriptions } from "./api";
import ActivityFeed from "./ActivityFeed";
import Composer from "./Composer";
import SubscriptionsDashboard from "./SubscriptionsDashboard";
import WatchView from "./watch";

/**
 * Media Import addon page — two top-level views:
 *
 *   Watch  — a calm place to notice and play videos from subscriptions
 *            the user deliberately chose to surface.
 *   Manage — URL import, subscription configuration, import health,
 *            failures, and activity.
 *
 * Manage is the landing view when nothing is surfaced yet: with no
 * feed/regular subscriptions and nothing part-watched, Watch has
 * nothing useful to say, while Manage is where the user goes to change
 * that. Watch is never presented as work left undone — importing for
 * search alone is the normal case (spec §3.1).
 *
 * Layout follows the core ``DriveHome`` pattern (no max-width cap, just
 * page padding) so the dashboard cards can fill the available width on
 * large displays while the Composer auto-centers under a comfortable
 * reading measure.
 *
 * scope=drive: this page always operates on the drive named in the URL
 * (``/drive/{name}/addons/media_import``). We never expose a drive
 * picker — that would cross drive boundaries (hako cRNeIvcbhz449BwTmof5m).
 */

type View = "watch" | "manage";

export default function MediaImportPage() {
  const params = useParams();
  const currentDrive = decodeURIComponent((params?.name as string) ?? "");
  const t = useTranslations("mediaImport");

  const [subsCount, setSubsCount] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [view, setView] = useState<View | null>(null);
  // Whether anything is *configured* to surface — a statement about
  // setup, not a count of unwatched videos. Decides both the landing
  // view and which empty-state sentence Watch shows.
  const [hasSurfacedSources, setHasSurfacedSources] = useState(false);

  useEffect(() => {
    if (!currentDrive) return;
    let cancelled = false;
    listSubscriptions(currentDrive)
      .then((rows) => {
        if (cancelled) return;
        const surfaced = rows.some((r) => r.display_mode !== "library");
        setSubsCount(rows.length);
        setHasSurfacedSources(surfaced);
        setView(surfaced ? "watch" : "manage");
      })
      .catch(() => {
        if (cancelled) return;
        setSubsCount(0);
        setHasSurfacedSources(false);
        setView("manage");
      });
    return () => {
      cancelled = true;
    };
  }, [currentDrive]);

  if (!currentDrive || view === null) {
    return (
      <div className="p-4 text-sm text-text-muted sm:p-6">
        {t("loadingDrive")}
      </div>
    );
  }

  return (
    <div className="space-y-8 p-4 sm:p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="text-2xl font-bold text-text-primary">{t("title")}</h1>
        <span className="text-xs text-text-muted">
          {t("driveLabel", { drive: currentDrive })}
        </span>
      </header>

      <nav
        className="flex gap-1 border-b border-bg-border"
        aria-label={t("nav.label")}
      >
        {(["watch", "manage"] as const).map((key) => (
          <button
            key={key}
            type="button"
            aria-current={view === key ? "page" : undefined}
            onClick={() => setView(key)}
            className={[
              "-mb-px rounded-t-xl border-b-2 px-4 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring",
              view === key
                ? "border-accent font-semibold text-text-primary"
                : "border-transparent text-text-muted hover:text-text-primary",
            ].join(" ")}
          >
            {t(`nav.${key}`)}
          </button>
        ))}
      </nav>

      {view === "watch" ? (
        <WatchView
          drive={currentDrive}
          hasSurfacedSources={hasSurfacedSources}
          onGoToManage={() => setView("manage")}
        />
      ) : (
        <>
          <Composer
            drive={currentDrive}
            initialExpanded={subsCount === 0}
            onCreated={() => setRefreshKey((k) => k + 1)}
          />

          <SubscriptionsDashboard
            drive={currentDrive}
            refreshKey={refreshKey}
          />

          <section>
            <h2 className="mb-3 text-[11px] font-semibold uppercase text-text-muted">
              {t("activity.heading")}
            </h2>
            <ActivityFeed drive={currentDrive} refreshKey={refreshKey} />
          </section>
        </>
      )}
    </div>
  );
}
