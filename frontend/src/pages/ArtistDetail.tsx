import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper, type RowSelectionState } from "@tanstack/react-table";
import { Link as RouterLink, useParams } from "react-router-dom";
import {
  LuArrowLeft,
  LuBell,
  LuBellRing,
  LuFolderSearch,
  LuGitMerge,
  LuLink,
  LuLoaderCircle,
  LuRefreshCw,
  LuSettings2,
  LuSquareLibrary,
} from "react-icons/lu";
import { AlbumArt } from "../components/AlbumArt";
import { useOpenArtist } from "../components/ArtistLink";
import { AlbumLinks } from "../components/AlbumLinks";
import { ArtistArtwork } from "../components/ArtistArtwork";
import { ServiceIcon } from "../components/ServiceIcon";
import { ToolItem } from "../components/ToolItem";
import { PreviewPlayButton, type PreviewTrack } from "../components/PreviewPlayer";
import { UniqueSparkle } from "../components/UniqueSparkle";
import { BoxCheck } from "../components/BoxCheck";
import { DataTable } from "../components/DataTable";
import { TableActionBar } from "../components/TableActionBar";
import { api, art } from "../api";
import type {
  Artist,
  ArtistDetail as ArtistDetailT,
  DiscographyItem,
  DiscographyResponse,
  Subscription,
  SimilarArtist,
} from "../types";
import { formatDate, shortQuality } from "../lib/format";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "../components/ui/accordion";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { ButtonGroup } from "../components/ui/button-group";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { Input } from "../components/ui/input";
import { RadioGroup, RadioGroupItem } from "../components/ui/radio-group";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Progress } from "../components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

const greenBadge = "bg-green-600/15 text-green-700 dark:text-green-400";

// One suggested artist. Clicking it opens their page whether or not they're in
// the library yet: an artist we've never seen is created on the way there and
// filled in from Last.fm and MusicBrainz.
function SimilarChip({
  s,
  busy,
  onOpen,
}: {
  s: SimilarArtist;
  busy: boolean;
  onOpen: (name: string, artistId: number | null) => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          disabled={busy}
          onClick={() => onOpen(s.name, s.artist_id ?? null)}
          className="disabled:opacity-60"
        >
          <Badge variant="secondary" className={s.owned ? greenBadge : undefined}>
            {s.name}
            {busy && <LuLoaderCircle aria-label="Opening" className="ml-1 animate-spin" />}
          </Badge>
        </button>
      </TooltipTrigger>
      <TooltipContent>
        {s.owned ? "Owned" : s.artist_id ? "Open their page" : `Open ${s.name}`}
      </TooltipContent>
    </Tooltip>
  );
}

