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

import { listSubscriptions } from "./api";
import ActivityFeed from "./ActivityFeed";
import Composer from "./Composer";
import SubscriptionsDashboard from "./SubscriptionsDashboard";

/**
 * Media Import page: URL import, subscription configuration, import
 * health, failures, and activity.
 *
 * The subscription list is read once to decide whether the Composer
 * starts expanded: it does when the drive has no subscription, and when
 * the read failed.
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
export default function MediaImportPage() {
  const params = useParams();
  const drive = decodeURIComponent((params?.name as string) ?? "");

  return <DriveMediaImportPage key={drive} drive={drive} />;
}

function DriveMediaImportPage({ drive }: { drive: string }) {
  const t = useTranslations("mediaImport");

  const [subsCount, setSubsCount] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!drive) return;
    let cancelled = false;
    listSubscriptions(drive)
      .then((rows) => {
        if (!cancelled) setSubsCount(rows.length);
      })
      .catch(() => {
        if (!cancelled) setSubsCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [drive]);

  // The scope line names the drive and no count: the subscription list is
  // read once on mount, so a number here would go stale the moment a source
  // is added below it.
  const header = (
    <PageHeader
      titleIcon={Rss}
      title={t("sidebar.label")}
      scope={drive || undefined}
    />
  );

  if (!drive || subsCount === null) {
    return (
      <PageFrame width="full" header={header}>
        <div className="px-4 pb-6 text-sm text-text-muted">{t("loadingDrive")}</div>
      </PageFrame>
    );
  }

  return (
    <PageFrame width="full" header={header}>
      <div className="px-4 pb-6 pt-6">
        <Composer
          drive={drive}
          initialExpanded={subsCount === 0}
          onCreated={() => setRefreshKey((k) => k + 1)}
        />

        <SubscriptionsDashboard drive={drive} refreshKey={refreshKey} />

        <section>
          <h2 className="mb-3 text-[11px] font-semibold uppercase text-text-muted">
            {t("activity.heading")}
          </h2>
          <ActivityFeed drive={drive} refreshKey={refreshKey} />
        </section>
      </div>
    </PageFrame>
  );
}
