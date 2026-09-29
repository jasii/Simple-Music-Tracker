import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import AudioPlayer, { RHAP_UI } from "react-h5-audio-player";
import "react-h5-audio-player/lib/styles.css";
import {
  LuBookmark,
  LuChevronsRight,
  LuListMusic,
  LuLoaderCircle,
  LuPause,
  LuPlay,
  LuShuffle,
  LuThumbsDown,
  LuThumbsUp,
  LuUserPlus,
  LuVolume2,
  LuX,
} from "react-icons/lu";
import { toast } from "sonner";
import { api, art } from "../api";
import { explainNoAudio, pausedMessage } from "./PrewarmNotice";
import { ArtistLink } from "./ArtistLink";
import { ServiceIcon } from "./ServiceIcon";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { emit } from "../lib/events";
import { continueOn, heardKey, heardReleases } from "../lib/playPrefs";
import { cn } from "../lib/utils";

/** The record a track was played for: what thumbs up/down and "heard" act on. */
export type ReleaseRef = {
  artist: string;
  album?: string | null;
  mbid?: string | null;
  date?: string | null;
  image?: string | null;
};

// Played this long, a release counts as heard.
const HEARD_AFTER_S = 15;

// One playable track: where the audio comes from plus what to call it. `note`
// is the line under the title -- "from Navidrome", "30-second sample".
export type PreviewTrack = {
  title: string;
  artist?: string | null;
  /** Set when the artist is already in the library, so the name links straight there. */
  artistId?: number | null;
  album?: string | null;
  /** The track's own page (Last.fm, or whichever catalogue named it). */
  url?: string | null;
  note?: string | null;
  /** Where the note points: the song on Navidrome, the store page, the video. */
  noteUrl?: string | null;
  /** The source's own mark, shown before the note (see ServiceIcon). */
  noteIcon?: string | null;
  /** Which row of a list this came from, so the list can mark what's playing. */
  group?: string | null;
  /** A line about the whole queue: why these are the tracks playing. */
  queueNote?: string | null;
  /** Cover art, for the lock screen. */
  image?: string | null;
  /** The release this was played for (Discover and the lists like it). */
  release?: ReleaseRef | null;
  src: string | null | undefined;
};

/**
 * What plays once a queue runs out: the next queue, and what follows that.
 * `load` resolves null when there's nothing more, or when continuing is
 * switched off and the person didn't ask (`asked`) for the next one.
 */
export type QueueContinuation = {
  /** The row expected next, for the queue view. */
  label?: string | null;
  load: (asked?: boolean) => Promise<{
    queue: PreviewTrack[];
    next: QueueContinuation | null;
  } | null>;
};

type PlayerState = {
  current: PreviewTrack | null;
  playing: boolean;
  /** Is this the track the bar is currently loaded with? */
  isCurrent: (src: string | null | undefined) => boolean;
  /** Same question by name, for a track whose source isn't resolved yet. */
  isCurrentTrack: (artist: string | null | undefined, title: string) => boolean;
  /**
   * Play track *index* of *queue*, or pause/resume it if it's already loaded.
   * *next* is what to play when the queue runs out (a list that keeps going).
   */
  toggle: (queue: PreviewTrack[], index: number, next?: QueueContinuation | null) => void;
  close: () => void;
};

const NOOP: PlayerState = {
  current: null,
  playing: false,
  isCurrent: () => false,
  isCurrentTrack: () => false,
  toggle: () => {},
  close: () => {},
};

const PlayerContext = createContext<PlayerState>(NOOP);

// The track's own page: whatever the source named, else Last.fm's page for it
// (which exists for anything Last.fm knows, and it named most of these).
function trackHref(track: PreviewTrack) {
  if (track.url) return track.url;
  if (!track.artist) return null;
  return (
    "https://www.last.fm/music/" +
    encodeURIComponent(track.artist) +
    "/_/" +
    encodeURIComponent(track.title)
  );
}

// The volume the player opens at, remembered per browser: the library starts
// every player at full, which is not where anyone left it.
const VOLUME_KEY = "smtPlayerVolume";
const DEFAULT_VOLUME = 0.6;

