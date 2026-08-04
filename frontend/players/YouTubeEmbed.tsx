"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  deleteWatchProgress,
  getWatchProgress,
  saveWatchProgress,
} from "@/lib/api";
import {
  clearProgress,
  getSavedProgress,
  saveProgress,
} from "@/lib/recentlyPlayed";
import {
  createYouTubeController,
  type MediaController,
  type YouTubePlayerLike,
} from "@/lib/mediaController";
import { useShortcuts } from "@/hooks/useShortcuts";
import { useProfile } from "@/components/ProfileProvider";
import type { LoftEmbedProps } from "@/components/loft/types";
import MediaControls from "@/components/player/MediaControls";
import { useFullscreen } from "@/components/player/hooks/useFullscreen";
import { loadYouTubeIframeApi } from "./loadYouTubeIframeApi";

const SAVE_INTERVAL = 5;
const RESUME_THRESHOLD = 5;
const POLL_INTERVAL_MS = 1000;
const YT_STATE_ENDED = 0;
const YT_STATE_PLAYING = 1;
const YT_STATE_BUFFERING = 3;

/**
 * How far the player's reported duration may drift from our own
 * metadata before we call it an ad. yt-dlp and the player routinely
 * disagree by about a second on the same video.
 */
const AD_DURATION_TOLERANCE_S = 2;

/**
 * How long a single click waits to see whether it is really the first
 * half of a double-click. Matches the delay mainstream players use.
 */
const CLICK_RESOLVE_MS = 220;

/**
 * Decide whether the player is currently playing an ad rather than the
 * requested video.
 *
 * There is no ad-state API on the YouTube IFrame player. What we can
 * observe is that during an ad, getDuration() reports the *ad's*
 * length. Comparing that against a duration we captured at import time
 * — which no ad can influence — is the most reliable signal available.
 *
 * Deliberately fail-open: with no trustworthy duration to compare
 * against, report "not an ad". A false positive disables seeking in the
 * middle of a video, which is far worse than letting an ad desync the
 * clock for a few seconds.
 */
export function isAdDuration(
  reportedDuration: number,
  durationHint: number | null | undefined,
  toleranceSeconds: number = AD_DURATION_TOLERANCE_S,
): boolean {
  if (durationHint == null) return false;
  if (!Number.isFinite(durationHint) || durationHint <= 0) return false;
  // The player reports 0 until metadata lands; that is "unknown", not
  // "an ad the length of nothing".
  if (!Number.isFinite(reportedDuration) || reportedDuration <= 0) return false;
  return Math.abs(reportedDuration - durationHint) > toleranceSeconds;
}

const YOUTUBE_HOSTS = new Set([
  "www.youtube.com",
  "youtube.com",
  "m.youtube.com",
  "music.youtube.com",
  "youtu.be",
]);

export function extractYouTubeId(url: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (!YOUTUBE_HOSTS.has(parsed.hostname)) return null;
  const match = url.match(
    /(?:youtube\.com\/(?:watch\?v=|embed\/|shorts\/)|youtu\.be\/)([\w-]{11})/,
  );
  return match?.[1] ?? null;
}

