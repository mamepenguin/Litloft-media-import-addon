"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ExternalLink } from "lucide-react";
import {
  createYouTubeController,
  type MediaController,
  type YouTubePlayerLike,
} from "@/lib/mediaController";
import { usePlaybackProgress } from "@/lib/playbackProgress";
import { setupMediaSession } from "@/lib/mediaSession";
import { getMediaClockSnapshot, subscribeMediaClock } from "@/lib/mediaClock";
import { useShortcuts } from "@/hooks/useShortcuts";
import type { LoftEmbedProps } from "@/components/loft/types";
import MediaControls from "@/components/player/MediaControls";
import { useFullscreen } from "@/components/player/hooks/useFullscreen";
import { loadYouTubeIframeApi } from "./loadYouTubeIframeApi";
import { useYouTubeUiPreference } from "./useYouTubeUiPreference";
import { PlayerUiToggle } from "./PlayerUiToggle";

const YT_STATE_ENDED = 0;
const YT_STATE_PLAYING = 1;
const YT_STATE_BUFFERING = 3;

// The video owner disallows embedded playback. Both codes mean the same
// thing (150 is a legacy duplicate of 101) and neither is affected by
// playerVars.controls — the iframe is refused regardless of which skin
// it would have drawn, so there is no working player to fall back to
// inside the embed at all.
const YT_ERROR_EMBED_NOT_ALLOWED = new Set([101, 150]);

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
  mediaSessionMetadata,
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
  const [loadFailed, setLoadFailed] = useState(false);
  // The video's owner disallows embedding (YT error 101/150). No
  // playerVars combination recovers this — it's rejected at the iframe
  // level — so once set, the fallback link-out replaces the frame
  // entirely instead of the usual custom/YouTube control skins.
  const [embedRestricted, setEmbedRestricted] = useState(false);
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
  //
  // Tagged with the file it belongs to. The teardown that records it
  // cannot tell a UI switch from a navigation, so an untagged value
  // would be handed to whatever file was opened next.
  const carriedRef = useRef<{ fileId: string; at: number } | null>(null);
  const carriedAt =
    carriedRef.current?.fileId === fileId ? carriedRef.current.at : undefined;

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

  // Only ever the player belonging to the UI currently on screen. A
  // stale one is the same as none: it is already destroyed, or about to
  // be, and anything sent to it crashes inside the widget.
  const activeMc = session && session.youtubeUi === youtubeUi ? session.mc : null;

  // Watch history — resume, periodic save, completion, teardown save —
  // is core's, shared with native video and audio. What stays here is
  // the part that is genuinely YouTube's: the ad heuristic (injected
  // into the controller, so the hook sees it generically) and carrying
  // the playhead across a deliberate iframe rebuild.
  //
  // The carried position is handed over as `initialTime` because that
  // is exactly its meaning to the hook: an explicit "land here" that
  // outranks stored progress. Read during render rather than through a
  // dependency — the ref is written in the teardown of the previous
  // player, and this render is the one the new controller triggers.
  const { notifyEnded } = usePlaybackProgress({
    mc: activeMc,
    fileId,
    initialTime: carriedAt ?? initialTime,
  });
  // Read through a ref inside the player effect: this identity changes
  // with the controller, and the controller is what that effect builds.
  const notifyEndedRef = useRef(notifyEnded);
  notifyEndedRef.current = notifyEnded;

  useEffect(() => {
    if (!activeMc || !mediaSessionMetadata) return;
    return setupMediaSession(activeMc, mediaSessionMetadata, {
      onNextTrack: () => onEndedRef.current?.(),
    });
  }, [activeMc, mediaSessionMetadata]);

  // The ad flag gates the gesture overlay, which must stand down while
  // YouTube's own skip button needs to be reachable. Subscribing to the
  // clock rather than reading it through useMediaClock keeps the render
  // out of it: only a change of flag re-renders, not every tick.
  useEffect(() => {
    if (!activeMc) {
      setAdActive(false);
      return;
    }
    const sync = () => setAdActive(getMediaClockSnapshot(activeMc).interrupted);
    const unsubscribe = subscribeMediaClock(activeMc, sync);
    sync();
    return unsubscribe;
  }, [activeMc]);

  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    setLoadFailed(false);
    setEmbedRestricted(false);

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

              // Resume is the progress hook's, including the rule that
              // an explicit position outranks stored progress. What it
              // cannot do is carry a UI switch across the rebuild
              // *synchronously*: the hook only reaches this controller
              // on the render that setSession above schedules. Seeking
              // here keeps the switch seamless; the hook then resumes to
              // the same position it was handed as initialTime, so the
              // second seek changes nothing.
              const carried =
                carriedRef.current?.fileId === fileId
                  ? carriedRef.current.at
                  : null;
              if (carried != null && carried > 0) {
                try {
                  target.seekTo(carried, true);
                  target.playVideo();
                } catch {
                  // Player may still be warming up.
                }
              }
            },
            onStateChange: ({ data }) => {
              if (cancelled) return;
              setEnded(data === YT_STATE_ENDED);
              // Buffering counts: the viewer is watching, the player is
              // just catching up.
              setPlaying(data === YT_STATE_PLAYING || data === YT_STATE_BUFFERING);
              if (data === YT_STATE_ENDED) {
                // ENDED fires for a pre-roll too, and there is no state
                // flag that tells the two apart — the duration mismatch
                // is the only signal. The guard has to stay here rather
                // than move into the hook: the hook can refuse to write
                // a completion, but onEnded is a lifecycle callback the
                // ad must not reach either, or finishing a pre-roll
                // advances the Collection to the next video.
                if (isInterrupted()) return;
                notifyEndedRef.current();
                onEndedRef.current?.();
              }
            },
            onError: ({ data }) => {
              if (cancelled) return;
              if (!YT_ERROR_EMBED_NOT_ALLOWED.has(data)) return;
              setEmbedRestricted(true);
              onMediaController?.(null);
              controllerRef.current = null;
              setSession(null);
              try {
                playerRef.current?.destroy?.();
              } catch {
                // Already gone.
              }
              playerRef.current = null;
              hostRef.current?.replaceChildren();
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
      try {
        // Where to pick up if this teardown is a UI switch rather than
        // leaving the page. The progress hook's own teardown save is
        // five-second-granular by design; a switch has to land on the
        // exact frame the viewer was looking at.
        const live = playerRef.current;
        if (live && !isInterrupted()) {
          const at = live.getCurrentTime();
          if (Number.isFinite(at) && at > 0) {
            carriedRef.current = { fileId, at };
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
    // Deliberately not keyed on initialTime: re-creating the YT.Player
    // to honour a new ?t= would tear the iframe down mid-playback and
    // reset the watch session. The effect below reseeks the live player
    // instead, so same-file citation jumps still work. It no longer
    // needs an exhaustive-deps exemption — onReady stopped reading
    // initialTime when resume moved into the progress hook.
  }, [fileId, videoId, onMediaController, isInterrupted, youtubeUi]);

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

  if (embedRestricted) {
    return (
      <div className="w-full">
        <div
          className="relative w-full overflow-hidden bg-black md:rounded-xl"
          style={{ paddingTop: "56.25%" }}
        >
          <img
            src={`/api/files/${fileId}/thumbnail`}
            alt=""
            className="absolute inset-0 h-full w-full object-cover opacity-40"
          />
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-4 text-center text-white">
            <p className="text-sm">{tmi("embedRestricted")}</p>
            <a
              href={`https://www.youtube.com/watch?v=${videoId}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-2xl bg-white/20 px-4 py-2 text-sm font-medium hover:bg-white/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring"
            >
              <ExternalLink size={16} aria-hidden="true" />
              {tmi("watchOnYouTube")}
            </a>
          </div>
        </div>
      </div>
    );
  }

  // The gesture overlay (drawn by MediaControls) is the only way to
  // react to input on the video: the iframe is cross-origin, so its
  // events never reach us. That makes it a liability during an ad or on
  // the end screen, where YouTube's own UI (skip button, advertiser
  // link, related videos) has to stay clickable — covering those breaks
  // the player and interferes with ads, which the API terms forbid.
  // Stand down in both states.
  const gesturesInteractive = !adActive && !ended;

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