function storedVolume() {
  try {
    const raw = localStorage.getItem(VOLUME_KEY);
    const level = raw === null ? NaN : Number(raw);
    return Number.isFinite(level) && level > 0 && level <= 1 ? level : DEFAULT_VOLUME;
  } catch {
    // Private windows and blocked site data both throw; the default is fine.
    return DEFAULT_VOLUME;
  }
}

function rememberVolume(level: number) {
  // Muting drops the volume to 0 and the library restores it on unmute, so a
  // zero is a mute, not a preference worth keeping.
  if (!(level > 0)) return;
  try {
    localStorage.setItem(VOLUME_KEY, String(level));
  } catch {
    /* nothing to do: the player still works, it just won't remember */
  }
}

const SHUFFLE_KEY = "smtPlayerShuffle";

function storedShuffle() {
  try {
    return localStorage.getItem(SHUFFLE_KEY) === "true";
  } catch {
    return false;
  }
}

function rememberShuffle(on: boolean) {
  try {
    localStorage.setItem(SHUFFLE_KEY, on ? "true" : "false");
  } catch {
    /* nothing to do: the player still works, it just won't remember */
  }
}

/** The queue's positions, the given one first and the rest in a jumble. */
function shuffledOrder(length: number, first: number) {
  const rest = Array.from({ length }, (_, i) => i).filter((i) => i !== first);
  for (let i = rest.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [rest[i], rest[j]] = [rest[j]!, rest[i]!];
  }
  return [first, ...rest];
}

function albumHref(artist: string, album: string) {
  return (
    "/album?artist=" + encodeURIComponent(artist) + "&title=" + encodeURIComponent(album)
  );
}

