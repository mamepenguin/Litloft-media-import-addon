"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { getWatchProgress, saveWatchProgress } from "@/lib/api";
import { getSavedProgress, saveProgress } from "@/lib/recentlyPlayed";
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
import { useYouTubeUiPreference } from "./useYouTubeUiPreference";
import { PlayerUiToggle } from "./PlayerUiToggle";

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
  onEnded,
}: LoftEmbedProps) {
  const videoId = extractYouTubeId(url);
  const wrapperRef = useRef<HTMLDivElement>(null);
  // The API replaces whatever node it is given with an iframe, so the
  // node cannot be one React owns — React would keep trying to manage
  // something no longer in the document. It owns this host instead, and
  // the mount inside it is made and thrown away by the effect.
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<
    (YouTubePlayerLike & { destroy(): void }) | null
  >(null);
  const controllerRef = useRef<MediaController | null>(null);
  const lastSavedRef = useRef(0);
  const [loadFailed, setLoadFailed] = useState(false);
  // Held in state (not just the ref) so the control bar renders as soon
  // as the player is ready — and tagged with the UI it was built for.
  //
  // Without that tag, switching UI hands the freshly mounted control bar
  // the *previous* player: state updates land after the render that
  // mounts it, so for one commit this would still point at the player
  // being destroyed in that same commit. The bar's mount effect then
  // applies the saved playback rate to it, and the call lands inside a
  // destroyed widget ("null is not an object (evaluating 'this.g.src')").
  const [session, setSession] = useState<{
    mc: MediaController;
    youtubeUi: boolean;
  } | null>(null);
  const [adActive, setAdActive] = useState(false);
  const [ended, setEnded] = useState(false);
  const [playing, setPlaying] = useState(false);
  const tsc = useTranslations("shortcuts");
  const tmi = useTranslations("mediaImport.player");
  const { nickname } = useProfile();
  const hasProfile = nickname !== null;

  // Read through a ref so the detector handed to the controller always
  // sees the current hint without rebuilding the player.
  const durationHintRef = useRef(durationHint);
  useEffect(() => {
    durationHintRef.current = durationHint;
  }, [durationHint]);

  // Same reason as durationHintRef: a caller that passes an inline
  // arrow would otherwise tear the iframe down and restart the watch
  // session on every one of its renders.
  const onEndedRef = useRef(onEnded);
  useEffect(() => {
    onEndedRef.current = onEnded;
  }, [onEnded]);

  // True while a long press is holding the speed boost. Fullscreen has
  // to stop treating downward travel as a dismiss for the duration, or
  // the drift that comes with a planted finger closes the frame.
  const [boosting, setBoosting] = useState(false);

  // Which player UI is on screen. Switching rebuilds the iframe, since
  // playerVars are read once at construction and never again.
  const [youtubeUi, setYoutubeUi] = useYouTubeUiPreference();
  // Carries the playhead across that rebuild. The periodic save has a
  // five-second threshold, so it cannot be relied on to hold the exact
  // position at the moment of a switch.
  const resumeAtRef = useRef<number | null>(null);

  // One instance for the whole frame. Every route into fullscreen —
  // the bar's button, the `f` shortcut, double-click — goes through it,
  // so they cannot disagree about whether we are fullscreen and, on
  // iPhone, whether we are faking it.
  const fullscreen = useFullscreen({
    frameRef: wrapperRef,
    autoRotateEnabled: playing,
    suppressSwipe: boosting,
  });

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
        saveProgress(fileId, current, duration);
      }
    };

    loadYouTubeIframeApi()
      .then((YT) => {
        if (cancelled) return;
        const host = hostRef.current;
        const wrapper = wrapperRef.current;
        if (!host || !wrapper) return;
        // A fresh mount every time. On a rebuild the previous one is
        // long gone — replaced by the iframe that has since been
        // destroyed — so reusing it would hand the API a detached node.
        host.replaceChildren();
        const mount = document.createElement("div");
        mount.className = "h-full w-full";
        host.appendChild(mount);
        const player = new YT.Player(mount, {
          videoId,
          width: "100%",
          height: "100%",
          playerVars: {
            enablejsapi: 1,
            // The one setting that decides which UI is on screen.
            // Litloft draws its own control bar (MediaControls) at 0;
            // at 1 the player draws its own and ours stands down. The
            // pause-time related-video overlay and the end screen are
            // NOT removable either way, and must not be covered — see
            // the gesture overlay's interactive gate.
            controls: youtubeUi ? 1 : 0,
            // Only meaningful while the controls are ours: without it
            // iOS refuses inline playback and hands the video to its
            // own full-screen player, where our controls cannot be
            // reached. Dropping it in YouTube-UI mode is deliberate —
            // that hand-off is what puts a Picture-in-Picture button
            // on screen, which no API of ours can produce.
            ...(youtubeUi ? {} : { playsinline: 1 }),
            disablekb: youtubeUi ? 0 : 1,
            modestbranding: 1,
            rel: 0,
            // Start with captions off rather than inheriting whatever
            // the viewer's YouTube account has set. Our own toggle is
            // the only thing that should turn them on here, and a
            // player that keeps re-enabling them from an account
            // preference fights that toggle on every seek.
            cc_load_policy: 0,
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
              setSession({ mc, youtubeUi });
              onMediaController?.(mc);

              // Citation jump (intelligence Ask `?t=`) wins over the
              // saved-progress resume. The user explicitly clicked a
              // timestamped citation, so silently snapping back to
              // wherever they last left off would be an obvious bug
              // — exactly what was reported. Skip the resume read
              // entirely in that case so we don't even pay the API
              // round-trip.
              // A UI switch outranks both the citation jump and the
              // saved progress: it is the most recent thing the viewer
              // did, and they expect to carry on from where they were.
              const resumeAt = resumeAtRef.current;
              resumeAtRef.current = null;
              if (resumeAt != null && resumeAt > 0) {
                try {
                  target.seekTo(resumeAt, true);
                  lastSavedRef.current = resumeAt;
                  target.playVideo();
                } catch {
                  // Player may still be warming up.
                }
              } else if (Number.isFinite(initialTime) && (initialTime ?? 0) > 0) {
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
                // ENDED fires for a pre-roll too, and there is no state
                // flag that tells the two apart — the same duration
                // mismatch that guards the periodic save is the only
                // signal. Persisting here without it would stamp the
                // ad's length onto the video as a completed watch.
                if (isInterrupted()) return;
                const p = playerRef.current;
                let current = NaN;
                let duration = NaN;
                try {
                  current = p?.getCurrentTime() ?? NaN;
                  duration = p?.getDuration() ?? NaN;
                } catch {
                  // Player may already be tearing down.
                }
                // Completion is kept, not erased: the history row is
                // what distinguishes "watched to the end" from "never
                // opened", and the 90% gate keeps it out of continue
                // watching anyway. Spec
                // 2026-08-10-media-import-watch-surface.md §4.2.
                if (Number.isFinite(duration) && duration > 0) {
                  const position =
                    Number.isFinite(current) && current > 0
                      ? current
                      : duration;
                  lastSavedRef.current = position;
                  if (hasProfile) {
                    saveWatchProgress(fileId, position, duration).catch(
                      () => {},
                    );
                  } else {
                    saveProgress(fileId, position, duration);
                  }
                }
                onEndedRef.current?.();
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
        // Where to pick up if this teardown is a UI switch rather than
        // leaving the page. Harmless otherwise: nothing reads it until
        // a player is built again for the same file.
        const live = playerRef.current;
        if (live && !isInterrupted()) {
          const at = live.getCurrentTime();
          if (Number.isFinite(at) && at > 0) resumeAtRef.current = at;
        }
      } catch {
        // Player may already be torn down.
      }
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
              saveProgress(fileId, current, duration);
            }
          }
        }
      } catch {
        // Player may already be torn down.
      }
      onMediaController?.(null);
      controllerRef.current = null;
      setSession(null);
      setAdActive(false);
      setEnded(false);
      setPlaying(false);
      try {
        playerRef.current?.destroy?.();
      } catch {
        // Player may already be torn down by React unmount.
      }
      playerRef.current = null;
      // destroy() takes the iframe with it, but not always — and a
      // leftover would be a second player on the next build.
      hostRef.current?.replaceChildren();
    };
    // initialTime is read inside the onReady closure but intentionally
    // excluded from this effect's dependency list — re-creating the
    // YT.Player just to honour a new ?t= would tear the iframe down
    // mid-playback and reset the watch session. A second effect below
    // reseeks the live player when initialTime changes, so the
    // citation-jump flow still works for same-file ?t= updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileId, videoId, hasProfile, onMediaController, isInterrupted, youtubeUi]);

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

  // The gesture overlay (drawn by MediaControls) is the only way to
  // react to input on the video: the iframe is cross-origin, so its
  // events never reach us. That makes it a liability during an ad or on
  // the end screen, where YouTube's own UI (skip button, advertiser
  // link, related videos) has to stay clickable — covering those breaks
  // the player and interferes with ads, which the API terms forbid.
  // Stand down in both states.
  const gesturesInteractive = !adActive && !ended;

  // Only ever the player belonging to the UI currently on screen. A
  // stale one is the same as none: it is already destroyed, or about to
  // be, and anything sent to it crashes inside the widget.
  const activeMc = session && session.youtubeUi === youtubeUi ? session.mc : null;

  return (
    <div className="w-full">
    <div
      ref={wrapperRef}
      data-testid="player-frame"
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
      {/* React owns this host and nothing inside it. The API replaces
          the node it is given with an iframe, so anything React thought
          it had put there would be gone from the document — and React
          would fail trying to update or remove it on the next render,
          which is exactly what broke switching player UI. */}
      <div
        ref={hostRef}
        className="absolute inset-0 [&>iframe]:h-full [&>iframe]:w-full [&>iframe]:border-0"
      />

      {/* In YouTube-UI mode the player draws its own controls, and ours
          would sit on top of them — including the gesture overlay,
          which would swallow every touch meant for them. */}
      {!youtubeUi && activeMc && (
        <MediaControls
          mc={activeMc}
          frameRef={wrapperRef}
          durationHint={durationHint}
          fullscreen={fullscreen}
          isPseudoFullscreen={fullscreen.isPseudo}
          interactive={gesturesInteractive}
          onBoostingChange={setBoosting}
          settingsExtra={
            <PlayerUiToggle youtubeUi={youtubeUi} onChange={setYoutubeUi} />
          }
        />
      )}
    </div>

      {/* The settings sheet goes with our controls, so the way back has
          to live outside the frame. */}
      {youtubeUi && (
        <div className="mt-2 px-1">
          <button
            type="button"
            className="rounded-2xl text-sm text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
            onClick={() => setYoutubeUi(false)}
          >
            {tmi("backToLitloftPlayer")}
          </button>
        </div>
      )}
    </div>
  );
}
