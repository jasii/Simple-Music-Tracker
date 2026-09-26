import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import AudioPlayer, { RHAP_UI } from "react-h5-audio-player";
import "react-h5-audio-player/lib/styles.css";
import { LuLoaderCircle, LuPause, LuPlay, LuShuffle, LuX } from "react-icons/lu";
import { toast } from "sonner";
import { api } from "../api";
import { ArtistLink } from "./ArtistLink";
import { ServiceIcon } from "./ServiceIcon";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";

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
  src: string | null | undefined;
};

type PlayerState = {
  current: PreviewTrack | null;
  playing: boolean;
  /** Is this the track the bar is currently loaded with? */
  isCurrent: (src: string | null | undefined) => boolean;
  /** Same question by name, for a track whose source isn't resolved yet. */
  isCurrentTrack: (artist: string | null | undefined, title: string) => boolean;
  /** Play track *index* of *queue*, or pause/resume it if it's already loaded. */
  toggle: (queue: PreviewTrack[], index: number) => void;
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

  const toggle = useCallback(
    (nextQueue: PreviewTrack[], nextIndex: number) => {
      const track = nextQueue[nextIndex];
      if (!track) return;
      // Same track: this is a pause/resume, not a reload.
      if (`${track.artist ?? ""}|${track.title}` === currentKey.current) {
        const el = ref.current?.audio?.current;
        if (!el) return;
        if (el.paused) el.play().catch(() => setPlaying(false));
        else el.pause();
        return;
      }
      const next = shuffle
        ? shuffledOrder(nextQueue.length, nextIndex)
        : Array.from({ length: nextQueue.length }, (_, i) => i);
      setQueue(nextQueue);
      setOrder(next);
      setPos(next.indexOf(nextIndex));
      forgetResolved();
    },
    [forgetResolved, shuffle],
  );

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

  const close = useCallback(() => {
    ref.current?.audio?.current?.pause();
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
      {current && <div aria-hidden className="h-24" />}
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
                {order.length > 1 ? ` · ${pos + 1} of ${order.length}` : ""}
                {(() => {
                  const note = current.note || resolvedNote;
                  if (!note) return null;
                  const href = current.noteUrl || resolvedNoteUrl;
                  const icon = current.noteIcon || resolvedNoteIcon;
                  // "from Navidrome", where Navidrome is the record on the
                  // server it came from.
                  return (
                    <>
                      {" · from "}
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
                  );
                })()}
              </p>
            </div>
            {resolving && !src && !videoId && (
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <LuLoaderCircle aria-hidden className="animate-spin" />
                finding audio
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
              showSkipControls={order.length > 1}
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
              onEnded={() => (pos + 1 < order.length ? step(1) : setPlaying(false))}
              onPlayError={() => setPlaying(false)}
              onError={() => {
                // No library copy and no sample: say so rather than sitting
                // silently on a track that will never start.
                setPlaying(false);
                toast.error(`No audio available for ${current.title}.`);
              }}
              onClickNext={() => step(1)}
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