// Every sample in the app plays through this one bar: press play on an artist's
// top tracks or an album's tracklist and it docks at the bottom, survives page
// changes (the provider lives in the layout, above the router outlet) and skips
// through whichever list the track came from.
export function PreviewPlayerProvider({ children }: { children: React.ReactNode }) {
  const [queue, setQueue] = useState<PreviewTrack[]>([]);
  // The positions to walk, in order: identity normally, jumbled when shuffle
  // is on. `pos` indexes this rather than the queue, so shuffling is a
  // reordering and switching it off puts the list back as it was.
  const [order, setOrder] = useState<number[]>([]);
  const [pos, setPos] = useState(0);
  const [shuffle, setShuffle] = useState(storedShuffle);
  const [playing, setPlaying] = useState(false);
  const ref = useRef<AudioPlayer>(null);
  // What follows the queue, when the list it came from keeps going. A ref: it
  // is only ever called, never rendered -- `hasNext` is the rendered half.
  const nextQueue = useRef<QueueContinuation | null>(null);
  const [hasNext, setHasNext] = useState(false);
  const [nextLabel, setNextLabel] = useState<string | null>(null);
  // Releases already reported heard this session.
  const reported = useRef<Set<string>>(new Set());
  // Fetching the next queue. The token drops an answer that lands after the
  // person has picked something else to play.
  const [advancing, setAdvancing] = useState(false);
  const advanceToken = useRef(0);
  // The level the player is set to, seeded from the last session. A ref, not
  // state: the prop only seeds the audio element, so re-rendering on every
  // slider movement would fight the person dragging it -- but the value has to
  // be *current*, because the player is unmounted between tracks (its src goes
  // away while the next one resolves) and mounts again from this.
  const volumeLevel = useRef(storedVolume());
  // Muted is remembered for the session only, and separately from the level:
  // the element is rebuilt between tracks, so without this a skip would
  // quietly unmute. Not persisted -- muting is a moment, not a preference.
  const muted = useRef(false);

  // Filled in for a track that arrived without a src: either an in-app stream
  // URL the server found, or the video Last.fm plays it from.
  const [resolvedSrc, setResolvedSrc] = useState<string | null>(null);
  const [resolvedNote, setResolvedNote] = useState<string | null>(null);
  // The credit under the title is a link when the source has a page for the
  // song: the album on Navidrome, the track on iTunes, the video itself.
  const [resolvedNoteUrl, setResolvedNoteUrl] = useState<string | null>(null);
  const [resolvedNoteIcon, setResolvedNoteIcon] = useState<string | null>(null);
  const [videoId, setVideoId] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  const current = queue[order[pos] ?? 0] ?? null;
  const src = current?.src || resolvedSrc;
  // What's loaded, readable from the toggle callback without re-creating it.
  // Comparing against the audio element's own src would never match: it
  // reports the resolved absolute URL, while a track's src is a relative path.
  const currentKey = useRef<string | null>(null);
  currentKey.current = current ? `${current.artist ?? ""}|${current.title}` : null;
  // The same song reached from another row of a list (an artist's top tracks
  // behind two of their releases) is a new start there, not a pause here.
  const currentGroup = useRef<string | null>(null);
  currentGroup.current = current?.group ?? null;

  // Whatever the last track resolved to has to go with it: a queue of tracks
  // that each resolve on demand (a playlist, an artist's top tracks) would
  // otherwise keep playing the first one, because a stale resolvedSrc reads
  // as "this track already has audio".
  const forgetResolved = useCallback(() => {
    setResolvedSrc(null);
    setResolvedNote(null);
    setResolvedNoteUrl(null);
    setResolvedNoteIcon(null);
    setVideoId(null);
  }, []);

  const load = useCallback(
    (tracks: PreviewTrack[], index: number, then: QueueContinuation | null) => {
      const walk = shuffle
        ? shuffledOrder(tracks.length, index)
        : Array.from({ length: tracks.length }, (_, i) => i);
      setQueue(tracks);
      setOrder(walk);
      setPos(walk.indexOf(index));
      forgetResolved();
      nextQueue.current = then;
      setHasNext(!!then);
      setNextLabel(then?.label ?? null);
    },
    [forgetResolved, shuffle],
  );

  const toggle = useCallback(
    (tracks: PreviewTrack[], index: number, then?: QueueContinuation | null) => {
      const track = tracks[index];
      if (!track) return;
      // Same track: this is a pause/resume, not a reload.
      if (
        `${track.artist ?? ""}|${track.title}` === currentKey.current &&
        (track.group ?? null) === currentGroup.current
      ) {
        const el = ref.current?.audio?.current;
        if (!el) return;
        if (el.paused) el.play().catch(() => setPlaying(false));
        else el.pause();
        return;
      }
      advanceToken.current += 1;
      setAdvancing(false);
      load(tracks, index, then ?? null);
    },
    [load],
  );

  // The queue ran out, or the person asked for the next one (`asked`): move on
  // to whatever the list says comes after it, if anything does.
  const advance = useCallback((asked = false) => {
    const then = nextQueue.current;
    // A queue that ran out with continuing off stays done -- but keeps its
    // place in the list, for a "next artist" pressed later.
    if (!then || (!asked && !continueOn())) return;
    const token = ++advanceToken.current;
    setAdvancing(true);
    then
      .load(asked)
      .then((found) => {
        if (token !== advanceToken.current) return;
        if (!found || !found.queue.length) {
          // Nothing further down the list plays.
          if (asked) toast.info("Nothing further down the list to play.");
          nextQueue.current = null;
          setHasNext(false);
          setNextLabel(null);
          return;
        }
        load(found.queue, 0, found.next);
      })
      .catch(() => {
        if (token === advanceToken.current) toast.error("Could not load the next one.");
      })
      .finally(() => {
        if (token === advanceToken.current) setAdvancing(false);
      });
  }, [load]);

  // Shuffling keeps the track that's playing and reorders what follows;
  // switching it off restores the queue's own order from where you are.
  const toggleShuffle = useCallback(() => {
    setShuffle((on) => {
      const next = !on;
      rememberShuffle(next);
      setOrder((current) => {
        const at = current[pos] ?? 0;
        if (next) {
          setPos(0);
          return shuffledOrder(queue.length, at);
        }
        setPos(at);
        return Array.from({ length: queue.length }, (_, i) => i);
      });
      return next;
    });
  }, [pos, queue.length]);

  // A track with no src of its own: ask the server where it can come from --
  // a library copy, a sample, or the video Last.fm's own player uses.
  useEffect(() => {
    if (!current || current.src || resolvedSrc || videoId) return;
    let dropped = false;
    setResolving(true);
    api
      .trackSource(current.artist || "", current.title, current.url)
      .then((r) => {
        if (dropped) return;
        // A video answer now usually carries a stream too: the server pulls
        // the audio out of it, so it plays in this player with the same
        // volume and the same pause button as everything else. The embed is
        // only for when that isn't possible.
        if (r.kind === "youtube" && r.stream) {
          setResolvedSrc(r.stream);
          setResolvedNote(r.label ?? "video audio");
          setResolvedNoteUrl(r.source_url ?? null);
          setResolvedNoteIcon(r.icon ?? null);
        } else if (r.kind === "youtube" && r.youtube_id) {
          setVideoId(r.youtube_id);
          setResolvedNote("video");
          setResolvedNoteUrl(r.source_url ?? null);
          setResolvedNoteIcon(r.icon ?? null);
        } else if (r.stream) {
          setResolvedSrc(r.stream);
          setResolvedNote(r.label ?? null);
          setResolvedNoteUrl(r.source_url ?? null);
          setResolvedNoteIcon(r.icon ?? null);
        } else if (r.paused) {
          toast.info(pausedMessage(current.title));
        } else {
          toast.error(`No audio found for ${current.title}.`);
        }
      })
      .catch(() => {
        if (!dropped) toast.error(`Could not look up ${current.title}.`);
      })
      .finally(() => {
        if (!dropped) setResolving(false);
      });
    return () => {
      dropped = true;
    };
  }, [current, resolvedSrc, videoId]);

  const step = useCallback(
    (by: number) => {
      setPos((i) => {
        const next = i + by;
        if (next < 0 || next >= order.length) return i;
        // The new track resolves its own audio; the old one's must not stand.
        forgetResolved();
        return next;
      });
    },
    [order.length, forgetResolved],
  );

  // Next track, or at the end of the queue the next row of the list.
  const goNext = useCallback(
    (asked = true) => {
      if (pos + 1 < order.length) step(1);
      else advance(asked);
    },
    [pos, order.length, step, advance],
  );

  const playPause = useCallback(() => {
    const el = ref.current?.audio?.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => setPlaying(false));
    else el.pause();
  }, []);

  // Jump to a track from the queue view.
  const jump = useCallback(
    (to: number) => {
      if (to === pos) return;
      forgetResolved();
      setPos(to);
    },
    [pos, forgetResolved],
  );

  // Played long enough (or to the end): the release counts as heard -- its
  // Discover row dims, and the walk down the list skips it next time.
  const reportHeard = useCallback(
    (force = false) => {
      const rel = current?.release;
      if (!rel?.artist) return;
      const key = heardKey(rel.artist, rel.album);
      if (reported.current.has(key)) return;
      const el = ref.current?.audio?.current;
      if (!force && (!el || el.currentTime < HEARD_AFTER_S)) return;
      reported.current.add(key);
      heardReleases.add(key);
      api.markHeard(rel.artist, rel.album).catch(() => {});
      emit("heard", { artist: rel.artist, album: rel.album, heard: true });
    },
    [current],
  );

  // Thumbs up: keep it, one way or the other.
  function saveRelease(rel: ReleaseRef) {
    if (!rel.album) return;
    api
      .wishlistAdd({ artist: rel.artist, album: rel.album, mbid: rel.mbid,
                     release_date: rel.date, image: rel.image })
      .then((r) => {
        if (r.error) throw new Error(r.error);
        toast.success(`Saved "${rel.album}" for later`);
        emit("saved", { artist: rel.artist, album: rel.album, saved: true });
      })
      .catch(() => toast.error("Could not save it."));
  }
  function followArtist(rel: ReleaseRef) {
    api
      .trackByName(rel.artist, "subscribed")
      .then((r: any) => {
        if (r?.error) throw new Error(r.error);
        toast.success(`Following ${rel.artist}`);
        emit("followed", { artist: rel.artist, album: rel.album, following: true });
      })
      .catch(() => toast.error(`Could not follow ${rel.artist}.`));
  }
  // Thumbs down: never show this release on Discover again, and move on.
  function dislike(rel: ReleaseRef) {
    if (!rel.album) return;
    const album = rel.album;
    api
      .addDiscoverIgnore(rel.artist, album)
      .then((r: any) => {
        emit("ignored", { artist: rel.artist, album });
        const rule = (r?.ignores ?? []).find(
          (i: { artist: string; album: string | null }) =>
            i.artist.toLowerCase() === rel.artist.toLowerCase() &&
            (i.album || "").toLowerCase() === album.toLowerCase(),
        );
        toast(`Ignored "${album}"`, {
          action: rule
            ? {
                label: "Undo",
                onClick: () => {
                  api.removeDiscoverIgnore(rule.id).then(() =>
                    emit("unignored", { artist: rel.artist, album }),
                  );
                },
              }
            : undefined,
        });
        if (nextQueue.current) advance(true);
        else ref.current?.audio?.current?.pause();
      })
      .catch(() => toast.error("Could not ignore it."));
  }

  // The lock screen, the headphones' buttons, the media keys.
  useEffect(() => {
    const session = typeof navigator !== "undefined" ? navigator.mediaSession : undefined;
    if (!session) return;
    if (!current) {
      session.metadata = null;
      return;
    }
    const cover = current.image || current.release?.image;
    try {
      session.metadata = new MediaMetadata({
        title: current.title,
        artist: current.artist ?? "",
        album: current.album ?? "",
        artwork: cover ? [{ src: new URL(art(cover), window.location.origin).href }] : [],
      });
    } catch {
      /* an older browser without MediaMetadata: the buttons still work */
    }
  }, [current]);
  useEffect(() => {
    const session = typeof navigator !== "undefined" ? navigator.mediaSession : undefined;
    if (!session) return;
    session.playbackState = !current ? "none" : playing ? "playing" : "paused";
  }, [current, playing]);
  useEffect(() => {
    const session = typeof navigator !== "undefined" ? navigator.mediaSession : undefined;
    if (!session) return;
    const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
      ["play", () => { ref.current?.audio?.current?.play().catch(() => {}); }],
      ["pause", () => ref.current?.audio?.current?.pause()],
      ["previoustrack", () => step(-1)],
      ["nexttrack", () => goNext(true)],
    ];
    for (const [action, handler] of handlers) {
      try { session.setActionHandler(action, handler); } catch { /* unsupported action */ }
    }
    return () => {
      for (const [action] of handlers) {
        try { session.setActionHandler(action, null); } catch { /* unsupported action */ }
      }
    };
  }, [step, goNext]);

  // Keyboard: space plays/pauses, n / p next / previous track, shift+N the
  // next artist. Not while typing, and not on a focused control (it has its
  // own meaning for space there).
  useEffect(() => {
    if (!current) return;
    function onKey(e: KeyboardEvent) {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) ||
          target.closest("[role=dialog], [role=menu], [role=listbox], .rhap_container"))
      ) {
        return;
      }
      if (e.key === " ") {
        if (target && /^(BUTTON|A)$/.test(target.tagName)) return;
        e.preventDefault();
        playPause();
      } else if (e.key === "n") {
        goNext(true);
      } else if (e.key === "p") {
        step(-1);
      } else if (e.key === "N" && nextQueue.current) {
        advance(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, goNext, step, advance, playPause]);

  const close = useCallback(() => {
    ref.current?.audio?.current?.pause();
    advanceToken.current += 1;
    setAdvancing(false);
    nextQueue.current = null;
    setHasNext(false);
    setNextLabel(null);
    setQueue([]);
    setOrder([]);
    setPos(0);
    setPlaying(false);
    forgetResolved();
  }, [forgetResolved]);

  const state = useMemo<PlayerState>(
    () => ({
      current,
      playing,
      isCurrent: (trackSrc) => !!trackSrc && !!current && current.src === trackSrc,
      isCurrentTrack: (artist, title) =>
        `${artist ?? ""}|${title}` === currentKey.current,
      toggle,
      close,
    }),
    [current, playing, toggle, close],
  );

  return (
    <PlayerContext.Provider value={state}>
      {children}
      {/* Keeps the page's last rows clear of the docked bar. */}
      {current && <div aria-hidden className="h-28" />}
      {current && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 px-4 py-2 backdrop-blur">
          <div className="mx-auto flex max-w-[60rem] flex-wrap items-center gap-x-3 gap-y-1">
            <div className="min-w-[8rem] flex-1">
              <p className="truncate text-sm font-medium">
                {trackHref(current) ? (
                  <a
                    href={trackHref(current)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:underline"
                  >
                    {current.title}
                  </a>
                ) : (
                  current.title
                )}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {/* The artist and the album stay inside the app; the track
                    title is the one link that leaves it. */}
                {current.artist && (
                  <ArtistLink name={current.artist} artistId={current.artistId} />
                )}
                {current.artist && current.album ? " - " : ""}
                {current.album && current.artist ? (
                  <RouterLink
                    to={albumHref(current.artist, current.album)}
                    className="hover:underline"
                  >
                    {current.album}
                  </RouterLink>
                ) : (
                  current.album
                )}
              </p>
              {(() => {
                const note = current.note || resolvedNote;
                const counted = order.length > 1;
                if (!note && !counted) return null;
                const href = current.noteUrl || resolvedNoteUrl;
                const icon = current.noteIcon || resolvedNoteIcon;
                // Where you are in the queue, then where the audio comes
                // from: "3 of 10 · from Navidrome".
                return (
                  <p className="truncate text-xs text-muted-foreground">
                    {counted ? `${pos + 1} of ${order.length}` : ""}
                    {counted && note ? " · " : ""}
                    {note && (
                      <>
                        {"from "}
                        {icon && (
                          <ServiceIcon
                            name={icon}
                            size={13}
                            className="mr-1 inline-block align-[-2px]"
                          />
                        )}
                        {href ? (
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="underline decoration-dotted hover:decoration-solid"
                            title={`Open ${note}`}
                          >
                            {note}
                          </a>
                        ) : (
                          note
                        )}
                      </>
                    )}
                  </p>
                );
              })()}
              {current.queueNote && (
                <p className="text-xs leading-snug text-amber-600 dark:text-amber-400">
                  {current.queueNote}
                </p>
              )}
            </div>
            {((resolving && !src && !videoId) || advancing) && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <LuLoaderCircle aria-hidden className="animate-spin" />
                {advancing ? "finding the next one" : "finding audio"}
              </span>
            )}
            {/* No sample and no copy of our own: play the same video Last.fm
                would have played, here rather than there. */}
            {videoId && (
              <iframe
                key={videoId}
                title={`${current.title} video`}
                src={`https://www.youtube.com/embed/${videoId}?autoplay=1&rel=0`}
                allow="autoplay; encrypted-media; picture-in-picture"
                allowFullScreen
                className="h-[84px] w-[150px] flex-none rounded border-0 bg-black"
              />
            )}
            {src && (
            <AudioPlayer
              ref={ref}
              src={src}
              autoPlay
              autoPlayAfterSrcChange
              preload="none"
              layout="horizontal-reverse"
              showJumpControls={false}
              showSkipControls={order.length > 1 || hasNext}
              showFilledProgress
              customAdditionalControls={[]}
              customVolumeControls={[RHAP_UI.VOLUME]}
              volume={volumeLevel.current}
              muted={muted.current}
              onVolumeChange={() => {
                const el = ref.current?.audio?.current;
                if (!el) return;
                // A zero is the mute button: remember that it's muted, but not
                // as a level -- the level to come back to is the last real one.
                if (!(el.volume > 0)) {
                  muted.current = true;
                  return;
                }
                muted.current = false;
                volumeLevel.current = el.volume;
                rememberVolume(el.volume);
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              listenInterval={3000}
              onListen={() => reportHeard()}
              onEnded={() => {
                reportHeard(true);
                goNext(false);
              }}
              onPlayError={() => setPlaying(false)}
              onError={() => {
                // No library copy and no sample: say so rather than sitting
                // silently on a track that will never start.
                setPlaying(false);
                explainNoAudio(current.title, `No audio available for ${current.title}.`);
                // A list that keeps going shouldn't stall on one dead track.
                if (hasNext) goNext(false);
              }}
              onClickNext={() => goNext(true)}
              onClickPrevious={() => step(-1)}
              className="smt-audio-player"
            />
            )}
            {/* Only worth offering when there's a queue to shuffle. */}
            {order.length > 1 && (
              <Button
                size="icon-xs"
                variant={shuffle ? "default" : "ghost"}
                aria-label={shuffle ? "Play in order" : "Shuffle"}
                title={shuffle ? "Playing shuffled" : "Shuffle"}
                aria-pressed={shuffle}
                onClick={toggleShuffle}
              >
                <LuShuffle />
              </Button>
            )}
            {/* What's queued: this release's tracks, then the row after it. */}
            {(order.length > 1 || nextLabel) && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button size="icon-xs" variant="ghost" aria-label="Queue" title="Queue">
                    <LuListMusic />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent side="top" align="end" className="max-h-80 w-72 overflow-y-auto">
                  <DropdownMenuLabel className="truncate">
                    {current.release?.album || current.album || current.artist || "Queue"}
                  </DropdownMenuLabel>
                  {order.map((at, i) => (
                    <DropdownMenuItem
                      key={i}
                      onSelect={() => jump(i)}
                      className={i === pos ? "font-semibold" : undefined}
                    >
                      <span className="w-5 flex-none text-right text-xs text-muted-foreground">{i + 1}</span>
                      <span className="truncate">{queue[at]?.title}</span>
                      {i === pos && playing && <LuVolume2 aria-label="playing" className="ml-auto" />}
                    </DropdownMenuItem>
                  ))}
                  {nextLabel && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => advance(true)}>
                        <LuChevronsRight />
                        <span className="truncate">
                          {continueOn() ? "Then: " : "Next artist: "}
                          {nextLabel}
                        </span>
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {hasNext && (
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label="Next artist"
                title={nextLabel ? `Next artist: ${nextLabel} (shift+N)` : "Next artist (shift+N)"}
                disabled={advancing}
                onClick={() => advance(true)}
              >
                <LuChevronsRight />
              </Button>
            )}
            {current.release && (
              <>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button size="icon-xs" variant="ghost" aria-label="I like this" title="I like this">
                      <LuThumbsUp />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent side="top" align="end">
                    {current.release.album && (
                      <DropdownMenuItem onSelect={() => saveRelease(current.release!)}>
                        <LuBookmark />
                        <span className="truncate">Save "{current.release.album}" for later</span>
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuItem onSelect={() => followArtist(current.release!)}>
                      <LuUserPlus />
                      <span className="truncate">Follow {current.release.artist}</span>
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                {current.release.album && (
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Not for me"
                    title={`Not for me: hide "${current.release.album}" from Discover`}
                    onClick={() => dislike(current.release!)}
                  >
                    <LuThumbsDown />
                  </Button>
                )}
              </>
            )}
            <Button size="icon-xs" variant="ghost" aria-label="Close player" onClick={close}>
              <LuX />
            </Button>
          </div>
        </div>
      )}
    </PlayerContext.Provider>
  );
}

export function usePreviewPlayer() {
  return useContext(PlayerContext);
}

// The play/pause button that belongs next to a track anywhere in the app.
// Disabled when no catalogue had a sample for that track.
export function PreviewPlayButton({
  queue,
  index,
  className,
}: {
  queue: PreviewTrack[];
  index: number;
  className?: string;
}) {
  const { isCurrentTrack, playing, toggle } = usePreviewPlayer();
  const track = queue[index];
  const active = !!track && isCurrentTrack(track.artist, track.title);
  const label = !track
    ? "No audio"
    : active && playing
      ? `Pause ${track.title}`
      : `Play ${track.title}`;
  return (
    <Button
      size="icon-xs"
      variant={active ? "default" : "outline"}
      disabled={!track}
      aria-label={label}
      title={label}
      onClick={() => toggle(queue, index)}
      className={cn("flex-none", className)}
    >
      {active && playing ? <LuPause /> : <LuPlay />}
    </Button>
  );
}
