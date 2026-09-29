// Playing a list of releases (Discover, Upcoming) or artists (Similar Artists):
// a row's play button plays what that row has, and with "Continue to next
// artist" on, the player walks on down the list when it runs out. All of it
// lives outside the pages, because the player -- and so the walk -- outlives
// them: it keeps going after you navigate away.
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api, hypemStream } from "../api";
import type { DiscoverSong } from "../types";
import {
  usePreviewPlayer,
  type PreviewTrack,
  type QueueContinuation,
} from "../components/PreviewPlayer";
import { continueOn, samplerSize } from "./playPrefs";
import { explainNoAudio } from "../components/PrewarmNotice";

export type PlayRow = {
  /** Which row this is: the player marks it while its tracks play. */
  key: string;
  artist: string;
  album?: string | null;
  mbid?: string | null;
  artistId?: number | null;
  image?: string | null;
  date?: string | null;
  /** An artist rather than a record: play their top tracks. */
  topTracksOnly?: boolean;
  /** Songs the source named (aired, posted), most played first. */
  songs?: DiscoverSong[] | null;
};

// A release nobody has opened is resolved while you wait (its tracklist, then
// where each track plays from). The pre-load job has usually done this; if
// not, this is how long to wait before playing whatever was found.
const RESOLVE_WAIT_MS = 60_000;
const TOP_TRACKS = 10;

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

// Loose title key, so "Sunday Bed (feat. X)" and "Sunday Bed" are one song.
const songKey = (title: string) =>
  title.toLowerCase().replace(/[([].*?[)\]]/g, "").replace(/[^\p{L}\p{N}]+/gu, "");

function releaseRef(row: PlayRow) {
  return { artist: row.artist, album: row.album, mbid: row.mbid, date: row.date, image: row.image };
}

// The songs a blog posted that Hype Machine streams whole: the one thing sure
// to play for a record no catalogue has yet, so they go first.
function postedSongs(row: PlayRow): PreviewTrack[] {
  return (row.songs ?? [])
    .filter((s) => s.hypem)
    .map((s) => ({
      title: s.title,
      artist: row.artist,
      artistId: row.artistId,
      album: row.album,
      note: "Hype Machine",
      noteUrl: `https://hypem.com/track/${s.hypem}`,
      noteIcon: null,
      group: row.key,
      image: row.image,
      release: releaseRef(row),
      src: hypemStream(s.hypem!, { artist: row.artist, title: s.title, album: row.album }),
    }));
}

// Put the songs the source named ahead of the rest, most played first, and
// drop any a posted song already covers.
function namedFirst(titles: string[], row: PlayRow, posted: PreviewTrack[]): string[] {
  const played = new Set(posted.map((t) => songKey(t.title)));
  const rest = titles.filter((t) => !played.has(songKey(t)));
  const named = (row.songs ?? []).map((s) => songKey(s.title));
  const rank = (t: string) => {
    const at = named.indexOf(songKey(t));
    return at < 0 ? named.length : at;
  };
  return [...rest].sort((a, b) => rank(a) - rank(b));
}

function rowLabel(row: PlayRow): string {
  return row.album ? `${row.artist} - ${row.album}` : row.artist;
}

async function topTracks(
  row: PlayRow,
  queueNote: string | null,
  posted: PreviewTrack[] = [],
): Promise<PreviewTrack[]> {
  const top = await api.artistTopTracksByName(row.artist, TOP_TRACKS).catch(() => null);
  const size = samplerSize();
  const played = new Set(posted.map((t) => songKey(t.title)));
  const tracks = (top?.tracks ?? []).filter((t) => t.stream && !played.has(songKey(t.name)));
  const room = size ? Math.max(size - posted.length, 0) : tracks.length;
  return [...posted, ...tracks.slice(0, room).map((t) => ({
    title: t.name,
    artist: row.artist,
    artistId: row.artistId,
    album: t.album,
    url: t.url,
    note: t.library ?? null,
    noteUrl: t.library_url ?? null,
    noteIcon: t.library_icon ?? null,
    group: row.key,
    queueNote,
    image: row.image,
    release: releaseRef(row),
    src: t.stream,
  }))];
}

/**
 * What a row plays: the release's own tracks that have audio -- or, with the
 * sampler on, its most played few -- else (the record isn't out yet, or the
 * row is an artist) the artist's top tracks, with a note saying why.
 */
