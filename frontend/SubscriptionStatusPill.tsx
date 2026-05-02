"use client";

import { AlertTriangle, CheckCircle2, Loader2, Pause } from "lucide-react";

import type { Subscription } from "./api";

/**
 * Single source of truth for a subscription's at-a-glance health.
 *
 * Status taxonomy (priority high -> low):
 *   syncing   — worker is running this sub right now
 *   attention — has at least one non-dismissed failed item
 *   paused    — is_enabled = false
 *   healthy   — none of the above
 *
 * The dashboard computes ``failedCount`` from server-side data and
 * passes it in; the pill itself is purely presentational so the
 * filter chip and the card render the same logic.
 */

export type SubscriptionStatus =
  | "syncing"
  | "attention"
  | "paused"
  | "healthy";

export function deriveStatus(
  sub: Subscription,
  failedCount: number,
): SubscriptionStatus {
  if (sub.running) return "syncing";
  if (failedCount > 0) return "attention";
  if (!sub.is_enabled) return "paused";
  return "healthy";
}

interface Props {
  status: SubscriptionStatus;
  className?: string;
}

const STYLES: Record<SubscriptionStatus, string> = {
  healthy: "bg-success/10 text-success",
  attention: "bg-warning/10 text-warning",
  paused: "bg-bg-hover text-text-muted",
  syncing: "bg-accent-cta/10 text-accent-cta",
};

const LABELS: Record<SubscriptionStatus, string> = {
  healthy: "Healthy",
  attention: "Needs attention",
  paused: "Paused",
  syncing: "Syncing",
};

export default function SubscriptionStatusPill({
  status,
  className = "",
}: Props) {
  const Icon = ({
    healthy: CheckCircle2,
    attention: AlertTriangle,
    paused: Pause,
    syncing: Loader2,
  } as const)[status];

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STYLES[status]} ${className}`}
      data-testid={`status-pill-${status}`}
    >
      <Icon
        size={12}
        className={status === "syncing" ? "animate-spin" : ""}
      />
      {LABELS[status]}
    </span>
  );
}
