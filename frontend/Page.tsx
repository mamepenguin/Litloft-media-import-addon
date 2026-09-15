"use client";

// Side-effect: ensure media_import's player registrations are evaluated
// when this addon's standalone page is loaded directly.
import "./players/registerMediaImportPlayers";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { Rss } from "lucide-react";

import { PageFrame } from "@/components/PageFrame";
import { PageHeader } from "@/components/PageHeader";
import { PageTabs } from "@/components/PageTabs";

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

const TABS = ["watch", "manage"] as const;
type View = (typeof TABS)[number];

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

  // The scope line names the drive and no count: the subscription list is
  // read once on mount, so a number here would go stale the moment a source
  // is added below it.
  const header = (
    <PageHeader
      titleIcon={Rss}
      title={t("sidebar.label")}
      scope={currentDrive || undefined}
      tabs={
        view === null ? undefined : (
          <PageTabs
            items={TABS.map((key) => ({ key, label: t(`nav.${key}`) }))}
            current={view}
            onSelect={(key) => setView(key as View)}
            label={t("nav.label")}
          />
        )
      }
    />
  );

  if (!currentDrive || view === null) {
    return (
      <PageFrame width="full" header={header}>
        <div className="px-4 pb-6 text-sm text-text-muted">{t("loadingDrive")}</div>
      </PageFrame>
    );
  }

  return (
    <PageFrame width="full" header={header}>
      <div className="space-y-8 px-4 pb-6">
      {/* `PageTabs` promises a tablist when its items do not navigate, and a
          tablist without a panel is half of that promise: a screen reader is
          told activating a tab swaps a region, and nothing says which region.
          Named rather than `aria-labelledby`-ed, because core generates the
          tab ids and does not expose them — the weaker of the two bindings,
          and the one this side can make on its own. */}
      <div role="tabpanel" aria-label={t(`nav.${view}`)}>
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
      </div>
    </PageFrame>
  );
}
