"use client";

// Side-effect: ensure media_import's player registrations are evaluated
// when this addon's standalone page is loaded directly.
import "./players/registerMediaImportPlayers";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";

import {
  listSubscriptions,
} from "./api";
import ActivityFeed from "./ActivityFeed";
import Composer from "./Composer";
import SubscriptionsDashboard from "./SubscriptionsDashboard";

/**
 * Media Import addon page — three sections:
 *
 *   1. Composer  — paste URL / drop link / subscribe
 *   2. Dashboard — subscription cards in a responsive grid
 *   3. Activity  — unified import feed
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
  const currentDrive = decodeURIComponent((params?.name as string) ?? "");
  const t = useTranslations("mediaImport");

  const [subsCount, setSubsCount] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!currentDrive) return;
    listSubscriptions(currentDrive)
      .then((rows) => setSubsCount(rows.length))
      .catch(() => setSubsCount(0));
  }, [currentDrive]);

  if (!currentDrive) {
    return (
      <div className="p-4 text-sm text-text-muted sm:p-6">
        {t("loadingDrive")}
      </div>
    );
  }

  return (
    <div className="space-y-8 p-4 sm:p-6">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h1 className="text-2xl font-bold text-text-primary">
          {t("title")}
        </h1>
        <span className="text-xs text-text-muted">
          {t("driveLabel", { drive: currentDrive })}
        </span>
      </header>

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
    </div>
  );
}
