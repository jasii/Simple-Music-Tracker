import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api";
import { AlbumArt } from "../components/AlbumArt";
import { DownloadsPanel } from "../components/DownloadsPanel";
import type { LibraryGap } from "../types";
import { useNav } from "../nav";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Switch } from "../components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

const PAGE = 50;
const TYPES = [
  { value: "album,ep,single", label: "All types" },
  { value: "album", label: "Albums" },
  { value: "ep", label: "EPs" },
  { value: "single", label: "Singles" },
];

function albumHref(r: LibraryGap): string {
  const qs = new URLSearchParams({ artist: r.artist, title: r.title, from: "missing" });
  if (r.mbid) qs.set("mbid", r.mbid);
  if (r.release_date) qs.set("date", r.release_date);
  if (r.image_url) qs.set("img", r.image_url);
  return "/album?" + qs.toString();
}

type Kind = "missing" | "incomplete" | "downloads";
const KINDS: Kind[] = ["missing", "incomplete", "downloads"];

const DESCRIPTIONS: Record<Kind, string> = {
  missing: "Releases your artists put out that the library doesn't have, from their cached discographies.",
  incomplete: "Albums the library holds fewer tracks of than MusicBrainz lists - a download that stopped short, usually.",
  downloads: "Soulseek downloads, followed file by file. Failed files are retried, then fetched from another peer.",
};