// The artist's best-known tracks, playable right under the header: the full
// song when one of your libraries has it, a 30-second sample otherwise. Every
// track plays through the one docked player (see components/PreviewPlayer).
function TopTracks({ artistId }: { artistId: number }) {
  const { data } = useQuery({
    queryKey: ["topTracks", artistId],
    queryFn: () => api.artistTopTracks(artistId, 5),
    staleTime: 60 * 60_000,
  });
  const tracks = data?.tracks ?? [];
  const queue: PreviewTrack[] = tracks.map((t) => ({
    title: t.name,
    artist: data?.artist,
    artistId,
    album: t.album,
    url: t.url,
    // t.library is the server when it's ours, the catalogue when it's a
    // sample; either way it's where the audio comes from.
    note: t.library ?? null,
    noteUrl: t.library_url ?? null,
    noteIcon: t.library_icon ?? null,
    src: t.stream,
  }));
  if (!tracks.length) return null;

  return (
    <div className="my-4">
      <p className="mb-1.5 text-muted-foreground">Top tracks</p>
      <div className="flex flex-wrap gap-2">
        {tracks.map((t, i) => (
          <div
            key={t.name}
            className={
              "relative flex max-w-[18rem] items-center gap-2 rounded-md border px-2 py-1.5" +
              (t.full ? " pr-7" : "")
            }
          >
            <PreviewPlayButton queue={queue} index={i} />
            <div className="min-w-0">
              <p className="truncate text-sm">
                {t.url ? (
                  <a href={t.url} target="_blank" rel="noopener noreferrer" className="hover:underline">
                    {t.name}
                  </a>
                ) : (
                  t.name
                )}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {t.playcount.toLocaleString()} plays
              </p>
            </div>
            {/* A corner mark for "this one plays in full from your library" --
                which library it is belongs in the tooltip, not five times over
                in the list. */}
            {t.full && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="absolute top-1.5 right-1.5 text-muted-foreground">
                    <LuSquareLibrary aria-label={`In your ${t.library} library`} />
                  </span>
                </TooltipTrigger>
                <TooltipContent>In your {t.library} library</TooltipContent>
              </Tooltip>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// Compact source label for the owned tags. The server reports which software
// it runs, so the label is already its real name ("Navidrome"); this only
// trims a still-generic "Subsonic / Navidrome" down to one word.
function shortSource(label: string): string {
  return label.split("/").pop()!.trim();
}
const grayBadge = "bg-muted text-muted-foreground";

// The service marks in the header: greyed back until the pointer is on them,
// matching the row of links on an album page.
const SERVICE_ICON =
  "grayscale opacity-45 transition hover:grayscale-0 hover:opacity-100";

const CATS: [keyof DiscographyResponse["groups"], string][] = [
  ["album", "Albums"],
  ["ep", "EPs"],
  ["single", "Singles"],
];

// One flattened discography row, tagged with its release type.
type DiscoRow = { it: DiscographyItem; type: string; label: string };
const dcol = createColumnHelper<DiscoRow>();

// How many similar-artist chips to show before the "show more".
const SIMILAR_SHOWN = 10;

// Mirrors app/exclusives._track_key, for the rare release with no MusicBrainz
// id: release labels and featured credits dropped, everything that marks a
// different recording (a remix, a live take) kept.
const NOISE_SUFFIX =
  /\s*[([-]\s*(?:\d{4}\s+)?(?:digital\s+|\d{4}\s+)?(?:re-?master(?:ed)?(?:\s+version)?(?:\s+\d{4})?|explicit(?:\s+version)?|clean(?:\s+version)?|bonus(?:\s+track)?|album\s+version|original\s+version|mono|stereo|deluxe(?:\s+edition)?|anniversary(?:\s+edition)?|reissue)\s*[)\]]?\s*$/i;
const FEATURING = /[([]?\s*(?:feat|ft)\.?\s[^)\]]*[)\]]?/i;

function titleKey(text: string): string {
  let key = (text || "").trim();
  for (;;) {
    const trimmed = key.replace(NOISE_SUFFIX, "").trim();
    if (trimmed === key || !trimmed) break;
    key = trimmed;
  }
  key = key.replace(FEATURING, " ");
  return key.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}

export default function ArtistDetail() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const qc = useQueryClient();

  // An artist created by clicking their name is at the front of the refresh
  // queue, so poll until that pass stamps last_checked: the page fills itself
  // in (MusicBrainz id, releases, counts) instead of needing a reload.
  const { data: artist } = useQuery({
    queryKey: ["artist", id],
    queryFn: () => api.artist(id),
    refetchInterval: (q) => (q.state.data && !q.state.data.last_checked ? 3000 : false),
  });
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.settings(),
    // Settings barely move; without this every artist page opened re-fetched
    // the whole settings payload.
    staleTime: 5 * 60_000,
  });
  // Similar-artist suggestions, from every source in the user's order.
  const { data: similar } = useQuery({
    queryKey: ["similarArtists", artist?.name],
    queryFn: () => api.similarArtists(artist!.name),
    enabled: !!artist?.name,
    staleTime: 10 * 60_000,
  });

  // The genre tags stored on the artist row, without repeats.
  const genreTags = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of (artist?.genres || "").split(",")) {
      const t = raw.trim();
      if (!t || seen.has(t.toLowerCase())) continue;
      seen.add(t.toLowerCase());
      out.push(t);
    }
    return out;
  }, [artist?.genres]);

  const {
    data: discoData,
    isError: discoError,
    refetch: refetchDisco,
  } = useQuery({
    queryKey: ["discography", id],
    queryFn: () => api.discography(id),
  });

  // How many songs each EP/single keeps off the albums. The first request for
  // an artist starts that pass server-side (it reads tracklists one release at
  // a time), so poll while it runs and show stale counts meanwhile.
  // Off by setting: no badge, and the tracklists behind it are never read.
  const showUnique = settings ? settings.show_unique_tags !== "false" : true;
  const { data: exclusives } = useQuery({
    queryKey: ["exclusives", id],
    queryFn: () => api.artistExclusives(id),
    enabled: !!artist?.mbid && showUnique,
    refetchInterval: (q) => (q.state.data?.running ? 4000 : false),
    staleTime: 60 * 60_000,
  });
  const uniqueProgress = exclusives?.progress?.total
    ? Math.round(((exclusives.progress.done ?? 0) / exclusives.progress.total) * 100)
    : 0;
  function uniqueCount(it: DiscographyItem) {
    const counts = showUnique ? exclusives?.counts : null;
    if (!counts) return null;
    return counts[it.mbid || "t:" + titleKey(it.title)] ?? null;
  }

  const [hiddenCats, setHiddenCats] = useState<Set<string>>(new Set());
  const [sub, setSub] = useState<Subscription>("none");
  const [mtypes, setMtypes] = useState<Set<string>>(new Set());
  const [mtypeResult, setMtypeResult] = useState("");
  const [refreshLabel, setRefreshLabel] = useState("Refresh now");
  const [toolsOpen, setToolsOpen] = useState(false);
  const [linksOpen, setLinksOpen] = useState(false);
  const [scanBusy, setScanBusy] = useState(false);
  const [scanResult, setScanResult] = useState("");

  // Discography table: search / owned filter / row selection (shared across the
  // three per-type tables via the rowKey-keyed selection record).
  // Opening a suggested artist creates their library row (seeded with whatever
  // is already known about them) and goes there, so a name is always a page.
  const { open: openSimilar, opening: openingSimilar } = useOpenArtist();

  const [showAllSimilar, setShowAllSimilar] = useState(false);
  const similarArtists = similar?.artists ?? [];

  const [discoSearch, setDiscoSearch] = useState("");
  const [ownedFilter, setOwnedFilter] = useState<"all" | "owned" | "unowned">("all");
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // A refresh that finds the MusicBrainz id after the page opened (a brand-new
  // artist, or one MusicBrainz was down for) should fill the discography in.
  const discoMbid = discoData?.mbid;
  useEffect(() => {
    if (artist?.mbid && !discoMbid) refetchDisco();
  }, [artist?.mbid, discoMbid, refetchDisco]);

  // Seed editable local state from the fetched artist / settings.
  useEffect(() => {
    if (!artist) return;
    setSub(artist.subscription);
    setMtypes(new Set((artist.monitor_types ?? "album,ep").split(",").filter(Boolean)));
  }, [artist]);
  useEffect(() => {
    if (!settings) return;
    setHiddenCats(new Set((settings.discography_autohide || "").split(",").filter(Boolean)));
  }, [settings]);

  // Derive discography display state from the query result.
  const disco: DiscographyResponse | null = discoData && discoData.mbid ? discoData : null;
  const discoSource = disco ? (disco.error ? "(MusicBrainz error)" : "(from MusicBrainz)") : "";
  const discoMsg = discoError
    ? "Failed to load discography."
    : !discoData
      ? "Loading from MusicBrainz..."
      : "No MusicBrainz match for this artist yet. Use Tools above to match a MusicBrainz URL.";

  // Flatten the grouped discography into typed rows.
  const tagged: DiscoRow[] = disco
    ? CATS.flatMap(([key, label]) => (disco.groups[key] || []).map((it) => ({ it, type: key as string, label })))
    : [];
  // The headline count reflects only the release types you're monitoring.
  const monitored = tagged.filter((t) => mtypes.has(t.type));
  const totalCount = monitored.length;
  const ownedCount = monitored.filter((t) => t.it.owned).length;

  const rowKey = (it: DiscographyItem) => it.mbid || it.title;
  const q = discoSearch.trim().toLowerCase();
  const matches = tagged
    .filter((t) => !q || t.it.title.toLowerCase().includes(q))
    .filter((t) => ownedFilter === "all" || (ownedFilter === "owned" ? !!t.it.owned : !t.it.owned));
  const rowsFor = (type: string) =>
    matches.filter((t) => t.type === type).sort((a, b) => (b.it.release_date || "").localeCompare(a.it.release_date || ""));
  const statFor = (type: string) => {
    const all = tagged.filter((t) => t.type === type);
    return { owned: all.filter((t) => t.it.owned).length, total: all.length };
  };
  // Accordions start open unless the type is auto-hidden in settings.
  const openCats = ["album", "ep", "single"].filter((k) => !hiddenCats.has(k));
  const selCount = Object.keys(rowSelection).length;

  function albumHref(it: DiscographyItem) {
    const qs = new URLSearchParams({ artist: artist?.name || "", title: it.title, from: "artist", artist_id: String(id) });
    if (it.mbid) qs.set("mbid", it.mbid);
    if (it.release_date) qs.set("date", it.release_date);
    if (it.image_url) qs.set("img", it.image_url);
    return "/album?" + qs.toString();
  }
  function bulkOwned(owned: boolean) {
    tagged.filter((t) => rowSelection[rowKey(t.it)]).forEach((t) => toggleOwned(t.it, owned));
    setRowSelection({});
  }

  // Columns shared by the three per-type tables. Selection is keyed by rowKey
  // (unique across types), so a single rowSelection record drives them all.
  const discoColumns = [
    dcol.display({
      id: "select",
      header: ({ table }) => (
        <BoxCheck checked={table.getIsAllRowsSelected()} onChange={(v) => table.toggleAllRowsSelected(v)} label="Select all" />
      ),
      cell: ({ row }) => (
        <BoxCheck checked={row.getIsSelected()} onChange={(v) => row.toggleSelected(v)} label={`Select ${row.original.it.title}`} />
      ),
      meta: { width: "2.5rem", align: "center" as const },
    }),
    dcol.accessor((r) => r.it.title, {
      id: "title",
      header: "Title",
      enableSorting: false,
      cell: ({ row }) => {
        const it = row.original.it;
        return (
          <div className="flex min-w-0 items-center gap-2">
            <AlbumArt
              src={it.image_url}
              boxSize="100px"
              resolve={{ artist: artist?.name || "", title: it.title, mbid: it.mbid }}
            />
            <RouterLink to={albumHref(it)} className="hover:underline">{it.title}</RouterLink>
            {/* EPs and singles: how much of this record the library hasn't
                got. Nothing missing earns no tag -- the absence is the
                answer. */}
            {row.original.type !== "album" && (uniqueCount(it)?.unique ?? 0) > 0 && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className="gap-1 whitespace-nowrap">
                    <UniqueSparkle />
                    {uniqueCount(it)!.unique}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  {uniqueCount(it)!.unique} of {uniqueCount(it)!.total}{" "}
                  {uniqueCount(it)!.total === 1 ? "song" : "songs"} here{" "}
                  {uniqueCount(it)!.unique === 1 ? "isn't" : "aren't"} in your library
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        );
      },
    }),
    dcol.accessor((r) => r.it.release_date, {
      id: "released",
      header: "Released",
      enableSorting: false,
      cell: ({ row }) => {
        const d = row.original.it.release_date;
        return <span className="text-muted-foreground">{d ? formatDate(d) : "TBA"}</span>;
      },
      meta: { width: "9rem" },
    }),
    dcol.display({
      id: "owned",
      header: "Owned",
      cell: ({ row }) => {
        const it = row.original.it;
        const sources = it.owned_sources ?? [];
        // Every quality you hold it in; older responses only carried the best.
        const held = it.owned_formats?.length
          ? it.owned_formats
          : it.owned_format
            ? [it.owned_format]
            : [];
        return (
          <div className="flex flex-col items-center gap-1">
            <BoxCheck
              checked={!!it.owned}
              onChange={(v) => toggleOwned(it, v)}
              label={it.owned ? `Mark ${it.title} not owned` : `Mark ${it.title} owned`}
            />
            {it.owned_covered && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className="px-1 py-0 text-[10px] leading-4 text-muted-foreground">
                    covered
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>Every song on it is in your library</TooltipContent>
              </Tooltip>
            )}
            {(sources.length > 0 || held.length > 0) && (
              <div className="flex flex-wrap justify-center gap-1">
                {/* What you've got, in shorthand: FLAC24, 320... */}
                {held.map((fmt) => (
                  <Badge
                    key={fmt}
                    variant="outline"
                    className="whitespace-nowrap border-emerald-600/40 px-1 py-0 text-[10px] leading-4 text-emerald-500"
                    title={`You have this in ${fmt}`}
                  >
                    {shortQuality(fmt)}
                  </Badge>
                ))}
                {sources.map((s) => (
                  <Badge
                    key={s.key}
                    variant="secondary"
                    className={`${greenBadge} whitespace-nowrap px-1 py-0 text-[10px] leading-4`}
                  >
                    {shortSource(s.label)}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        );
      },
      meta: { width: "10.5rem", align: "center" as const },
    }),
  ];

  function renderTable(rows: DiscoRow[]) {
    return (
      <div className="mt-2">
        <DataTable
          data={rows}
          columns={discoColumns}
          getRowId={(r) => rowKey(r.it)}
          enableRowSelection
          rowSelection={rowSelection}
          onRowSelectionChange={setRowSelection}
          initialPageSize={25}
          prefetchUrl={(r) => (r.it.image_url ? art(r.it.image_url) : null)}
          emptyText="No albums match."
        />
      </div>
    );
  }

  function changeSub(state: Subscription) {
    setSub(state);
    api.setSubscription(id, state);
  }

  const following = sub === "subscribed" || sub === "notify";
  const notifyOn = sub === "notify";

  function toggleOwned(item: DiscographyItem, owned: boolean) {
    // Optimistically flip the flag in the cached discography, then persist.
    qc.setQueryData<DiscographyResponse>(["discography", id], (prev) => {
      if (!prev) return prev;
      const groups = { ...prev.groups };
      (Object.keys(groups) as (keyof typeof groups)[]).forEach((k) => {
        groups[k] = groups[k].map((it) => (it === item ? { ...it, owned } : it));
      });
      return { ...prev, groups };
    });
    api.setAlbumOwned(id, item.title, owned, item.mbid).catch(() =>
      qc.invalidateQueries({ queryKey: ["discography", id] }),
    );
  }

  // Rescan only this artist's folders (fast), then refresh owned flags. The scan
  // runs inline server-side, so we show a busy state and report the summary it
  // returns rather than streaming progress.
  function rescanOwned() {
    setScanBusy(true);
    setScanResult("");
    api.scanArtist(id)
      .then((r) => {
        const parts = [`Filesystem: ${r.files} files, ${r.albums} albums`];
        for (const s of r.sources ?? []) parts.push(`${s.label}: ${s.albums} albums`);
        setScanResult(parts.join(" · "));
        return qc.invalidateQueries({ queryKey: ["artist", id] });
      })
      .then(() => refetchDisco())
      .catch(() => setScanResult("Scan failed."))
      .finally(() => setScanBusy(false));
  }

  function toggleMtype(value: string, checked: boolean) {
    const next = new Set(mtypes);
    if (checked) next.add(value); else next.delete(value);
    setMtypes(next);
    setMtypeResult("Saving...");
    api
      .setMonitorTypes(id, Array.from(next))
      .then((r: any) => {
        const kept: string[] = r.monitor_types || Array.from(next);
        setMtypes(new Set(kept));
        // Keep the cached artist in sync so navigating back doesn't re-seed the
        // old monitor types (which would revert the headline count).
        qc.setQueryData<ArtistDetailT>(["artist", id], (prev) =>
          prev ? { ...prev, monitor_types: kept.join(",") } : prev,
        );
        setMtypeResult(kept.length ? `Saved (${kept.join(", ")})` : "Saved");
        setTimeout(() => setMtypeResult(""), 2500);
      })
      .catch(() => setMtypeResult("Failed."));
  }

  function refresh() {
    setRefreshLabel("Refreshing...");
    api.refreshArtist(id).then(() => setTimeout(() => setRefreshLabel("Refresh now"), 3000));
  }

  if (!artist) return <p className="text-muted-foreground">Loading...</p>;

  const lastfmHref = artist.lastfm_url || "https://www.last.fm/music/" + encodeURIComponent(artist.name);

  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <RouterLink to="/artists" className="inline-flex items-center gap-1.5 hover:underline">
          <LuArrowLeft aria-hidden />
          All artists
        </RouterLink>
        {/* Everything that acts on this artist lives here rather than in four
            buttons scattered down the page. */}
        {/* Not modal: a modal menu locks pointer events on the body while it
            closes, which fights the dialogs its own items open. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline">
              <LuSettings2 aria-hidden /> Tools
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-[22rem]">
            {/* Each tool says what it does: the four of them were four verbs
                with no way to tell which one touched what. */}
            <ToolItem
              icon={<LuRefreshCw aria-hidden />}
              title={refreshLabel}
              description="Ask MusicBrainz and Last.fm about this artist again: new releases, bio, image, genres."
              disabled={refreshLabel !== "Refresh now"}
              onSelect={refresh}
            />
            <ToolItem
              icon={<LuFolderSearch aria-hidden />}
              title={scanBusy ? "Scanning..." : "Rescan owned"}
              description="Re-check your libraries for which of these releases you have, and in what quality."
              disabled={scanBusy || !disco}
              onSelect={rescanOwned}
            />
            <ToolItem
              icon={<LuLink aria-hidden />}
              title="Match albums"
              description="Connect a library album to a release here, or disconnect one the matcher got wrong."
              onSelect={() => setTimeout(() => setLinksOpen(true), 0)}
            />
            <ToolItem
              icon={<LuGitMerge aria-hidden />}
              title="Match / merge artist"
              description="Point this artist at a MusicBrainz URL, or fold a duplicate artist row into this one."
              onSelect={() => setTimeout(() => setToolsOpen(true), 0)}
            />
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Monitor for new releases</DropdownMenuLabel>
            {[["album", "Albums"], ["ep", "EPs"], ["single", "Singles"]].map(([value, label]) => (
              <DropdownMenuCheckboxItem
                key={value}
                checked={mtypes.has(value)}
                // Kept open: picking two of the three is one trip, not two.
                onSelect={(e) => e.preventDefault()}
                onCheckedChange={(v) => toggleMtype(value, v === true)}
              >
                {label}
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="flex flex-wrap items-start gap-4">
        {/* Shows a stand-in tile when there's no usable photo, and hides the
            artwork picker behind a hover button. */}
        <ArtistArtwork
          artistId={artist.id}
          name={artist.name}
          imageUrl={artist.image_url}
          locked={!!artist.image_locked}
        />
        <div>
          <h1 className="text-2xl font-bold">{artist.name}</h1>
          <div className="my-1 flex items-center gap-3">
            {/* Each service's own mark, in the version that suits the theme
                (see ServiceIcon). Quiet until hovered, like the ones on an
                album page: they're a way out of the app, not the subject. */}
            {artist.mbid && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <a href={`https://musicbrainz.org/artist/${artist.mbid}`} target="_blank" rel="noopener noreferrer">
                    <ServiceIcon name="musicbrainz" size={22} className={SERVICE_ICON} />
                  </a>
                </TooltipTrigger>
                <TooltipContent>MusicBrainz</TooltipContent>
              </Tooltip>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <a href={lastfmHref} target="_blank" rel="noopener noreferrer">
                  <ServiceIcon name="last-fm" size={22} className={SERVICE_ICON} />
                </a>
              </TooltipTrigger>
              <TooltipContent>Last.fm</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <a href={`https://music.youtube.com/search?q=${encodeURIComponent(artist.name)}`} target="_blank" rel="noopener noreferrer">
                  <ServiceIcon name="youtube" size={22} className={SERVICE_ICON} />
                </a>
              </TooltipTrigger>
              <TooltipContent>YouTube Music</TooltipContent>
            </Tooltip>
          </div>
          <p className="text-muted-foreground">
            {artist.track_count} tracks in library
            {artist.last_checked && <> <span className="mx-1">/</span> checked {artist.last_checked}</>}
          </p>
          {genreTags.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1.5">
              {genreTags.map((t) => (
                <Badge key={t} variant="outline" className="capitalize text-muted-foreground">
                  {t.replace(/[._]/g, " ")}
                </Badge>
              ))}
            </div>
          )}
          <div className="my-2 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={following ? "outline" : "default"}
              onClick={() => changeSub(following ? "none" : "subscribed")}
            >
              {following ? "Following" : "Follow"}
            </Button>
            {following && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label={notifyOn ? "Notifications on for this artist" : "Notify me about this artist"}
                    aria-pressed={notifyOn}
                    size="sm"
                    variant={notifyOn ? "default" : "outline"}
                    onClick={() => changeSub(notifyOn ? "subscribed" : "notify")}
                  >
                    {notifyOn ? <LuBellRing /> : <LuBell />}
                    {notifyOn ? "Notifying" : "Notify"}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {notifyOn
                    ? "You'll be notified when this artist releases something. Click to stop."
                    : "Get notified when this artist releases something."}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
          {mtypeResult && <p className="text-muted-foreground">{mtypeResult}</p>}
        </div>
      </div>

      <TopTracks artistId={id} />

      {artist.bio && (
        <div className="my-4 text-muted-foreground" dangerouslySetInnerHTML={{ __html: artist.bio }} />
      )}

      {similarArtists.length > 0 && (
        <div className="my-4 flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">
            Similar artists{similar?.source ? ` (via ${similar.source})` : ""}:
          </span>
          {/* Best matches first; the long tail is one click away, so a
              well-connected artist doesn't push the page down. */}
          {(showAllSimilar ? similarArtists : similarArtists.slice(0, SIMILAR_SHOWN)).map((s) => (
            <SimilarChip key={s.name} s={s} busy={openingSimilar === s.name} onOpen={openSimilar} />
          ))}
          {similarArtists.length > SIMILAR_SHOWN && (
            <Button size="xs" variant="ghost" onClick={() => setShowAllSimilar((v) => !v)}>
              {showAllSimilar
                ? "Show fewer"
                : `Show ${similarArtists.length - SIMILAR_SHOWN} more`}
            </Button>
          )}
        </div>
      )}

      {/* Both tools are modals now, opened from the menu above. */}
      <MergeTools
        artistId={id}
        mbid={artist.mbid}
        open={toolsOpen}
        onOpenChange={setToolsOpen}
        onMerged={() => window.location.reload()}
        onMatched={() => refetchDisco()}
      />
      <AlbumLinks
        artistId={id}
        open={linksOpen}
        onOpenChange={setLinksOpen}
        onChange={() => refetchDisco()}
      />

      <div className="mt-6 mb-2 flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold">
          Discography <span className="text-muted-foreground">{discoSource}</span>
        </h2>
        {disco && totalCount > 0 && (
          <Badge variant="secondary" className={ownedCount ? greenBadge : grayBadge}>
            {ownedCount}/{totalCount} owned
          </Badge>
        )}
        {/* The unique-song pass, while it reads one tracklist per release. */}
        {exclusives?.running && (
          <div className="ml-auto flex min-w-[10rem] items-center gap-2">
            <Progress value={uniqueProgress} className="w-24" />
            <span className="text-sm font-normal text-muted-foreground">
              {exclusives.progress?.total
                ? `checking releases ${exclusives.progress.done ?? 0}/${exclusives.progress.total}`
                : "checking releases"}
            </span>
          </div>
        )}
        {scanResult && !scanBusy && (
          <span className="text-sm font-normal text-muted-foreground">{scanResult}</span>
        )}
      </div>
      {!disco ? (
        <p className="text-muted-foreground">{discoMsg}</p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <Input
              className="h-8 min-w-[14rem] flex-1"
              type="search"
              placeholder="Filter albums..."
              value={discoSearch}
              onChange={(e) => setDiscoSearch(e.target.value)}
            />
            <ButtonGroup>
              {(["all", "owned", "unowned"] as const).map((f) => (
                <Button
                  key={f}
                  size="xs"
                  variant={ownedFilter === f ? "secondary" : "outline"}
                  onClick={() => setOwnedFilter(f)}
                >
                  {f === "all" ? "All" : f === "owned" ? "Owned" : "Not owned"}
                </Button>
              ))}
            </ButtonGroup>
          </div>

          <TableActionBar open={selCount > 0} count={selCount} onClear={() => setRowSelection({})}>
            <Button size="sm" variant="outline" onClick={() => bulkOwned(true)}>Mark owned</Button>
            <Button size="sm" variant="outline" onClick={() => bulkOwned(false)}>Mark not owned</Button>
          </TableActionBar>

          <Accordion type="multiple" defaultValue={openCats}>
            {([["album", "Albums"], ["ep", "EPs"], ["single", "Singles"]] as const).map(([key, label]) => {
              const st = statFor(key);
              return (
                <AccordionItem key={key} value={key}>
                  <AccordionTrigger>
                    <span className="flex-1 text-left text-sm font-semibold">
                      {label} <span className="font-normal text-muted-foreground">({st.total})</span>
                    </span>
                    {st.total > 0 && (
                      <Badge variant="secondary" className={"mr-2 " + (st.owned ? greenBadge : grayBadge)}>
                        {st.owned}/{st.total} owned
                      </Badge>
                    )}
                  </AccordionTrigger>
                  <AccordionContent>{renderTable(rowsFor(key))}</AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        </>
      )}
    </div>
  );
}

function MergeTools({
  artistId,
  mbid,
  open,
  onOpenChange,
  onMatched,
  onMerged,
}: {
  artistId: number;
  mbid: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onMatched: () => void;
  onMerged: () => void;
}) {
  const [matchLink, setMatchLink] = useState(mbid ? `https://musicbrainz.org/artist/${mbid}` : "");
  const [matchResult, setMatchResult] = useState("");
  const [mergeSearch, setMergeSearch] = useState("");
  const [matches, setMatches] = useState<Artist[]>([]);
  const [compare, setCompare] = useState<{ target: ArtistDetailT; source: ArtistDetailT } | null>(null);
  const [keepName, setKeepName] = useState<"target" | "source">("target");
  const [mergeResult, setMergeResult] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>();

  function doMatch() {
    const link = matchLink.trim();
    if (!link) return;
    setMatchResult("Matching...");
    api.setMbid(artistId, link).then((r: any) => {
      if (r.error) { setMatchResult(r.error); return; }
      setMatchResult("Matched: " + r.matched_name);
      onMatched();
    }).catch(() => setMatchResult("Failed."));
  }

  function onSearch(q: string) {
    setMergeSearch(q);
    setCompare(null);
    clearTimeout(timer.current);
    if (q.trim().length < 2) { setMatches([]); return; }
    timer.current = setTimeout(() => {
      api.artists({ ignored: "all", q: q.trim() }).then((d) => setMatches(d.artists.filter((a) => a.id !== artistId).slice(0, 20)));
    }, 250);
  }

  function showCompare(sourceId: number) {
    Promise.all([api.artist(artistId), api.artist(sourceId)]).then(([target, source]) => {
      setCompare({ target, source });
      setKeepName("target");
    });
  }

  function confirmMerge() {
    if (!compare) return;
    const name = keepName === "source" ? compare.source.name : compare.target.name;
    setMergeResult("Merging...");
    api.merge(artistId, [compare.source.id], name).then((r: any) => {
      if (r.error) { setMergeResult(r.error); return; }
      setMergeResult(`Merged into "${r.name}". Reloading...`);
      setTimeout(onMerged, 800);
    }).catch(() => setMergeResult("Failed."));
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="w-[min(92vw,42rem)] max-w-none">
        <AlertDialogHeader>
          <AlertDialogTitle>Match / merge artist</AlertDialogTitle>
          <AlertDialogDescription>
            Point this artist at the right MusicBrainz entry, or fold a duplicate of
            them into this one.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="max-h-[70vh] overflow-y-auto">
      <div className="mt-1">
        <p className="mb-1 font-semibold">Match to a MusicBrainz artist URL or ID</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="min-w-[18rem] flex-1" value={matchLink} onChange={(e) => setMatchLink(e.target.value)} placeholder="https://musicbrainz.org/artist/..." />
          <Button variant="outline" onClick={doMatch}>Match</Button>
          <span className="text-muted-foreground">{matchResult}</span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">Sets the MusicBrainz id used to fetch this artist's releases.</p>
      </div>

      <div className="mt-4">
        <p className="mb-1 font-semibold">Merge another artist into this one</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="min-w-[18rem] flex-1" type="search" value={mergeSearch} onChange={(e) => onSearch(e.target.value)} placeholder="Search your library..." />
          <span className="text-muted-foreground">{mergeResult}</span>
        </div>
        {matches.length > 0 && (
          <div className="mt-2 flex flex-col gap-1">
            {matches.map((a) => (
              <Button key={a.id} variant="ghost" className="justify-start" size="sm" onClick={() => showCompare(a.id)}>
                {a.name} <span className="ml-1 text-muted-foreground">({a.track_count || 0} tracks)</span>
              </Button>
            ))}
          </div>
        )}
        {compare && (
          <div className="mt-3">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <CompareCard a={compare.target} role="Keep (this artist)" />
              <CompareCard a={compare.source} role="Merge in & remove" />
            </div>
            <RadioGroup className="my-3 flex flex-wrap items-center gap-4" value={keepName} onValueChange={(v) => setKeepName(v as "target" | "source")}>
              <span className="font-semibold">Keep name:</span>
              <label className="flex cursor-pointer items-center gap-2">
                <RadioGroupItem value="target" />
                <span>{compare.target.name}</span>
              </label>
              {compare.source.name.toLowerCase() !== compare.target.name.toLowerCase() && (
                <label className="flex cursor-pointer items-center gap-2">
                  <RadioGroupItem value="source" />
                  <span>{compare.source.name}</span>
                </label>
              )}
            </RadioGroup>
            <div className="flex gap-2">
              <Button onClick={confirmMerge}>Merge these</Button>
              <Button variant="outline" onClick={() => setCompare(null)}>Cancel</Button>
            </div>
          </div>
        )}
        <p className="mt-2 text-sm text-muted-foreground">The other artist's tracks and releases move here; it is then removed.</p>
      </div>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Close</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function CompareCard({ a, role }: { a: ArtistDetailT; role: string }) {
  // Plenty of stored photos are links that no longer serve anything; a broken
  // image icon in a merge dialog reads as "this row is broken", so it hides.
  const [broken, setBroken] = useState(false);
  return (
    <div className="rounded-md border p-2.5">
      <p className="text-xs uppercase text-muted-foreground">{role}</p>
      {a.image_url && !broken && (
        <img
          src={art(a.image_url)}
          alt=""
          onError={() => setBroken(true)}
          className="my-1 h-[72px] w-[72px] rounded-md object-cover"
        />
      )}
      <p className="font-bold">{a.name}</p>
      <p className="text-muted-foreground">{a.track_count || 0} tracks · {a.releases ? a.releases.length : 0} releases</p>
      <p className="text-muted-foreground">MusicBrainz: {a.mbid ? "matched" : "none"}</p>
      <p className="text-muted-foreground">Status: {a.subscription || "none"}</p>
    </div>
  );
}
