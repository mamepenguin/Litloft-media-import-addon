"use client";

// Side-effect: ensure media_import's player registrations are evaluated
// when this addon's standalone page is loaded directly.
import "./players/registerMediaImportPlayers";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import {
  listSubscriptions,
} from "./api";
import ActivityFeed from "./ActivityFeed";
import Composer from "./Composer";
import SubscriptionsDashboard from "./SubscriptionsDashboard";

/**
 * Media Import addon page — three sections, top to bottom:
 *
 *   1. Composer  — paste URL / drop link / subscribe
 *   2. Dashboard — subscription cards with summary header
 *   3. Activity  — unified import feed
 *
 * Composer starts expanded only when the drive has zero subscriptions
 * (first-run mode); on installs that already have content it
 * collapses to a one-line affordance so the dashboard is the visual
 * primary.
 *
 * The page is scope=drive: it always operates on the drive named in
 * the URL (``/drive/{name}/addons/media_import``). We never expose
 * a drive picker on this page — that would cross drive boundaries
 * and bypass per-drive access control (hako cRNeIvcbhz449BwTmof5m).
 */

export default function MediaImportPage() {
  const params = useParams();
  const currentDrive = decodeURIComponent((params?.name as string) ?? "");

  const [subsCount, setSubsCount] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // Probe whether to render the composer expanded (zero-state) or
  // collapsed. Done as a one-shot fetch — the dashboard owns the
  // authoritative subscription list and re-fetches independently.
  useEffect(() => {
    if (!currentDrive) return;
    listSubscriptions(currentDrive)
      .then((rows) => setSubsCount(rows.length))
      .catch(() => setSubsCount(0));
  }, [currentDrive]);

  if (!currentDrive) {
    return (
      <div className="mx-auto max-w-3xl p-6 text-sm text-text-muted">
        Loading drive...
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold text-text-primary">Media Import</h1>
        <span className="text-xs text-text-muted">drive: {currentDrive}</span>
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
        <h2 className="mb-2 text-sm font-medium text-text-secondary">
          Recent activity
        </h2>
        <ActivityFeed drive={currentDrive} refreshKey={refreshKey} />
      </section>
    </div>
  );
}