export default function Missing() {
  const nav = useNav();
  const [kind, setKind] = useState<Kind>(() => {
    try {
      const saved = localStorage.getItem("missingTab") as Kind;
      return KINDS.includes(saved) ? saved : "missing";
    } catch { return "missing"; }
  });
  const isGaps = kind !== "downloads";
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [types, setTypes] = useState("album,ep,single");
  const [sort, setSort] = useState("artist");
  // MusicBrainz files every reissue, live set and compilation as its own
  // release group, which is most of the list; they're hidden by default.
  const [withReissues, setWithReissues] = useState(() => {
    try { return localStorage.getItem("missingReissues") === "true"; } catch { return false; }
  });
  const [page, setPage] = useState(0);

  // Debounced because this filter runs server-side over the whole sweep.
  useEffect(() => {
    const t = setTimeout(() => { setDebounced(search); setPage(0); }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const params = useMemo(
    () => ({
      q: debounced.trim(),
      types,
      sort,
      include_secondary: withReissues ? 1 : 0,
      limit: PAGE,
      offset: page * PAGE,
    }),
    [debounced, types, sort, withReissues, page],
  );
  // The downloads tab has its own list; the counts in the tab labels still
  // come from whichever gaps list was open last.
  const gapKind = isGaps ? kind : "missing";
  const { data, isPending, refetch, isFetching } = useQuery({
    queryKey: ["libraryGaps", gapKind, params],
    queryFn: () => api.libraryGaps(gapKind, params),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
    refetchInterval: (query) => (query.state.data?.rebuilding ? 3000 : false),
  });

  function changeKind(v: string) {
    const next = v as Kind;
    setKind(next);
    setPage(0);
    try { localStorage.setItem("missingTab", next); } catch {}
  }

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const pages = Math.ceil(total / PAGE);
  const shownFrom = total === 0 ? 0 : page * PAGE + 1;
  const shownTo = Math.min(total, (page + 1) * PAGE);

  return (
    <div>
      <Tabs value={kind} onValueChange={changeKind}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold">Missing</h1>
          {/* Four tabs don't fit a phone: the strip scrolls rather than the page. */}
          <div className="max-w-full overflow-x-auto">
          <TabsList>
            <TabsTrigger value="missing">
              Not owned{data ? ` (${data.missing_total})` : ""}
            </TabsTrigger>
            <TabsTrigger value="incomplete">
              Incomplete{data?.incomplete_total != null ? ` (${data.incomplete_total})` : ""}
            </TabsTrigger>
            <TabsTrigger value="downloads">Downloads</TabsTrigger>
          </TabsList>
          </div>
        </div>
        {!nav.hide_page_descriptions && (
          <p className="text-muted-foreground">{DESCRIPTIONS[kind]}</p>
        )}

        {isGaps ? (<>
        <div className="my-3 flex flex-wrap items-center gap-2">
          <Input
            className="min-w-[14rem] flex-1"
            type="search"
            placeholder="Filter by artist or album..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select value={types} onValueChange={(v) => { setTypes(v); setPage(0); }}>
            <SelectTrigger className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>
              {TYPES.map((t) => (
                <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={sort} onValueChange={(v) => { setSort(v); setPage(0); }}>
            <SelectTrigger className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="artist">Sort: Artist</SelectItem>
              <SelectItem value="newest">Sort: Newest</SelectItem>
              <SelectItem value="oldest">Sort: Oldest</SelectItem>
            </SelectContent>
          </Select>
          <div className="flex items-center gap-2">
            <Switch
              id="missing-reissues"
              checked={withReissues}
              onCheckedChange={(v) => {
                setWithReissues(v);
                setPage(0);
                try { localStorage.setItem("missingReissues", String(v)); } catch {}
              }}
            />
            <Label htmlFor="missing-reissues">Compilations &amp; live</Label>
          </div>
          <Button variant="outline" disabled={isFetching} onClick={() => refetch()}>
            Rescan
          </Button>
        </div>

        <p className="mb-2 text-sm text-muted-foreground">
          {isPending
            ? "Working it out..."
            : `${shownFrom}-${shownTo} of ${total}`}
          {data?.rebuilding && " · rebuilding the list in the background..."}
          {data && !data.grab_ready && " · configure a download client in Settings to grab"}
        </p>

        <TabsContent value={kind} className="mt-0">
          <div className="divide-y">
            {rows.map((r) => (
              <GapRow
                key={`${r.artist_id}-${r.mbid || r.title}`}
                r={r}
                grabbable={!!data?.grab_ready}
                incomplete={kind === "incomplete"}
              />
            ))}
          </div>
          {!isPending && !rows.length && (
            <p className="text-muted-foreground">
              {kind === "missing"
                ? "Nothing missing - or no discographies cached yet. Open a few artist pages first."
                : "No short albums found. Track counts come from a library scan, and each album's tracklist is fetched from MusicBrainz a few at a time, so this fills in over the first hours."}
            </p>
          )}
        </TabsContent>

        {pages > 1 && (
          <div className="mt-3 flex items-center gap-2">
            <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">Page {page + 1} of {pages}</span>
            <Button
              size="sm"
              variant="outline"
              disabled={page + 1 >= pages}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        )}
        </>) : (
          <TabsContent value="downloads" className="mt-3">
            <DownloadsPanel />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}

// One missing or incomplete release: art, what it is, and a one-click grab
// through the configured download client.
function GapRow({
  r,
  grabbable,
  incomplete,
}: {
  r: LibraryGap;
  grabbable: boolean;
  incomplete: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  function grabMissing() {
    setBusy(true);
    api
      .grabMissing({
        artist: r.artist,
        title: r.title,
        mbid: r.mbid ?? undefined,
        owned_format: r.owned_format,
      })
      .then((res) => {
        if (res.error) {
          toast.error(res.error);
          return;
        }
        setDone(true);
        toast.success(`${r.artist} - ${r.title}: ${res.message ?? "sent"}`);
      })
      .catch(() => toast.error("Couldn't fetch the missing tracks."))
      .finally(() => setBusy(false));
  }

  function grab() {
    setBusy(true);
    api
      .grab(r.artist, r.title)
      .then((res) => {
        if (res.error) {
          toast.error(res.error);
          return;
        }
        setDone(true);
        toast.success(`${r.artist} - ${r.title}: ${res.message ?? "sent"} (${res.client})`);
      })
      .catch(() => toast.error("Grab failed."))
      .finally(() => setBusy(false));
  }

  return (
    <div className="flex items-center gap-3 py-2.5">
      <AlbumArt src={r.image_url} boxSize="64px" rounded="md" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          <RouterLink to={albumHref(r)} className="hover:underline">{r.title}</RouterLink>
        </p>
        <p className="text-sm text-muted-foreground">
          <RouterLink to={`/artist/${r.artist_id}`} className="hover:underline">{r.artist}</RouterLink>
          {r.year ? ` · ${r.year}` : ""}
          {` · ${r.type.toUpperCase()}`}
          {r.owned_format ? ` · you have ${r.owned_format}` : ""}
          {r.total_tracks ? ` · ${r.have_tracks} of ${r.total_tracks} tracks` : ""}
        </p>
      </div>
      {grabbable && (
        <div className="flex-none">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="xs"
                variant={done ? "outline" : "default"}
                disabled={busy || done}
                onClick={incomplete ? grabMissing : grab}
              >
                {done
                  ? "Sent"
                  : busy
                    ? incomplete ? "Checking..." : "Searching..."
                    : incomplete ? "Get missing" : "Grab"}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {incomplete
                ? "Find which tracks the library lacks and fetch just those"
                : "Search your download client for it and fetch it"}
            </TooltipContent>
          </Tooltip>
        </div>
      )}
    </div>
  );
}