export default function YouTubeEmbed({
  fileId,
  url,
  onMediaController,
  initialTime,
  durationHint,
}: LoftEmbedProps) {
  const videoId = extractYouTubeId(url);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const mountRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<
    (YouTubePlayerLike & { destroy(): void }) | null
  >(null);
  const controllerRef = useRef<MediaController | null>(null);
  const lastSavedRef = useRef(0);
  const [loadFailed, setLoadFailed] = useState(false);
  // Held in state (not just the ref) so the control bar renders as soon
  // as the player is ready.
  const [controller, setController] = useState<MediaController | null>(null);
  const [adActive, setAdActive] = useState(false);
  const [ended, setEnded] = useState(false);
  const [playing, setPlaying] = useState(false);
  const tsc = useTranslations("shortcuts");
  const { nickname } = useProfile();
  const hasProfile = nickname !== null;

  // Read through a ref so the detector handed to the controller always
  // sees the current hint without rebuilding the player.
  const durationHintRef = useRef(durationHint);
  useEffect(() => {
    durationHintRef.current = durationHint;
  }, [durationHint]);

  // One instance for the whole frame. Every route into fullscreen —
  // the bar's button, the `f` shortcut, double-click — goes through it,
  // so they cannot disagree about whether we are fullscreen and, on
  // iPhone, whether we are faking it.
  const fullscreen = useFullscreen({ frameRef: wrapperRef, autoRotateEnabled: playing });

  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    },
    [],
  );

  const isInterrupted = useCallback(() => {
    const player = playerRef.current;
    if (!player) return false;
    try {
      return isAdDuration(player.getDuration(), durationHintRef.current);
    } catch {
      return false;
    }
  }, []);

  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    let pollHandle: ReturnType<typeof setInterval> | null = null;
    setLoadFailed(false);
    lastSavedRef.current = 0;

    const persist = (current: number, duration: number) => {
      if (!Number.isFinite(current) || current <= 0) return;
      if (!Number.isFinite(duration) || duration <= 0) return;
      if (Math.abs(current - lastSavedRef.current) < SAVE_INTERVAL) return;
      lastSavedRef.current = current;
      if (hasProfile) {
        saveWatchProgress(fileId, current, duration).catch(() => {});
      } else {
        saveProgress(fileId, current);
      }
    };

    loadYouTubeIframeApi()
      .then((YT) => {
        if (cancelled) return;
        const mount = mountRef.current;
        const wrapper = wrapperRef.current;
        if (!mount || !wrapper) return;
        const player = new YT.Player(mount, {
          videoId,
          width: "100%",
          height: "100%",
          playerVars: {
            enablejsapi: 1,
            // Litloft draws its own control bar (MediaControls), so the
            // YouTube chrome is turned off. This also removes the
            // title / share overlays. The pause-time related-video
            // overlay and the end screen are NOT removable, and must
            // not be covered — see the overlay's pointer-events below.
            controls: 0,
            // Required once controls are ours: without it iOS hands
            // playback to its native fullscreen player and our controls
            // stop being reachable.
            playsinline: 1,
            disablekb: 1,
            modestbranding: 1,
            rel: 0,
          },
          events: {
            onReady: async ({ target }) => {
              if (cancelled) return;
              playerRef.current = target as YouTubePlayerLike & {
                destroy(): void;
              };
              const mc = createYouTubeController(target, wrapper, {
                isInterrupted,
              });
              controllerRef.current = mc;
              setController(mc);
              onMediaController?.(mc);

              // Citation jump (intelligence Ask `?t=`) wins over the
              // saved-progress resume. The user explicitly clicked a
              // timestamped citation, so silently snapping back to
              // wherever they last left off would be an obvious bug
              // — exactly what was reported. Skip the resume read
              // entirely in that case so we don't even pay the API
              // round-trip.
              if (Number.isFinite(initialTime) && (initialTime ?? 0) > 0) {
                try {
                  target.seekTo(initialTime as number, true);
                  lastSavedRef.current = initialTime as number;
                } catch {
                  // Player may still be warming up; seekTo will be
                  // retried implicitly when the buffer catches up.
                }
              } else {
                try {
                  const saved = hasProfile
                    ? (await getWatchProgress(fileId)).position
                    : getSavedProgress(fileId);
                  if (cancelled) return;
                  const duration = target.getDuration();
                  const upperOk =
                    !Number.isFinite(duration) ||
                    duration <= 0 ||
                    saved < duration - RESUME_THRESHOLD;
                  if (saved > RESUME_THRESHOLD && upperOk) {
                    target.seekTo(saved, true);
                    lastSavedRef.current = saved;
                  }
                } catch {
                  // Fire-and-forget: don't block playback.
                }
              }
              if (cancelled) return;

              pollHandle = setInterval(() => {
                const p = playerRef.current;
                if (!p) return;
                try {
                  const inAd = isInterrupted();
                  setAdActive(inAd);
                  // During an ad the player's clock belongs to the ad,
                  // so persisting it would overwrite the resume point
                  // with an ad offset.
                  if (!inAd) persist(p.getCurrentTime(), p.getDuration());
                } catch {
                  // Player may be transitioning; skip this tick.
                }
              }, POLL_INTERVAL_MS);
            },
            onStateChange: ({ data }) => {
              if (cancelled) return;
              setEnded(data === YT_STATE_ENDED);
              // Buffering counts: the viewer is watching, the player is
              // just catching up.
              setPlaying(data === YT_STATE_PLAYING || data === YT_STATE_BUFFERING);
              if (data === YT_STATE_ENDED) {
                lastSavedRef.current = 0;
                if (hasProfile) {
                  deleteWatchProgress(fileId).catch(() => {});
                } else {
                  clearProgress(fileId);
                }
              }
            },
          },
        });
        playerRef.current = player;
      })
      .catch(() => {
        if (cancelled) return;
        setLoadFailed(true);
      });
    return () => {
      cancelled = true;
      if (pollHandle) clearInterval(pollHandle);
      try {
        const p = playerRef.current;
        // Leaving mid-ad must not stamp the ad's offset onto the
        // resume point, same as the periodic save above.
        if (p && !isInterrupted()) {
          const current = p.getCurrentTime();
          const duration = p.getDuration();
          if (
            Number.isFinite(current) &&
            current > 0 &&
            Number.isFinite(duration) &&
            duration > 0 &&
            Math.abs(current - lastSavedRef.current) >= 1
          ) {
            if (hasProfile) {
              saveWatchProgress(fileId, current, duration).catch(() => {});
            } else {
              saveProgress(fileId, current);
            }
          }
        }
      } catch {
        // Player may already be torn down.
      }
      onMediaController?.(null);
      controllerRef.current = null;
      setController(null);
      setAdActive(false);
      setEnded(false);
      setPlaying(false);
      try {
        playerRef.current?.destroy?.();
      } catch {
        // Player may already be torn down by React unmount.
      }
      playerRef.current = null;
    };
    // initialTime is read inside the onReady closure but intentionally
    // excluded from this effect's dependency list — re-creating the
    // YT.Player just to honour a new ?t= would tear the iframe down
    // mid-playback and reset the watch session. A second effect below
    // reseeks the live player when initialTime changes, so the
    // citation-jump flow still works for same-file ?t= updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, videoId, hasProfile, onMediaController, isInterrupted]);

  // Reseek when ?t= changes on the current iframe. Triggered when the
  // user clicks a second citation for the same .loft file (different
  // timestamp) — the file detail page updates `initialTime` without
  // remounting LoftPlayer, so we need to seek the existing player
  // rather than wait for an onReady that will never fire again.
  useEffect(() => {
    if (!Number.isFinite(initialTime) || (initialTime ?? 0) <= 0) return;
    const p = playerRef.current;
    if (!p) return;
    try {
      p.seekTo(initialTime as number, true);
      lastSavedRef.current = initialTime as number;
    } catch {
      // Player not in a seekable state yet; the onReady handler will
      // pick up `initialTime` on first mount.
    }
  }, [initialTime]);

  useShortcuts("loft-player", tsc("loftPlayer"), [
    { key: "space",      label: tsc("play"),          handler: () => controllerRef.current?.togglePlay() },
    { key: "arrowleft",  label: tsc("seekBack10"),     handler: () => { const mc = controllerRef.current; if (mc) mc.seek(mc.getCurrentTime() - 10); } },
    { key: "arrowright", label: tsc("seekForward10"),  handler: () => { const mc = controllerRef.current; if (mc) mc.seek(mc.getCurrentTime() + 10); } },
    { key: "arrowup",    label: tsc("seekForward60"),  handler: () => { const mc = controllerRef.current; if (mc) mc.seek(mc.getCurrentTime() + 60); } },
    { key: "arrowdown",  label: tsc("seekBack60"),     handler: () => { const mc = controllerRef.current; if (mc) mc.seek(mc.getCurrentTime() - 60); } },
    { key: "m",          label: tsc("mute"),           handler: () => controllerRef.current?.toggleMute() },
    { key: "f",          label: tsc("fullscreen"),     handler: () => fullscreen.toggle() },
  ]);

  if (!videoId || loadFailed) {
    return null;
  }

  // The overlay is the only way to react to clicks on the video: the
  // iframe is cross-origin, so its events never reach us. That makes it
  // a liability during an ad or on the end screen, where YouTube's own
  // UI (skip button, advertiser link, related videos) has to stay
  // clickable — covering those breaks the player and interferes with
  // ads, which the API terms forbid. Stand down in both states.
  const overlayInteractive = !adActive && !ended;

  return (
    <div
      ref={wrapperRef}
      tabIndex={0}
      className={[
        "overflow-hidden bg-black focus:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring",
        fullscreen.isPseudo
          ? // Pinned over the viewport instead of sitting in the page.
            // The frame itself is styled; the iframe is never moved,
            // because re-parenting one reloads it.
            "fixed inset-0 z-50 rounded-none"
          : "relative w-full md:rounded-xl",
      ].join(" ")}
      // The aspect-ratio shim only applies in the page. Filling the
      // viewport is the point of fullscreen, and the YouTube player
      // letterboxes the video itself.
      style={fullscreen.isPseudo ? undefined : { paddingTop: "56.25%" }}
    >
      {/* The YouTube API *replaces* the mount node with its iframe, so
          it gets a stable wrapper of its own. Without it React would be
          holding a reference to a node that is no longer in the
          document, and inserting the siblings below could fail. */}
      <div className="absolute inset-0 [&>iframe]:h-full [&>iframe]:w-full [&>iframe]:border-0">
        <div ref={mountRef} className="h-full w-full" />
      </div>

      <div
        className="absolute inset-0 z-0"
        style={{ pointerEvents: overlayInteractive ? "auto" : "none" }}
        // Pointer-only: on touch, a tap should surface the controls
        // (handled by MediaControls watching this frame) rather than
        // toggle playback, matching every mobile player.
        //
        // The play toggle is deferred because a double-click delivers
        // two clicks before dblclick. Acting on both would pause and
        // resume on the way to fullscreen, which the YouTube player
        // shows as a visible hitch.
        onClick={(e) => {
          if (!e.nativeEvent.detail) return;
          if (!window.matchMedia("(pointer: fine)").matches) return;
          if (clickTimerRef.current) return;
          clickTimerRef.current = setTimeout(() => {
            clickTimerRef.current = null;
            controllerRef.current?.togglePlay();
          }, CLICK_RESOLVE_MS);
        }}
        onDoubleClick={() => {
          if (clickTimerRef.current) {
            clearTimeout(clickTimerRef.current);
            clickTimerRef.current = null;
          }
          fullscreen.toggle();
        }}
      />

      <MediaControls
        mc={controller}
        frameRef={wrapperRef}
        durationHint={durationHint}
        fullscreen={fullscreen}
        isPseudoFullscreen={fullscreen.isPseudo}
      />
    </div>
  );
}
