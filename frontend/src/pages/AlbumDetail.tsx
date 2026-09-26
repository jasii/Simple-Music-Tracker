import { useEffect, useState } from "react";
import { LuArrowLeft, LuFlame, LuLoaderCircle } from "react-icons/lu";
import { toast } from "sonner";
import { useQuery } from "@tanstack/react-query";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { AlbumArt } from "../components/AlbumArt";
import { ArtistLink } from "../components/ArtistLink";
import { ArtZoom } from "../components/ArtZoom";
import { PreviewPlayButton, type PreviewTrack } from "../components/PreviewPlayer";
import { AlbumExclusiveSparkles, UniqueSparkle } from "../components/UniqueSparkle";
import type { AlbumExtras, AlbumTrack } from "../types";
import { ReleaseIcons, formatDate, shortQuality } from "../lib/format";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Progress } from "../components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

// How a Soulseek download's status reads next to the album.
const DOWNLOAD_STATUS: Record<string, string> = {
  searching: "searching",
  downloading: "downloading",
  complete: "downloaded",
  partial: "incomplete",
  failed: "failed",
  cancelled: "cancelled",
};

function fmtDuration(sec?: number | null): string {
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

export default function AlbumDetail() {
  const [params] = useSearchParams();
  const artist = (params.get("artist") || "").trim();
  const title = (params.get("title") || "").trim();
  const mbid = (params.get("mbid") || "").trim();
  const origin = params.get("from");
  const artistIdParam = params.get("artist_id");
  const releaseDate = (params.get("date") || "").trim();
  // Cover URL carried over from the row that was clicked: it's already in the
  // browser/art cache, so the cover paints immediately instead of waiting for
  // the (slow) tracklist lookup to resolve its own image.
  const img = (params.get("img") || "").trim();

  const { data, isError, refetch } = useQuery({
    queryKey: ["album", artist, title, mbid],
    queryFn: () => api.album(artist, title, mbid || undefined),
    // The unique-track marks wait on a background pass over the artist's
    // albums the first time; poll until it has finished.
    // Also while a Soulseek download of it is moving, so its progress shows.
    refetchInterval: (q) =>
      q.state.data?.unique_running ||
      ["searching", "downloading"].includes(q.state.data?.download?.status ?? "")
        ? 5000
        : false,
  });
  // Write-up and tags, gathered from whichever metadata sources answer each
  // of them (Settings > Metadata sets the order). Fetched on its own so the
  // page never waits on a slow source to render.
  const { data: extras } = useQuery({
    queryKey: ["albumExtras", artist, title, mbid],
    queryFn: () => api.albumExtras(artist, title, mbid || undefined),
    enabled: !!artist && !!title,
    staleTime: 5 * 60_000,
  });
  // A configured download client, for the Grab and Get missing buttons.
  const { data: downloaderData } = useQuery({
    queryKey: ["plugins", "downloader"],
    queryFn: () => api.plugins("downloader"),
    staleTime: 5 * 60_000,
  });
  const downloader = (downloaderData?.plugins ?? []).find((p) => p.configured);

  const [following, setFollowing] = useState(false);
  useEffect(() => {
    if (data) setFollowing(!!data.following);
  }, [data]);

  const msg = isError
    ? "Failed to load tracklist."
    : !data
      ? "Loading..."
      : "Tracklist not available yet.";

  const [busy, setBusy] = useState(false);
  function toggleFollow() {
    const state = following ? "none" : "subscribed";
    setBusy(true);
    api
      .trackByName(artist, state)
      .then((r: any) => { if (!r.error) setFollowing(state !== "none"); })
      .finally(() => setBusy(false));
  }

  // Back link mirrors the old album_page logic.
  let back = { to: "/upcoming", label: "Upcoming" };
  if (origin === "discover") back = { to: "/discover", label: "Discover" };
  else if (origin === "missing") back = { to: "/missing", label: "Missing" };
  else if (origin === "artist" && artistIdParam && /^\d+$/.test(artistIdParam))
    back = { to: `/artist/${artistIdParam}`, label: artist };

  const tracks: AlbumTrack[] = data?.tracks || [];
  // Every quality the library holds this release in, best first (one server
  // may have the FLAC and another the 320). Older responses carried only the
  // best one.
  const ownedFormats: string[] = data?.owned_formats?.length
    ? data.owned_formats
    : data?.owned_format
      ? [data.owned_format]
      : [];
  // Everything plays through the app's own stream route: the full song from a
  // library that has it, else the 30-second sample the catalogues gave us.
  // What each track can be played from, worked out in the background as soon
  // as the page opens: a library copy, a catalogue sample, or the video
  // Last.fm's own player uses. Rows with nothing get no play button.
  const { data: playable } = useQuery({
    queryKey: ["albumPlayable", artist, title, mbid],
    queryFn: () => api.albumPlayable(artist, title, mbid || undefined),
    enabled: tracks.length > 0,
    refetchInterval: (q) => (q.state.data && !q.state.data.ready ? 3000 : false),
  });
  const source = (name: string) => playable?.tracks?.[name];
  // The library holds fewer tracks of this than the tracklist has. Decided on
  // the scan's count, not on which rows play: a track a library can't find by
  // name isn't proof it's missing, but a folder of 7 files for a 9-track
  // album is.
  const ownedTracks = data?.owned_tracks ?? 0;
  // Measured against the shortest official edition, so a standard-edition
  // copy isn't short of a bonus track the listed tracklist happens to carry.
  const completeTracks = data?.complete_tracks || tracks.length;
  const short = !!data?.owned && ownedTracks > 0 && completeTracks > ownedTracks;
  // Which ones, once the per-track library check has run.
  const missingNames =
    short && playable?.ready
      ? tracks.filter((t) => source(t.name)?.kind !== "library").map((t) => t.name)
      : [];
  const missingSet = new Set(missingNames);
  const [fetchingMissing, setFetchingMissing] = useState(false);
  function getMissing() {
    setFetchingMissing(true);
    api
      .grabMissing({
        artist,
        title,
        mbid: mbid || undefined,
        // Unknown until the library check finishes: the server works it out.
        tracks: missingNames.length ? missingNames : undefined,
        owned_format: ownedFormats[0],
      })
      .then((r) => {
        if (r.error) toast.error(r.error);
        else toast.success(`${r.client}: ${r.message}`);
        refetch();
      })
      .catch(() => toast.error("Could not reach the server."))
      .finally(() => setFetchingMissing(false));
  }
  const [grabbing, setGrabbing] = useState(false);
  function grabAlbum() {
    setGrabbing(true);
    api
      .grab(artist, title)
      .then((r) => {
        if (r.error) toast.error(r.error);
        else toast.success(`${r.client}: ${r.message}`);
        refetch();
      })
      .catch(() => toast.error("Could not reach the server."))
      .finally(() => setGrabbing(false));
  }
  const job = data?.download;
  const audioProgress = playable?.progress?.total
    ? Math.round(((playable.progress.done ?? 0) / playable.progress.total) * 100)
    : 0;
  const queue: PreviewTrack[] = tracks.map((t) => {
    const found = source(t.name);
    return {
      title: t.name,
      artist,
      artistId: data?.artist_id,
      album: title,
      url: t.url,
      // Whatever it plays from gets the credit: the server it's on, the
      // catalogue that sampled it, or the video it came out of.
      note: found && found.kind !== "none" ? found.label ?? null : null,
      noteUrl: found?.source_url ?? null,
      noteIcon: found?.icon ?? null,
      // A resolved stream plays straight away; a video is left to the player,
      // which asks for the id (already cached by the pass above).
      src: found?.stream ?? null,
    };
  });

  return (
    <div>
      <p className="mb-3">
        <RouterLink to={back.to} className="inline-flex items-center gap-1.5 hover:underline">
          <LuArrowLeft aria-hidden />
          {back.label}
        </RouterLink>
      </p>

      <div className="flex flex-wrap items-start gap-4">
        {/* Click the cover for the full-size artwork. */}
        <ArtZoom src={img || data?.image} alt={`${title} cover`}>
          <AlbumArt
            src={img || data?.image}
            boxSize="175px"
            rounded="md"
            resolve={{ artist, title, mbid: mbid || undefined }}
          />
        </ArtZoom>
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          {/* Their page here whether or not we have them yet: an unknown name
              is created and scraped on the way. */}
          <ArtistLink name={artist} artistId={data?.artist_id} className="text-muted-foreground" />
          {releaseDate && <p className="mt-0.5 text-sm text-muted-foreground">{formatDate(releaseDate)}</p>}
          {(data?.primary_type || ownedFormats.length > 0 || short) && (
            <p className="mt-1 flex flex-wrap items-center gap-1.5">
              {/* What kind of record this is, what marks it as a reissue, a
                  live set or a remix collection rather than a new one -- and
                  the quality you already hold it in, if you hold it. */}
              {data?.primary_type && <Badge variant="secondary">{data.primary_type}</Badge>}
              {(data?.secondary_types ?? []).map((t) => (
                <Badge key={t} variant="outline" className="text-muted-foreground">{t}</Badge>
              ))}
              {short && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className="border-amber-600/40 text-amber-500">
                      {ownedTracks} of {completeTracks} tracks
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>
                    The library has fewer tracks of this than MusicBrainz lists
                  </TooltipContent>
                </Tooltip>
              )}
              {ownedFormats.map((fmt) => (
                <Tooltip key={fmt}>
                  <TooltipTrigger asChild>
                    <Badge variant="outline" className="border-emerald-600/40 text-emerald-500">
                      {shortQuality(fmt)}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>{`You have this in ${fmt}`}</TooltipContent>
                </Tooltip>
              ))}
            </p>
          )}
          <div className="mt-2">
            <Button
              size="xs"
              variant={following ? "outline" : "default"}
              disabled={busy}
              onClick={toggleFollow}
            >
              {following ? "Following" : "Follow"}
            </Button>
            {short && downloader && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="xs"
                    className="ml-2"
                    disabled={fetchingMissing || job?.status === "searching" || job?.status === "downloading"}
                    onClick={getMissing}
                  >
                    {fetchingMissing ? "Looking..." : "Get missing tracks"}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {missingNames.length
                    ? `Fetch ${missingNames.length} missing track${missingNames.length === 1 ? "" : "s"} from ${downloader.label}`
                    : `Find which tracks the library lacks and fetch just those from ${downloader.label}`}
                </TooltipContent>
              </Tooltip>
            )}
            {!data?.owned && downloader && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    size="xs"
                    className="ml-2"
                    disabled={grabbing || job?.status === "searching" || job?.status === "downloading"}
                    onClick={grabAlbum}
                  >
                    {grabbing ? "Searching..." : "Grab"}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{`Search ${downloader.label} for this release and download it`}</TooltipContent>
              </Tooltip>
            )}
          </div>
          {job && (
            <p className="mt-1.5 text-sm text-muted-foreground">
              <RouterLink
                to="/missing"
                className="hover:underline"
                onClick={() => { try { localStorage.setItem("missingTab", "downloads"); } catch {} }}
              >
                Soulseek: {DOWNLOAD_STATUS[job.status] ?? job.status}
                {job.total ? ` · ${job.done}/${job.total} files` : ""}
                {job.failed ? ` · ${job.failed} failed` : ""}
              </RouterLink>
            </p>
          )}
          <ReleaseIcons artist={artist} album={title} mbid={mbid || undefined} />
        </div>
      </div>

      <AlbumFacts extras={extras} />

      <h2 className="mt-6 mb-2 flex flex-wrap items-center gap-3 text-xl font-semibold">
        Tracks
        {tracks.length > 0 && playable && !playable.ready && (
          <span className="flex min-w-[12rem] items-center gap-2 text-sm font-normal text-muted-foreground">
            <Progress value={audioProgress} className="w-24" />
            finding audio {playable.progress?.done ?? 0}/{playable.progress?.total ?? tracks.length}
          </span>
        )}
      </h2>
      {tracks.length === 0 ? (
        <p className="flex items-center gap-2 text-muted-foreground">
          {!data && !isError && <LuLoaderCircle aria-hidden className="animate-spin" />}
          {msg}
        </p>
      ) : (
        <div className="flex flex-col">
          {tracks.map((t, i) => (
            <div
              key={i}
              className={
                "flex items-center gap-3 border-b py-1.5" +
                (missingSet.has(t.name) ? " text-muted-foreground" : "")
              }
            >
              <span className="w-6 flex-none text-right text-muted-foreground">{i + 1}</span>
              {/* One docked player for the whole app; the server hands back the
                  full song when a library has it, else the sample. */}
              {source(t.name) && source(t.name)!.kind !== "none" ? (
                <PreviewPlayButton queue={queue} index={i} />
              ) : (
                <span className="w-6 flex-none" aria-hidden />
              )}
              <div className="flex min-w-0 flex-1 items-center gap-1.5">
                <span className="truncate">
                  {t.url ? (
                    <a href={t.url} target="_blank" rel="noopener" className="hover:underline">{t.name}</a>
                  ) : (
                    t.name
                  )}
                </span>
                {missingSet.has(t.name) && (
                  <Badge variant="outline" className="flex-none border-amber-600/40 text-amber-500">
                    missing
                  </Badge>
                )}
                {t.unique && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <UniqueSparkle label="Not in your library" />
                    </TooltipTrigger>
                    <TooltipContent>Not in your library</TooltipContent>
                  </Tooltip>
                )}
                {t.album_exclusive && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <AlbumExclusiveSparkles label="On no album by this artist" />
                    </TooltipTrigger>
                    <TooltipContent>
                      This song is on no album by this artist
                    </TooltipContent>
                  </Tooltip>
                )}
                {t.hot && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="flex-none leading-none">
                        <LuFlame
                          stroke="url(#smtHotFlame)"
                          aria-label="One of this album's most played tracks"
                        />
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      {t.playcount
                        ? `${t.playcount.toLocaleString()} plays on Last.fm`
                        : "One of this album's most played tracks"}
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>
              {t.duration ? <span className="flex-none text-muted-foreground">{fmtDuration(t.duration)}</span> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * What the metadata sources know about a release beyond its tracklist: the
 * write-up and the genre tags. The write-up says which source it came from,
 * since they disagree and the order is the user's to set.
 */
function AlbumFacts({ extras }: { extras?: AlbumExtras }) {
  const [open, setOpen] = useState(false);
  if (!extras) return null;

  const description = extras.description;
  const tags = extras.tags ?? [];
  if (!description && !tags.length) return null;

  // Long write-ups are folded: the point of the page is the tracklist.
  const long = !!description && description.length > 320;

  return (
    <div className="mt-6 rounded-md border p-3">
      {tags.length > 0 && (
        <p className="flex flex-wrap gap-1">
          {tags.map((tag) => (
            <Badge key={tag} variant="outline" className="text-muted-foreground">
              {tag}
            </Badge>
          ))}
        </p>
      )}

      {description && (
        <div className={tags.length ? "mt-2" : undefined}>
          <p className={long && !open ? "line-clamp-3 text-sm" : "text-sm"}>
            {description}
          </p>
          <p className="mt-1 flex items-center gap-2">
            {long && (
              <Button size="xs" variant="ghost" onClick={() => setOpen((v) => !v)}>
                {open ? "Show less" : "Read more"}
              </Button>
            )}
            {extras.sources?.description && (
              <span className="text-xs text-muted-foreground">
                via {extras.sources.description}
              </span>
            )}
          </p>
        </div>
      )}
    </div>
  );
}