export async function rowQueue(
  row: PlayRow,
  onProgress?: (done: number, total: number) => void,
): Promise<PreviewTrack[]> {
  if (!row.artist) return [];
  const posted = postedSongs(row);
  if (row.topTracksOnly || !row.album) {
    return topTracks(row, row.topTracksOnly ? null : `Playing ${row.artist}'s top tracks`, posted);
  }
  const album = row.album;
  const mbid = row.mbid || undefined;
  const size = samplerSize();
  const until = Date.now() + RESOLVE_WAIT_MS;
  let found = await api.albumPlayable(row.artist, album, mbid, size > 0);
  while (!found.ready && found.running && Date.now() < until) {
    onProgress?.(found.progress?.done ?? 0, found.progress?.total ?? 0);
    await sleep(1500);
    found = await api.albumPlayable(row.artist, album, mbid, size > 0);
  }
  const titles = found.order?.length ? found.order : Object.keys(found.tracks || {});
  // Only what streams through the player: a video embed can't say when it's
  // finished, so it would stall the walk down the list.
  let playable = titles.filter((title) => found.tracks?.[title]?.stream);
  if (size) {
    // The most played first, then the rest in tracklist order.
    const ranked = (found.ranked ?? []).filter((t) => playable.includes(t));
    playable = [...ranked, ...playable.filter((t) => !ranked.includes(t))];
  }
  // What the station aired or the blog posted comes before all of that.
  playable = namedFirst(playable, row, posted);
  if (size) playable = playable.slice(0, Math.max(size - posted.length, 0));
  if (playable.length) {
    return [...posted, ...playable.map((title) => {
      const t = found.tracks[title]!;
      return {
        title,
        artist: row.artist,
        artistId: row.artistId,
        album,
        note: t.label ?? null,
        noteUrl: t.source_url ?? null,
        noteIcon: t.icon ?? null,
        group: row.key,
        image: row.image,
        release: releaseRef(row),
        src: t.stream,
      };
    })];
  }
  return topTracks(
    row,
    `No music found for "${album}", so playing ${row.artist}'s top tracks`,
    posted,
  );
}

/** After row *index* of *rows*: the next row down that has anything to play. */
export function continueFrom(
  rows: PlayRow[],
  index: number,
  skip?: (row: PlayRow) => boolean,
): QueueContinuation {
  const upcoming = rows.slice(index + 1).find((r) => !skip?.(r));
  return {
    label: upcoming ? rowLabel(upcoming) : null,
    load: async (asked) => {
      for (let i = index + 1; i < rows.length; i += 1) {
        // Read each time, so switching it off mid-album stops at the album's
        // end -- unless the person asked for the next one themselves.
        if (!asked && !continueOn()) return null;
        const row = rows[i]!;
        if (skip?.(row)) continue;
        const queue = await rowQueue(row).catch(() => []);
        if (queue.length) return { queue, next: continueFrom(rows, i, skip) };
      }
      return null;
    },
  };
}

export type Starting = { key: string; done: number; total: number };

/**
 * Play buttons for a list: play(row, rows) starts that row (or pauses and
 * resumes it when it's the one playing), handing the player the rows after it
 * to go on to. *starting* is the row being looked up, for its spinner.
 */
export function useRowPlayer() {
  const player = usePreviewPlayer();
  const [starting, setStarting] = useState<Starting | null>(null);
  const token = useRef(0);

  function play(row: PlayRow, rows: PlayRow[], skip?: (row: PlayRow) => boolean) {
    if (player.current && player.current.group === row.key) {
      player.toggle([player.current], 0);
      return;
    }
    const mine = ++token.current;
    setStarting({ key: row.key, done: 0, total: 0 });
    const index = rows.findIndex((r) => r.key === row.key);
    rowQueue(row, (done, total) => {
      if (mine === token.current) setStarting({ key: row.key, done, total });
    })
      .then((queue) => {
        if (mine !== token.current) return;
        if (!queue.length) {
          explainNoAudio(rowLabel(row), `Nothing to play for ${rowLabel(row)}.`);
          return;
        }
        player.toggle(queue, 0, continueFrom(rows, index, skip));
      })
      .catch(() => {
        if (mine === token.current) toast.error(`Could not look up ${rowLabel(row)}.`);
      })
      .finally(() => {
        if (mine === token.current) setStarting(null);
      });
  }

  return {
    play,
    starting,
    activeKey: player.current?.group ?? null,
    playing: player.playing,
  };
}
