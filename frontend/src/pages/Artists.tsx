import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper, type RowSelectionState } from "@tanstack/react-table";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { BoxCheck } from "../components/BoxCheck";
import { DataTable, type PageSizeOption } from "../components/DataTable";
import { AlbumArt } from "../components/AlbumArt";
import {
  ArtworkReviewDialog,
  useArtworkReviewCount,
} from "../components/ArtworkReview";
import { MergeSuggestions } from "../components/MergeSuggestions";
import { TableActionBar } from "../components/TableActionBar";
import type { Artist, Stats, Subscription } from "../types";
import { Alert, AlertDescription } from "../components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "../components/ui/alert-dialog";
import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { ToolItem } from "../components/ToolItem";
import { Input } from "../components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import {
  CircleStop,
  FolderSearch,
  ImageOff,
  RefreshCw,
  Settings2,
  Zap,
} from "lucide-react";

const PAGE_SIZES: PageSizeOption[] = [
  { label: "25", value: "25" },
  { label: "50", value: "50" },
  { label: "100", value: "100" },
  { label: "200", value: "200" },
  { label: "All", value: "all" },
];

const col = createColumnHelper<Artist>();

export default function Artists() {
  const qc = useQueryClient();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("name");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  // Only one library scan runs at a time, so the buttons become a Cancel.
  const [scanning, setScanning] = useState(false);
  // True while the followed-artist refresh is working, so the menu can say so.
  const [refreshing, setRefreshing] = useState(false);
  const [artworkOpen, setArtworkOpen] = useState(false);
  const [selectedArtworkOpen, setSelectedArtworkOpen] = useState(false);
  // What the Tools menu says about the artwork that needs a person.
  const artwork = useArtworkReviewCount();

  // Add-by-link state.
  const [mbLink, setMbLink] = useState("");
  const [mbState, setMbState] = useState<Subscription>("subscribed");
  const [mbResult, setMbResult] = useState("");
  const [mbBusy, setMbBusy] = useState(false);

  // One fetch per subscription filter; search and sort are applied below,
  // client-side, so typing is instant and costs no requests.
  const artistsKey = ["artists", { filter }] as const;
  const { data: artists = [], isPending: loading } = useQuery({
    queryKey: artistsKey,
    queryFn: () => {
      const params: Record<string, string> = {};
      if (filter) params.subscription = filter;
      return api.artists(params).then((d) => d.artists);
    },
    // Keep the previous list visible while a new query loads -- no blank flash
    // when changing the filter.
    placeholderData: keepPreviousData,
  });
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: () => api.stats() });

  const reloadArtists = () => qc.invalidateQueries({ queryKey: ["artists"] });
  const reloadStats = () => qc.invalidateQueries({ queryKey: ["stats"] });

  useEffect(() => {
    // Resume a running scan/refresh on mount.
    api.scanStatus().then((s) => { if (s.running) { setScanning(true); pollScan(); } }).catch(() => {});
    api.refreshStatus().then((s) => { if (s.running) { setRefreshing(true); pollRefresh(); } }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function setSubscription(id: number, state: Subscription) {
    await api.setSubscription(id, state);
    qc.setQueryData<Artist[]>(artistsKey, (prev) =>
      prev?.map((a) => (a.id === id ? { ...a, subscription: state } : a)),
    );
    reloadStats();
  }

  function toggleSub(a: Artist, checked: boolean) {
    const notify = a.subscription === "notify";
    const state: Subscription = checked ? (notify ? "notify" : "subscribed") : "none";
    setSubscription(a.id, state);
  }

  function toggleNotify(a: Artist, checked: boolean) {
    const subbed = a.subscription === "subscribed" || a.subscription === "notify";
    const state: Subscription = checked ? "notify" : subbed ? "subscribed" : "none";
    setSubscription(a.id, state);
  }

  function deselect(id: number) {
    setRowSelection((prev) => {
      const next = { ...prev };
      delete next[String(id)];
      return next;
    });
  }

  function ignoreOne(id: number) {
    api.setIgnore(id, true).then(() => {
      qc.setQueryData<Artist[]>(artistsKey, (prev) => prev?.filter((x) => x.id !== id));
      deselect(id);
      reloadStats();
    });
  }

  const selectedIds = Object.keys(rowSelection).map(Number);
  const selCount = selectedIds.length;

  function bulkSubscription(state: Subscription) {
    api.bulkSubscription(selectedIds, state).then(() => {
      setRowSelection({});
      reloadArtists();
      reloadStats();
    });
  }
  function bulkIgnore() {
    api.bulkIgnore(selectedIds, true).then(() => {
      setRowSelection({});
      reloadArtists();
      reloadStats();
    });
  }

  // The same jobs the page-wide Tools menu offers, aimed at the selection.
  const [bulkBusy, setBulkBusy] = useState("");
  function refreshSelected() {
    setBulkBusy("refresh");
    api.refreshArtists(selectedIds)
      .then((r) => {
        setProgress(`Queued ${r.queued} artist${r.queued === 1 ? "" : "s"} for refresh...`);
        setRefreshing(true);
        setTimeout(pollRefresh, 800);
      })
      .finally(() => setBulkBusy(""));
  }
  function rescanSelected() {
    setBulkBusy("scan");
    setProgress(`Rescanning ${selCount} artist${selCount === 1 ? "" : "s"}...`);
    api.scanArtists(selectedIds)
      .then((r) => {
        setProgress(
          `Rescanned ${r.scanned} artist${r.scanned === 1 ? "" : "s"}` +
          (r.failed ? `, ${r.failed} failed` : "") + ".",
        );
        reloadArtists();
        reloadStats();
        setTimeout(() => setProgress(null), 4000);
      })
      .finally(() => setBulkBusy(""));
  }

  // --- scan / refresh polling ---
  const scanTick = useRef(0);
  function pollScan() {
    api.scanStatus().then((s: any) => {
      if (s.running) {
        setScanning(true);
        setProgress(
          s.state === "queued"
            ? `Waiting for the running scan to finish (${s.position} ahead)...`
            : (s.mode === "quick" ? "Quick scan" : "Full scan") +
              `: ${s.files_seen} files seen, ${s.artists_found} artists. ${s.message || ""}`,
        );
        scanTick.current += 1;
        if (scanTick.current % 3 === 0) { reloadArtists(); reloadStats(); }
        setTimeout(pollScan, 1000);
      } else {
        setScanning(false);
        setProgress("Scan finished: " + (s.message || ""));
        scanTick.current = 0;
        reloadArtists();
        reloadStats();
        setTimeout(() => setProgress(null), 4000);
      }
    });
  }
  function pollRefresh() {
    api.refreshStatus().then((s: any) => {
      if (s.running) {
        const now = s.current
          ? ` (on: ${s.current}${s.elapsed ? " " + s.elapsed + "s" : ""})`
          : s.message ? ` (last: ${s.message})` : "";
        setProgress(`Refreshing following: ${s.queued || 0} queued, ${s.processed || 0} done${now}`);
        setRefreshing(true);
        setTimeout(pollRefresh, 2000);
      } else {
        setRefreshing(false);
        setProgress("Refresh finished.");
        reloadStats();
        setTimeout(() => setProgress(null), 4000);
      }
    });
  }

  function startScan(quick: boolean) {
    api.scan(quick).then((r: any) => {
      if (r.error) {
        setProgress(r.error);
        // Already queued or running elsewhere: follow that one instead.
        if (r.state) { setScanning(true); setTimeout(pollScan, 800); }
        return;
      }
      setScanning(true);
      setProgress(
        r.state === "queued"
          ? `Queued behind ${r.position} scan${r.position === 1 ? "" : "s"}...`
          : `${quick ? "Quick" : "Full"} scan started...`,
      );
      setTimeout(pollScan, 800);
    });
  }
  function cancelScan() {
    setProgress("Cancelling scan...");
    api.scanCancel().finally(() => setTimeout(pollScan, 500));
  }
  function startRefresh() {
    api.refreshAll().then((r: any) => {
      setProgress(r.error ? r.error : "Refresh started...");
      if (!r.error) { setRefreshing(true); setTimeout(pollRefresh, 800); }
    });
  }

  function addByLink() {
    const link = mbLink.trim();
    if (!link) return;
    setMbBusy(true);
    setMbResult("Looking up...");
    api
      .addByLink(link, mbState)
      .then((r) => {
        if (r.error) {
          setMbResult(r.error);
        } else {
          setMbResult(`${r.created ? "Added " : "Now following "}${r.name}. Fetching releases...`);
          setMbLink("");
          reloadArtists();
          reloadStats();
        }
      })
      .catch(() => setMbResult("Failed to add artist."))
      .finally(() => setMbBusy(false));
  }

  const stat = (k: keyof Stats) => (stats ? stats[k] : "-");

  // Search / "has missing" / sort, all over the list already in memory.
  const displayed = useMemo(() => {
    const q = search.trim().toLowerCase();
    let rows = q ? artists.filter((a) => a.name.toLowerCase().includes(q)) : artists;
    if (onlyMissing) rows = rows.filter(hasMissing);
    const byName = (a: Artist, b: Artist) => a.sort_name.localeCompare(b.sort_name);
    rows = [...rows].sort(
      sort === "recent"
        ? (a, b) => (b.last_checked ?? "").localeCompare(a.last_checked ?? "") || byName(a, b)
        : byName,
    );
    return rows;
  }, [artists, search, onlyMissing, sort]);

  // TanStack column defs. Sorting/filtering stay server-side (the toolbar below
  // drives the query), so columns are not click-sortable here.
  const columns = [
    col.display({
      id: "select",
      header: ({ table }) => (
        <BoxCheck
          checked={table.getIsAllRowsSelected()}
          onChange={(v) => table.toggleAllRowsSelected(v)}
          label="Select all"
        />
      ),
      cell: ({ row }) => (
        <BoxCheck
          checked={row.getIsSelected()}
          onChange={(v) => row.toggleSelected(v)}
          label={`Select ${row.original.name}`}
        />
      ),
      meta: { width: "3rem", align: "center" },
    }),
    col.accessor("name", {
      header: "Artist",
      enableSorting: false,
      cell: ({ row }) => (
        // Same 40px thumbnail the Following page uses, so an artist looks the
        // same wherever they're listed. AlbumArt keeps its vinyl placeholder
        // when the photo is missing or its link has gone dead.
        <div className="flex items-center gap-3">
          <AlbumArt src={row.original.image_url} boxSize="40px" rounded="md" />
          <RouterLink to={`/artist/${row.original.id}`} className="hover:underline">
            {row.original.name}
          </RouterLink>
        </div>
      ),
    }),
    col.accessor((a) => (monitorsType(a, "album") ? missingOf(a.owned_albums, a.disc_albums) : undefined), {
      id: "albums",
      header: "Albums",
      sortUndefined: "last",
      sortDescFirst: true,
      cell: ({ row }) => (
        <OwnedTotal owned={row.original.owned_albums} total={row.original.disc_albums} monitored={monitorsType(row.original, "album")} />
      ),
      meta: { width: "5.5rem", align: "end" },
    }),
    col.accessor((a) => (monitorsType(a, "ep") ? missingOf(a.owned_eps, a.disc_eps) : undefined), {
      id: "eps",
      header: "EPs",
      sortUndefined: "last",
      sortDescFirst: true,
      cell: ({ row }) => (
        <OwnedTotal owned={row.original.owned_eps} total={row.original.disc_eps} monitored={monitorsType(row.original, "ep")} />
      ),
      meta: { width: "5rem", align: "end" },
    }),
    col.accessor((a) => (monitorsType(a, "single") ? missingOf(a.owned_singles, a.disc_singles) : undefined), {
      id: "singles",
      header: "Singles",
      sortUndefined: "last",
      sortDescFirst: true,
      cell: ({ row }) => (
        <OwnedTotal owned={row.original.owned_singles} total={row.original.disc_singles} monitored={monitorsType(row.original, "single")} />
      ),
      meta: { width: "5.5rem", align: "end" },
    }),
    col.display({
      id: "follow",
      header: "Follow",
      cell: ({ row }) => {
        const a = row.original;
        const checked = a.subscription === "subscribed" || a.subscription === "notify";
        return <BoxCheck checked={checked} onChange={(v) => toggleSub(a, v)} label={`Follow ${a.name}`} />;
      },
      meta: { width: "3rem", align: "center" },
    }),
    col.display({
      id: "notify",
      header: "Notify",
      cell: ({ row }) => {
        const a = row.original;
        return (
          <BoxCheck
            checked={a.subscription === "notify"}
            onChange={(v) => toggleNotify(a, v)}
            label={`Notify for ${a.name}`}
          />
        );
      },
      meta: { width: "3rem", align: "center" },
    }),
    col.display({
      id: "ignore",
      header: "Ignore",
      cell: ({ row }) => (
        <AlertDialog>
          <Tooltip>
            <TooltipTrigger asChild>
              <AlertDialogTrigger asChild>
                <Button size="xs" variant="ghost" className="text-muted-foreground">Ignore</Button>
              </AlertDialogTrigger>
            </TooltipTrigger>
            <TooltipContent>Hide this artist</TooltipContent>
          </Tooltip>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Ignore {row.original.name}?</AlertDialogTitle>
              <AlertDialogDescription>
                This hides {row.original.name} from your library and lists. You can restore them later from the{" "}
                <RouterLink to="/ignored" className="underline">Ignored</RouterLink> page.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => ignoreOne(row.original.id)}>Ignore</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ),
      meta: { width: "4.5rem", align: "center" },
    }),
  ];

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Artists</h1>

      <p className="mb-3 text-muted-foreground">
        {stat("visible")} artists <Sep /> {stat("following")} following <Sep />{" "}
        {stat("upcoming_month")} upcoming this month <Sep />{" "}
        <RouterLink to="/ignored" className="hover:underline">{stat("ignored")} ignored</RouterLink>
      </p>

      <MergeSuggestions onMerged={() => { reloadArtists(); reloadStats(); }} />
      {/* Opened from the Tools menu; appears there once an artwork pass has run. */}
      <ArtworkReviewDialog open={artworkOpen} onOpenChange={setArtworkOpen} />
      {/* The same review, narrowed to the rows that are ticked. */}
      <ArtworkReviewDialog
        open={selectedArtworkOpen}
        onOpenChange={setSelectedArtworkOpen}
        ids={selectedIds}
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="min-w-[14rem] flex-1"
          type="search"
          placeholder="Filter artists..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Select value={filter || "all"} onValueChange={(v) => setFilter(v === "all" ? "" : v)}>
          <SelectTrigger className="w-auto"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="following">Following</SelectItem>
            <SelectItem value="subscribed">Follow only</SelectItem>
            <SelectItem value="notify">Notify</SelectItem>
            <SelectItem value="none">Not followed</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="w-auto"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="name">Sort: Name</SelectItem>
            <SelectItem value="recent">Sort: Last checked</SelectItem>
          </SelectContent>
        </Select>
        <Button
          variant={onlyMissing ? "secondary" : "outline"}
          onClick={() => setOnlyMissing((v) => !v)}
        >
          Has missing
        </Button>
        {/* One menu for the jobs this page can start, each saying what it
            actually touches: two of them read local files and the third is the
            one that goes looking for missing artist information online. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button variant="outline">
              <Settings2 aria-hidden /> Tools
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-[22rem]">
            <DropdownMenuLabel>Artist information</DropdownMenuLabel>
            <ToolItem
              icon={<RefreshCw size={15} aria-hidden />}
              title={refreshing ? "Refreshing following..." : "Refresh following"}
              description="Asks MusicBrainz and Last.fm about every artist you follow: new releases, bios, images, genres. This is the one that fills in missing artist information."
              disabled={refreshing}
              onSelect={startRefresh}
            />
            {artwork.ready && (artwork.total > 0 || artwork.dismissed > 0) && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuLabel>Artwork</DropdownMenuLabel>
                <ToolItem
                  icon={<ImageOff size={15} aria-hidden />}
                  title={artwork.total ? `Find artwork (${artwork.total})` : "Find artwork"}
                  description={
                    artwork.total
                      ? `${artwork.total} ${artwork.total === 1 ? "artist has" : "artists have"} no artwork the app can load - pick one by hand, or set them aside.`
                      : "Nothing left to find, but the ones you set aside are here."
                  }
                  onSelect={() => setTimeout(() => setArtworkOpen(true), 0)}
                />
              </>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Music folder</DropdownMenuLabel>
            {scanning ? (
              <ToolItem
                icon={<CircleStop size={15} aria-hidden />}
                title="Cancel scan"
                description="Stop the running folder scan, or drop it from the queue."
                onSelect={cancelScan}
              />
            ) : (
              <>
                <ToolItem
                  icon={<FolderSearch size={15} aria-hidden />}
                  title="Full scan"
                  description="Re-reads every audio file in your music directory and rebuilds artist and track counts. Navidrome and Plex are scanned from Settings, not here."
                  onSelect={() => startScan(false)}
                />
                <ToolItem
                  icon={<Zap size={15} aria-hidden />}
                  title="Quick scan"
                  description="Same walk, but only files added or changed since the last scan - the fast one after dropping in a new album."
                  onSelect={() => startScan(true)}
                />
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Input
          className="min-w-[18rem] flex-1"
          placeholder="Monitor an artist by MusicBrainz link or ID..."
          value={mbLink}
          onChange={(e) => setMbLink(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addByLink(); } }}
        />
        <Select value={mbState} onValueChange={(v) => setMbState(v as Subscription)}>
          <SelectTrigger className="w-auto"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="subscribed">Follow</SelectItem>
            <SelectItem value="notify">Follow + Notify</SelectItem>
          </SelectContent>
        </Select>
        <Button onClick={addByLink} disabled={mbBusy}>Add</Button>
        <span className="text-muted-foreground">{mbResult}</span>
      </div>

      {progress && (
        <Alert className="mb-3">
          <AlertDescription>{progress}</AlertDescription>
        </Alert>
      )}

      <TableActionBar open={selCount > 0} count={selCount} onClear={() => setRowSelection({})}>
        <Button size="sm" variant="outline" onClick={() => bulkSubscription("subscribed")}>Follow</Button>
        <Button size="sm" variant="outline" onClick={() => bulkSubscription("notify")}>Follow + Notify</Button>
        <Button size="sm" variant="outline" onClick={() => bulkSubscription("none")}>Unfollow</Button>
        <Button size="sm" variant="outline" onClick={bulkIgnore}>Ignore</Button>
        {/* The page's own tools, narrowed to what's ticked. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="outline">
              <Settings2 aria-hidden /> Tools
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-w-[22rem]">
            <DropdownMenuLabel>
              For the {selCount} selected
            </DropdownMenuLabel>
            <ToolItem
              icon={<RefreshCw size={15} aria-hidden />}
              title={bulkBusy === "refresh" ? "Queueing..." : "Refresh these artists"}
              description="Asks MusicBrainz and Last.fm about just these: new releases, bios, images, genres."
              disabled={!!bulkBusy}
              onSelect={refreshSelected}
            />
            <ToolItem
              icon={<FolderSearch size={15} aria-hidden />}
              title={bulkBusy === "scan" ? "Rescanning..." : "Rescan what you own"}
              description="Re-checks your libraries for these artists' albums and track counts. Scoped to their own folders, so it's quick."
              disabled={!!bulkBusy}
              onSelect={rescanSelected}
            />
            <ToolItem
              icon={<ImageOff size={15} aria-hidden />}
              title="Find artwork for these"
              description="Shows whatever the sources offer for the ones without a working picture, to pick by hand."
              onSelect={() => setTimeout(() => setSelectedArtworkOpen(true), 0)}
            />
          </DropdownMenuContent>
        </DropdownMenu>
      </TableActionBar>

      <DataTable
        data={displayed}
        columns={columns}
        getRowId={(a) => String(a.id)}
        enableRowSelection
        rowSelection={rowSelection}
        onRowSelectionChange={setRowSelection}
        loading={loading}
        emptyText={onlyMissing ? "No followed artists are missing releases." : "No artists. Run a library scan from the toolbar."}
        initialPageSize={50}
        pageSizeOptions={PAGE_SIZES}
        summary={(shown, total) => (total ? `Showing ${shown} of ${total} artists` : "")}
      />
    </div>
  );
}

function Sep() {
  return <span className="mx-1.5 text-muted-foreground">/</span>;
}

// Whether an artist monitors a release type (monitor_types is a comma list of
// album,ep,single). Unmonitored types are shown as a dash, not owned/total.
function monitorsType(a: Artist, type: string): boolean {
  return (a.monitor_types || "").split(",").includes(type);
}

// Missing count for a type: total - owned. Undefined until owned/total are
// synced (owned is null even on rows whose disc_* was cached by older code),
// so those rows sort last and read as unknown rather than "owns none".
function missingOf(owned?: number | null, total?: number | null): number | undefined {
  if (owned == null || total == null) return undefined;
  return total - owned;
}

function hasMissing(a: Artist): boolean {
  return (
    (monitorsType(a, "album") ? (missingOf(a.owned_albums, a.disc_albums) ?? 0) : 0) > 0 ||
    (monitorsType(a, "ep") ? (missingOf(a.owned_eps, a.disc_eps) ?? 0) : 0) > 0 ||
    (monitorsType(a, "single") ? (missingOf(a.owned_singles, a.disc_singles) ?? 0) : 0) > 0
  );
}

// "owned/total" cell. Dash when the type isn't monitored or isn't synced yet
// (owned null); amber when something's missing, muted once fully owned.
function OwnedTotal({
  owned,
  total,
  monitored,
}: {
  owned?: number | null;
  total?: number | null;
  monitored: boolean;
}) {
  if (!monitored || owned == null || total == null)
    return <span className="text-muted-foreground">-</span>;
  const cls = total - owned > 0 ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground";
  return <span className={cls}>{owned}/{total}</span>;
}
