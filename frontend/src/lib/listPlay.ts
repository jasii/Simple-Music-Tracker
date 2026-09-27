// Playing a list of releases (Discover, Upcoming) or artists (Similar Artists):
// a row's play button plays what that row has, and with "Continue to next
// artist" on, the player walks on down the list when it runs out. All of it
// lives outside the pages, because the player -- and so the walk -- outlives
// them: it keeps going after you navigate away.
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import {
  usePreviewPlayer,
  type PreviewTrack,
  type QueueContinuation,
} from "../components/PreviewPlayer";
import { continueOn, samplerSize } from "./playPrefs";

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
};

// A release nobody has opened is resolved while you wait (its tracklist, then
// where each track plays from). The pre-load job has usually done this; if
// not, this is how long to wait before playing whatever was found.
const RESOLVE_WAIT_MS = 60_000;
const TOP_TRACKS = 10;

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

function rowLabel(row: PlayRow): string {
  return row.album ? `${row.artist} - ${row.album}` : row.artist;
}

async function topTracks(row: PlayRow, queueNote: string | null): Promise<PreviewTrack[]> {
  const top = await api.artistTopTracksByName(row.artist, TOP_TRACKS).catch(() => null);
  const size = samplerSize();
  const tracks = (top?.tracks ?? []).filter((t) => t.stream);
  return (size ? tracks.slice(0, size) : tracks).map((t) => ({
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
    release: { artist: row.artist, album: row.album, mbid: row.mbid, date: row.date, image: row.image },
    src: t.stream,
  }));
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
  if (row.topTracksOnly || !row.album) {
    return topTracks(row, row.topTracksOnly ? null : `Playing ${row.artist}'s top tracks`);
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
    playable = [...ranked, ...playable.filter((t) => !ranked.includes(t))].slice(0, size);
  }
  if (playable.length) {
    return playable.map((title) => {
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
        release: { artist: row.artist, album, mbid: row.mbid, date: row.date, image: row.image },
        src: t.stream,
      };
    });
  }
  return topTracks(row, `No music found for "${album}", so playing ${row.artist}'s top tracks`);
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
          toast.error(`Nothing to play for ${rowLabel(row)}.`);
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
