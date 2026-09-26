import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { LuBell, LuBellRing, LuEyeOff, LuUserX } from "react-icons/lu";
import { api } from "../api";
import { AlbumArt } from "../components/AlbumArt";
import { ArtistLink, linkArtistNames, useOpenArtist } from "../components/ArtistLink";
import type { DiscoverItem, DiscoverSourceStatus, DiscoverSourceTag, LastfmArtist, SimilarRanking } from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
  useComboboxAnchor,
} from "../components/ui/combobox";
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
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Label } from "../components/ui/label";
import { Separator } from "../components/ui/separator";
import { Switch } from "../components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { ReleaseIcons } from "../lib/format";

// Relative "time since" for a unix-epoch-seconds timestamp (mirrors Settings).
function fmtAgo(epoch?: number | null): string {
  if (!epoch) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Per-source badge colors (replaces the old Chakra colorPalette).
const SRC_BADGE: Record<string, string> = {
  lastfm: "bg-red-500/15 text-red-600 dark:text-red-400",
  metacritic: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
  aoty: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
  iing: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
};
const srcBadge = (key?: string) => SRC_BADGE[key as string] ?? "bg-muted text-muted-foreground";
// Calendar day-dot colors per source.
const SRC_DOT: Record<string, string> = {
  lastfm: "bg-red-500",
  metacritic: "bg-yellow-500",
  aoty: "bg-blue-500",
  iing: "bg-emerald-500",
};

function itemSources(r: DiscoverItem): DiscoverSourceTag[] {
  return r.sources && r.sources.length ? r.sources : [{ key: r.source, label: r.source_label }];
}

function albumHref(r: DiscoverItem): string | null {
  if (!r.artist || !r.album) return null;
  const qs = new URLSearchParams({ artist: r.artist, title: r.album, from: "discover" });
  if (r.mbid) qs.set("mbid", r.mbid);
  const d = r.normalized_date || r.release_date;
  if (d) qs.set("date", d);
  if (r.image) qs.set("img", r.image);
  return "/album?" + qs.toString();
}

type DiscoverCache = { sources: DiscoverSourceStatus[]; items: DiscoverItem[] };

export default function Discover() {
  const nav = useNav();
  const qc = useQueryClient();
  // Seed from the cross-navigation cache so returning to Discover paints
  // instantly instead of re-blanking to "Loading...".
  const cached = qc.getQueryData<DiscoverCache>(["discover"]);
  const [items, setItems] = useState<DiscoverItem[]>(cached?.items ?? []);
  const [sources, setSources] = useState<DiscoverSourceStatus[]>(cached?.sources ?? []);
  const [loaded, setLoaded] = useState(!!cached);
  const [loadingMsg, setLoadingMsg] = useState<string | null>(cached ? null : "Loading...");
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("discoverHidden") || "[]")); } catch { return new Set(); }
  });
  const [view, setView] = useState<"agenda" | "calendar">(() => {
    try { return (localStorage.getItem("discoverView") as "agenda" | "calendar") || "agenda"; } catch { return "agenda"; }
  });
  const [tab, setTab] = useState<string>(() => {
    try { return localStorage.getItem("discoverTab") || "releases"; } catch { return "releases"; }
  });
  // Agenda only: whether weeks before the current one are shown.
  const [showPast, setShowPast] = useState<boolean>(() => {
    try { return localStorage.getItem("discoverShowPast") !== "false"; } catch { return true; }
  });
  // Hide artists already followed. Artists followed *during this session* stay
  // visible (so the "Following" confirmation isn't yanked away mid-click);
  // they drop out on the next feed load.
  const [hideFollowed, setHideFollowed] = useState<boolean>(() => {
    try { return localStorage.getItem("discoverHideFollowed") === "true"; } catch { return false; }
  });
  // Hide releases the library already owns (album-level, not just the artist).
  const [hideOwned, setHideOwned] = useState<boolean>(() => {
    try { return localStorage.getItem("discoverHideOwned") === "true"; } catch { return false; }
  });
  const [justFollowed, setJustFollowed] = useState<Set<string>>(new Set());
  const pollTimer = useRef<ReturnType<typeof setTimeout>>();
  const sourceAnchor = useComboboxAnchor();

  const load = useCallback(
    (refresh?: string | false, poll = false) => {
      // Only blank the list on the very first load. Refreshes keep the current
      // items on screen (status line shows progress) so the window doesn't flash.
      if (!poll && !loaded) setLoadingMsg("Loading...");
      const q = refresh ? "?refresh=" + encodeURIComponent(refresh) : "";
      api
        .getJSON<{ sources: DiscoverSourceStatus[]; items: DiscoverItem[]; count: number; refreshing: boolean }>(
          "/api/discover/releases" + q,
        )
        .then((data) => {
          const srcs = data.sources || [];
          const its = data.items || [];
          setSources(srcs);
          setItems(its);
          setLoaded(true);
          setLoadingMsg(null);
          // Persist across navigation so the next visit shows cached results.
          qc.setQueryData<DiscoverCache>(["discover"], { sources: srcs, items: its });
          if (pollTimer.current) clearTimeout(pollTimer.current);
          if (data.refreshing) pollTimer.current = setTimeout(() => load(undefined, true), 5000);
        })
        .catch(() => {
          if (!poll && !loaded) setLoadingMsg("Failed to load.");
        });
    },
    [loaded, qc],
  );

  useEffect(() => {
    // Refresh in the background; stay silent when we already have cached data.
    load(false, !!cached);
    return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The source filter is a shadcn multi-select combobox where selected = visible.
  // Map the chosen labels back onto the `hidden` set (keyed by source key),
  // preserving any hidden entries for sources that aren't currently configured.
  function changeVisibleSources(labels: string[]) {
    const keep = new Set(labels);
    setHidden((prev) => {
      const next = new Set(prev);
      for (const s of sources) {
        if (!s.configured) continue;
        if (keep.has(s.label)) next.delete(s.key);
        else next.add(s.key);
      }
      try { localStorage.setItem("discoverHidden", JSON.stringify(Array.from(next))); } catch {}
      return next;
    });
  }
  function changeView(v: "agenda" | "calendar") {
    setView(v);
    try { localStorage.setItem("discoverView", v); } catch {}
  }
  function changeTab(v: string) {
    setTab(v);
    try { localStorage.setItem("discoverTab", v); } catch {}
  }
  function changeShowPast(v: boolean) {
    setShowPast(v);
    try { localStorage.setItem("discoverShowPast", String(v)); } catch {}
  }
  function changeHideFollowed(v: boolean) {
    setHideFollowed(v);
    try { localStorage.setItem("discoverHideFollowed", String(v)); } catch {}
  }
  function changeHideOwned(v: boolean) {
    setHideOwned(v);
    try { localStorage.setItem("discoverHideOwned", String(v)); } catch {}
  }

  function setFollowing(artist: string, following: boolean) {
    setItems((prev) =>
      prev.map((it) => ((it.artist || "").toLowerCase() === artist.toLowerCase() ? { ...it, following } : it)),
    );
  }
  function follow(artist: string) {
    return api.trackByName(artist, "subscribed").then((r: any) => {
      if (!r.error) {
        setFollowing(artist, true);
        // Exempt from the hide-followed filter until the next feed load.
        setJustFollowed((prev) => new Set(prev).add(artist.toLowerCase()));
      }
    });
  }
  function unfollow(artist: string) {
    return api.trackByName(artist, "none").then((r: any) => { if (!r.error) setFollowing(artist, false); });
  }
  function setNotify(artist: string, on: boolean) {
    return api.trackByName(artist, on ? "notify" : "subscribed").then(() => {});
  }
  // Persist an ignore rule and drop matching rows immediately (the backend
  // filters them out of every future feed load).
  function ignore(artist: string, album?: string) {
    return api.addDiscoverIgnore(artist, album).then(() => {
      const a = artist.toLowerCase();
      const b = (album || "").toLowerCase();
      setItems((prev) =>
        prev.filter((it) => {
          const ia = (it.artist || "").toLowerCase();
          if (ia !== a) return true;
          return album ? (it.album || "").toLowerCase() !== b : false;
        }),
      );
      qc.invalidateQueries({ queryKey: ["discoverIgnores"] });
    });
  }

  const configuredAny = sources.some((s) => s.configured);
  const configuredSources = useMemo(() => sources.filter((s) => s.configured), [sources]);
  const sourceLabels = useMemo(() => configuredSources.map((s) => s.label), [configuredSources]);
  const selectedSources = useMemo(
    () => configuredSources.filter((s) => !hidden.has(s.key)).map((s) => s.label),
    [configuredSources, hidden],
  );
  const visible = useMemo(
    () =>
      items
        .filter((r) => itemSources(r).some((s) => !hidden.has(s.key as string)))
        .filter(
          (r) =>
            !hideFollowed ||
            !r.following ||
            justFollowed.has((r.artist || "").toLowerCase()),
        )
        .filter((r) => !hideOwned || !r.owned),
    [items, hidden, hideFollowed, hideOwned, justFollowed],
  );
  // Agenda list with past weeks optionally dropped. Undated ("TBA") items stay;
  // the cutoff is the start of the current week, matching the Agenda buckets.
  const agendaItems = useMemo(() => {
    if (showPast) return visible;
    const cutoff = new Date();
    cutoff.setHours(0, 0, 0, 0);
    cutoff.setDate(cutoff.getDate() - cutoff.getDay());
    return visible.filter(
      (r) => !r.normalized_date || new Date(r.normalized_date + "T00:00:00") >= cutoff,
    );
  }, [visible, showPast]);

  const noSources = loaded && !configuredAny;

  // Live status line: counts reflect the source checkboxes (client-side filter),
  // so toggling Last.fm / Metacritic updates the number immediately.
  const shownCount = sources.filter((s) => s.configured && !s.error && !hidden.has(s.key)).length;
  const busy = sources.filter((s) => s.refreshing);
  const errs = sources.filter((s) => s.error);
  const statusLine =
    shownCount === 0
      ? "" // empty state below carries the (linked) message instead
      : `${visible.length} releases from ${shownCount} ${shownCount === 1 ? "source" : "sources"}` +
        (busy.length ? ` · refreshing ${busy.map((s) => s.label).join(", ")}...` : "") +
        (errs.length ? ` · ${errs.map((s) => `${s.label}: ${s.error}`).join("; ")}` : "");

  const emptyState = (
    <p className="text-muted-foreground">
      No releases to show. Pick a source or add one in{" "}
      <RouterLink to="/settings?tab=discovery" className="hover:underline">Settings</RouterLink>.
    </p>
  );

  return (
    <div>
      <Tabs value={tab} onValueChange={changeTab}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold">Discover</h1>
          <TabsList>
            <TabsTrigger value="releases">New Releases</TabsTrigger>
            <TabsTrigger value="similar">Similar Artists</TabsTrigger>
            <TabsTrigger value="scrobbles">Your Last.fm</TabsTrigger>
          </TabsList>
        </div>
        {!nav.hide_page_descriptions && (
          <p className="text-muted-foreground">Find new music to track that may not be in your library yet.</p>
        )}

        <TabsContent value="releases" className="mt-1">
          <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-sm">
              <ViewToggle view={view} onChange={changeView} />
              <Separator orientation="vertical" className="h-6" />
              <div className="flex items-center gap-2">
                <Switch id="discover-hide-followed" checked={hideFollowed} onCheckedChange={changeHideFollowed} />
                <Label htmlFor="discover-hide-followed">Hide followed</Label>
              </div>
              <Separator orientation="vertical" className="h-6" />
              <div className="flex items-center gap-2">
                <Switch id="discover-hide-owned" checked={hideOwned} onCheckedChange={changeHideOwned} />
                <Label htmlFor="discover-hide-owned">Hide owned</Label>
              </div>
              {view === "agenda" && (
                <>
                  <Separator orientation="vertical" className="h-6" />
                  <div className="flex items-center gap-2">
                    <Switch id="discover-show-past" checked={showPast} onCheckedChange={changeShowPast} />
                    <Label htmlFor="discover-show-past">Past weeks</Label>
                  </div>
                </>
              )}
              <Separator orientation="vertical" className="h-6" />
              {configuredSources.length > 0 && (
                <Combobox
                  multiple
                  autoHighlight
                  items={sourceLabels}
                  value={selectedSources}
                  onValueChange={changeVisibleSources}
                >
                  <ComboboxChips ref={sourceAnchor} className="min-w-[180px]">
                    <ComboboxValue>
                      {(values: string[]) => (
                        <>
                          {values.map((v) => {
                            const s = configuredSources.find((x) => x.label === v);
                            return (
                              <ComboboxChip key={v}>
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <span>{v}</span>
                                  </TooltipTrigger>
                                  <TooltipContent side="bottom">
                                    {s ? (
                                      <>
                                        Last sync: {s.refreshing ? "syncing…" : fmtAgo(s.fetched_at)}
                                        {" · "}
                                        {s.count} {s.count === 1 ? "result" : "results"}
                                        {s.error ? ` · ${s.error}` : ""}
                                      </>
                                    ) : (
                                      v
                                    )}
                                  </TooltipContent>
                                </Tooltip>
                              </ComboboxChip>
                            );
                          })}
                          <ComboboxChipsInput placeholder={values.length ? "" : "Sources"} />
                        </>
                      )}
                    </ComboboxValue>
                  </ComboboxChips>
                  <ComboboxContent anchor={sourceAnchor}>
                    <ComboboxEmpty>No sources.</ComboboxEmpty>
                    <ComboboxList>
                      {(item: string) => (
                        <ComboboxItem key={item} value={item}>
                          {item}
                          {configuredSources.find((s) => s.label === item)?.error ? " (error)" : ""}
                        </ComboboxItem>
                      )}
                    </ComboboxList>
                  </ComboboxContent>
                </Combobox>
              )}
          </div>
          {statusLine && <p className="mb-3 text-muted-foreground">{statusLine}</p>}
          {loadingMsg ? (
            <p className="text-muted-foreground">{loadingMsg}</p>
          ) : noSources || shownCount === 0 ? (
            emptyState
          ) : view === "calendar" ? (
            <Calendar
              items={visible}
              itemDots={(r) =>
                itemSources(r)
                  .filter((s) => !hidden.has(s.key as string))
                  .map((s) => SRC_DOT[s.key as string] ?? "bg-foreground")
              }
              renderEvent={(r, k) => <CalEvent key={k} r={r} hidden={hidden} />}
            />
          ) : (
            <Agenda
              items={agendaItems}
              renderItem={(r, k) => <AgendaRow key={k} r={r} hidden={hidden} onFollow={follow} onUnfollow={unfollow} onNotify={setNotify} onIgnore={ignore} />}
              emptyMsg={emptyState}
            />
          )}
        </TabsContent>

        <TabsContent value="similar" className="mt-3">
          <SimilarRankings />
        </TabsContent>

        <TabsContent value="scrobbles" className="mt-3">
          <Scrobbles />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function AgendaRow({
  r,
  hidden,
  onFollow,
  onUnfollow,
  onNotify,
  onIgnore,
}: {
  r: DiscoverItem;
  hidden: Set<string>;
  onFollow: (a: string) => Promise<void>;
  onUnfollow: (a: string) => Promise<void>;
  onNotify: (a: string, on: boolean) => Promise<void>;
  onIgnore: (a: string, album?: string) => Promise<void>;
}) {
  const href = albumHref(r);
  const [busy, setBusy] = useState(false);
  // Seeded from the feed, which knows whether this artist is set to Notify --
  // a bell that starts "off" for an artist who already notifies is a lie.
  const [notifyOn, setNotifyOn] = useState(!!r.notify);
  const [bellBusy, setBellBusy] = useState(false);
  const [hideBusy, setHideBusy] = useState(false);
  // A feed reload can change it under us (following elsewhere, a refresh).
  useEffect(() => { setNotifyOn(!!r.notify); }, [r.notify]);
  function toggleFollow() {
    if (!r.artist) return;
    setBusy(true);
    if (r.following) setNotifyOn(false);
    (r.following ? onUnfollow(r.artist) : onFollow(r.artist)).finally(() => setBusy(false));
  }
  function toggleNotify() {
    if (!r.artist) return;
    const next = !notifyOn;
    setBellBusy(true);
    onNotify(r.artist, next).then(() => setNotifyOn(next)).finally(() => setBellBusy(false));
  }
  function runIgnore(album?: string) {
    if (!r.artist) return;
    setHideBusy(true);
    onIgnore(r.artist, album).finally(() => setHideBusy(false));
  }
  return (
    <div className="flex items-center gap-3 py-2.5">
      <AlbumArt src={r.image} boxSize="150px" rounded="md" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          {href ? (
            <RouterLink to={href} className="hover:underline">{r.album}</RouterLink>
          ) : r.album_url ? (
            <a href={r.album_url} target="_blank" rel="noopener" className="hover:underline">{r.album}</a>
          ) : (
            r.album
          )}
        </p>
        <div>
          {/* Always our own artist page, even for an artist we don't have yet:
              opening one creates them and scrapes their details. Their Last.fm
              page is linked from there. */}
          <ArtistLink name={r.artist} artistId={r.artist_id} />
        </div>
        {r.context && (
          <p className="text-sm text-muted-foreground">
            {linkArtistNames(r.context, r.context_artists)}
          </p>
        )}
        {r.genres && r.genres.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {r.genres.map((g) => (
              <Badge key={g} variant="outline" className="capitalize text-muted-foreground">{g}</Badge>
            ))}
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {itemSources(r).filter((s) => !hidden.has(s.key as string)).map((s) => (
            <Badge key={s.key} variant="secondary" className={srcBadge(s.key as string)}>{s.label}</Badge>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <Button
            size="xs"
            variant={r.following ? "outline" : "default"}
            disabled={busy}
            onClick={toggleFollow}
          >
            {r.following ? "Following" : "Follow"}
          </Button>
          {r.following && (
            <Tooltip>
              <TooltipTrigger asChild>
                {/* Labelled, not just an icon: on a row of four small buttons
                    the bell alone never said whether it was on. */}
                <Button
                  aria-label={notifyOn ? "Notifications on for this artist" : "Notify me about this artist"}
                  aria-pressed={notifyOn}
                  size="xs"
                  variant={notifyOn ? "default" : "outline"}
                  disabled={bellBusy}
                  onClick={toggleNotify}
                >
                  {notifyOn ? <LuBellRing /> : <LuBell />}
                  {notifyOn ? "Notifying" : "Notify"}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {notifyOn
                  ? `You'll be notified when ${r.artist} releases something. Click to stop.`
                  : `Get notified when ${r.artist} releases something.`}
              </TooltipContent>
            </Tooltip>
          )}
          {r.artist && (
            <>
              {r.album && (
                <IgnoreButton
                  icon={<LuEyeOff />}
                  tooltip={`Ignore this release (hide "${r.album}")`}
                  title="Ignore this release?"
                  description={`"${r.album}" by ${r.artist} will never appear on Discover again. You can undo this on the Ignored page.`}
                  disabled={hideBusy}
                  onConfirm={() => runIgnore(r.album!)}
                />
              )}
              <IgnoreButton
                icon={<LuUserX />}
                tooltip={`Ignore artist (hide everything by ${r.artist})`}
                title="Ignore this artist?"
                description={`Every release by ${r.artist} will be hidden from Discover. You can undo this on the Ignored page.`}
                disabled={hideBusy}
                onConfirm={() => runIgnore()}
              />
            </>
          )}
        </div>
      </div>
      {r.artist && r.album && (
        <div className="flex-none self-start">
          <ReleaseIcons artist={r.artist} album={r.album} mbid={r.mbid} />
        </div>
      )}
    </div>
  );
}

// Similar-artist ranking: artists suggested on many of your artists' pages but
// not in your library -- the stronger the overlap, the more worth checking out.
// Fills up passively as artist pages are browsed (suggestions are recorded
// server-side); hidden entirely until there's something to show.
// How many rows are added each time "Show more" is pressed (also the first page).
const SIMILAR_PAGE = 30;

function SimilarRankings() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["similarRankings"],
    queryFn: () => api.similarRankings(),
    staleTime: 5 * 60_000,
  });
  // Scan progress: poll while running, refresh the ranking when it finishes.
  const { data: scan } = useQuery({
    queryKey: ["similarScan"],
    queryFn: () => api.similarScanStatus(),
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });
  // Genre lookup progress: same deal, and the ranking is refreshed as it goes
  // so newly-tagged artists join the filter without a reload.
  const { data: enrich } = useQuery({
    queryKey: ["similarEnrich"],
    queryFn: () => api.similarEnrichStatus(),
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && scan && !scan.running) {
      qc.invalidateQueries({ queryKey: ["similarRankings"] });
    }
    wasRunning.current = !!scan?.running;
  }, [scan, qc]);
  const enrichWasRunning = useRef(false);
  const enrichDone = enrich?.done ?? 0;
  useEffect(() => {
    // While the lookup runs, pull fresh genres every ~25 artists; and once more
    // when it stops, to pick up the tail.
    if (enrich?.running && enrichDone && enrichDone % 25 === 0) {
      qc.invalidateQueries({ queryKey: ["similarRankings"] });
    }
    if (enrichWasRunning.current && enrich && !enrich.running) {
      qc.invalidateQueries({ queryKey: ["similarRankings"] });
    }
    enrichWasRunning.current = !!enrich?.running;
  }, [enrich, enrichDone, qc]);

  // Selected genres (empty = no genre filter) and whether a row has to carry
  // all of them or just one. Both persist like the other Discover preferences.
  const [genres, setGenres] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem("discoverSimilarGenres") || "[]"); } catch { return []; }
  });
  const [matchAll, setMatchAll] = useState<boolean>(() => {
    try { return localStorage.getItem("discoverSimilarGenreAll") === "true"; } catch { return false; }
  });
  const [limit, setLimit] = useState(SIMILAR_PAGE);
  const [enrichBusy, setEnrichBusy] = useState(false);
  const genreAnchor = useComboboxAnchor();

  const ranked = data?.artists ?? [];
  // Genre -> how many ranked artists carry it, most common first: the filter
  // options, and a hint at what the list is actually made of.
  const genreCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const s of ranked) {
      for (const g of s.genres) counts.set(g, (counts.get(g) ?? 0) + 1);
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [ranked]);
  const genreOptions = useMemo(() => genreCounts.map(([g]) => g), [genreCounts]);
  const countByGenre = useMemo(() => new Map(genreCounts), [genreCounts]);
  // Filter, keeping the server's order: most artist matches first.
  const filtered = useMemo(() => {
    if (!genres.length) return ranked;
    const want = genres.map((g) => g.toLowerCase());
    return ranked.filter((s) => {
      const have = new Set(s.genres.map((g) => g.toLowerCase()));
      return matchAll ? want.every((g) => have.has(g)) : want.some((g) => have.has(g));
    });
  }, [ranked, genres, matchAll]);

  function changeGenres(next: string[]) {
    setGenres(next);
    setLimit(SIMILAR_PAGE);
    try { localStorage.setItem("discoverSimilarGenres", JSON.stringify(next)); } catch {}
  }
  function changeMatchAll(v: boolean) {
    setMatchAll(v);
    setLimit(SIMILAR_PAGE);
    try { localStorage.setItem("discoverSimilarGenreAll", String(v)); } catch {}
  }
  function toggleEnrich() {
    setEnrichBusy(true);
    const call = enrich?.running ? api.similarEnrichStop() : api.similarEnrichStart();
    call
      .then((state) => qc.setQueryData(["similarEnrich"], state))
      .finally(() => setEnrichBusy(false));
  }

  if (!ranked.length && !scan?.running) {
    // Nothing recorded yet: point at the scan (now in Settings) to seed it.
    return (
      <p className="text-muted-foreground">
        No suggestions yet. They come from Last.fm and build up as you browse
        artist pages - or run the library
        scan in{" "}
        <RouterLink to="/settings?tab=discovery" className="hover:underline">Settings</RouterLink>{" "}
        to cover the whole library at once.
      </p>
    );
  }
  const shown = filtered.slice(0, limit);
  const known = data?.genres_known ?? 0;
  const total = data?.genres_total ?? ranked.length;
  return (
    <div>
      {scan?.running && (
        <p className="text-sm text-muted-foreground">
          {`Scanning ${scan.done}/${scan.total}${scan.current ? ` - ${scan.current}` : ""} · ${scan.recorded} suggestions`}
        </p>
      )}
      <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-sm">
        {genres.length > 1 && (
          <>
            <div className="flex items-center gap-2">
              <Switch id="similar-genre-all" checked={matchAll} onCheckedChange={changeMatchAll} />
              <Label htmlFor="similar-genre-all">Match all genres</Label>
            </div>
            <Separator orientation="vertical" className="h-6" />
          </>
        )}
        {genres.length > 0 && (
          <Button size="xs" variant="ghost" onClick={() => changeGenres([])}>Clear genres</Button>
        )}
        {genreOptions.length > 0 && (
          <Combobox
            multiple
            autoHighlight
            items={genreOptions}
            value={genres}
            onValueChange={changeGenres}
          >
            <ComboboxChips ref={genreAnchor} className="min-w-[180px]">
              <ComboboxValue>
                {(values: string[]) => (
                  <>
                    {values.map((v) => (
                      <ComboboxChip key={v} className="capitalize">{v.replace(/[._]/g, " ")}</ComboboxChip>
                    ))}
                    <ComboboxChipsInput placeholder={values.length ? "" : "Genres"} />
                  </>
                )}
              </ComboboxValue>
            </ComboboxChips>
            <ComboboxContent anchor={genreAnchor}>
              <ComboboxEmpty>No genres.</ComboboxEmpty>
              <ComboboxList>
                {(item: string) => (
                  <ComboboxItem key={item} value={item}>
                    <span className="capitalize">{item.replace(/[._]/g, " ")}</span>
                    <span className="ml-2 text-muted-foreground">{countByGenre.get(item)}</span>
                  </ComboboxItem>
                )}
              </ComboboxList>
            </ComboboxContent>
          </Combobox>
        )}
      </div>
      <p className="mb-2 text-sm text-muted-foreground">
        {genres.length
          ? `${filtered.length} of ${ranked.length} artists match ${matchAll ? "all" : "any"} of the chosen genres`
          : `${ranked.length} artists similar to the most artists you already have, but not in your library`}
        , most matches first. Builds up as you browse artist pages.
      </p>
      {/* Genres arrive one lookup per artist, so the filter only sees part of
          the list until the bulk lookup has run. */}
      {(known < total || enrich?.running) && (
        <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>
            {enrich?.running
              ? `Looking up genres: ${enrich.done}/${enrich.total}${enrich.current ? ` - ${enrich.current}` : ""}`
              : `Genres known for ${known} of ${total} suggestions.`}
          </span>
          <Button size="xs" variant="outline" disabled={enrichBusy} onClick={toggleEnrich}>
            {enrich?.running ? "Stop" : "Look up genres"}
          </Button>
          {!enrich?.running && enrich?.message && <span>{enrich.message}</span>}
        </p>
      )}
      <div className="divide-y">
        {shown.map((s) => (
          <SimilarRow key={s.name} s={s} />
        ))}
      </div>
      {!shown.length && (
        <p className="text-muted-foreground">
          No suggestions carry {matchAll ? "all" : "any"} of those genres yet.
        </p>
      )}
      {filtered.length > shown.length && (
        <div className="mt-3">
          <Button size="sm" variant="outline" onClick={() => setLimit((n) => n + SIMILAR_PAGE)}>
            Show more ({filtered.length - shown.length} left)
          </Button>
        </div>
      )}
    </div>
  );
}

// Row details (image, genres, bio) are fetched one at a time: each server-side
// cache miss asks the metadata sources in turn, and a page of rows firing at
// once would be a burst of lookups at Last.fm.
let infoChain: Promise<unknown> = Promise.resolve();
function queuedSimilarInfo(name: string) {
  const p = infoChain.then(() => api.similarArtistInfo(name));
  infoChain = p.catch(() => {});
  return p;
}

const PERIODS: [string, string][] = [
  ["overall", "All time"],
  ["12month", "Last year"],
  ["6month", "6 months"],
  ["3month", "3 months"],
  ["1month", "Last month"],
  ["7day", "Last week"],
];

// The artists the user actually plays, from their Last.fm scrobbles. The point
// of the tab is the gap: what they listen to constantly and don't own.
function Scrobbles() {
  const [period, setPeriod] = useState<string>(() => {
    try { return localStorage.getItem("discoverScrobblePeriod") || "overall"; } catch { return "overall"; }
  });
  const [unowned, setUnowned] = useState<boolean>(() => {
    try { return localStorage.getItem("discoverScrobbleUnowned") !== "false"; } catch { return true; }
  });
  const { data, isPending } = useQuery({
    queryKey: ["lastfmTop", period, unowned],
    queryFn: () => api.lastfmTopArtists(period, unowned),
    staleTime: 10 * 60_000,
  });

  function changePeriod(v: string) {
    setPeriod(v);
    try { localStorage.setItem("discoverScrobblePeriod", v); } catch {}
  }
  function changeUnowned(v: boolean) {
    setUnowned(v);
    try { localStorage.setItem("discoverScrobbleUnowned", String(v)); } catch {}
  }

  if (data && !data.configured) {
    return (
      <p className="text-muted-foreground">
        Add your Last.fm username in{" "}
        <RouterLink to="/settings" className="hover:underline">Settings</RouterLink>{" "}
        (next to the API key) to see the artists you play most, and which of them
        your library is missing.
      </p>
    );
  }
  const artists = data?.artists ?? [];
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-sm">
        <div className="flex items-center gap-2">
          <Switch id="scrobble-unowned" checked={unowned} onCheckedChange={changeUnowned} />
          <Label htmlFor="scrobble-unowned">Only ones I don't own</Label>
        </div>
        <Separator orientation="vertical" className="h-6" />
        <div className="flex flex-wrap gap-1">
          {PERIODS.map(([value, label]) => (
            <Button
              key={value}
              size="xs"
              variant={period === value ? "secondary" : "ghost"}
              onClick={() => changePeriod(value)}
            >
              {label}
            </Button>
          ))}
        </div>
      </div>
      <p className="mb-2 text-sm text-muted-foreground">
        {isPending
          ? "Reading your scrobbles..."
          : `${artists.length} artists${data?.user ? ` from ${data.user}` : ""}`}
        {unowned ? ", none of which are in your library" : ""}
      </p>
      <div className="divide-y">
        {artists.map((a) => (
          <ScrobbleRow key={a.name} a={a} />
        ))}
      </div>
      {!isPending && !artists.length && (
        <p className="text-muted-foreground">
          Nothing to show for this period{unowned ? " - you own everything you play here." : "."}
        </p>
      )}
    </div>
  );
}

function ScrobbleRow({ a }: { a: LastfmArtist }) {
  const [following, setFollowing] = useState(
    a.subscription === "subscribed" || a.subscription === "notify",
  );
  const [busy, setBusy] = useState(false);
  function toggleFollow() {
    setBusy(true);
    api
      .trackByName(a.name, following ? "none" : "subscribed")
      .then((r: any) => { if (!r.error) setFollowing((f) => !f); })
      .finally(() => setBusy(false));
  }
  return (
    <div className="flex items-center gap-3 py-2.5">
      <AlbumArt src={a.image_url} boxSize="64px" rounded="md" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          <ArtistLink name={a.name} artistId={a.artist_id} />
        </p>
        <p className="text-sm text-muted-foreground">
          {a.playcount.toLocaleString()} plays
          {a.owned ? " · in your library" : ""}
        </p>
      </div>
      <div className="flex-none">
        <Button size="xs" variant={following ? "outline" : "default"} disabled={busy} onClick={toggleFollow}>
          {following ? "Following" : "Follow"}
        </Button>
      </div>
    </div>
  );
}

// One ranked artist, styled like the New Releases agenda rows: art, linked
// name, why they're suggested, genres, and a follow button.
function SimilarRow({ s }: { s: SimilarRanking }) {
  const { data: info } = useQuery({
    queryKey: ["similarInfo", s.name.toLowerCase()],
    queryFn: () => queuedSimilarInfo(s.name),
    staleTime: Infinity,
  });
  const [following, setFollowing] = useState(s.subscription === "subscribed" || s.subscription === "notify");
  const [busy, setBusy] = useState(false);
  function toggleFollow() {
    setBusy(true);
    api
      .trackByName(s.name, following ? "none" : "subscribed")
      .then((r: any) => { if (!r.error) setFollowing((f) => !f); })
      .finally(() => setBusy(false));
  }
  const external = s.url || info?.lastfm_url;
  const rowGenres = info?.genres?.length ? info.genres : s.genres;
  const sources = s.sources.slice(0, 6).join(", ");
  const more = s.sources.length > 6 ? ` +${s.sources.length - 6} more` : "";
  return (
    <div className="flex items-center gap-3 py-2.5">
      <AlbumArt src={info?.image_url} boxSize="150px" rounded="md" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          <ArtistLink name={s.name} artistId={s.artist_id} />
        </p>
        <p className="text-sm text-muted-foreground">
          Similar to {s.count} of your {s.count === 1 ? "artist" : "artists"}: {sources}
          {more}
          {s.site &&
            (external ? (
              <>
                {" · via "}
                <a href={external} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  {s.site}
                </a>
              </>
            ) : (
              ` · via ${s.site}`
            ))}
        </p>
        {rowGenres.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {rowGenres.map((g) => (
              <Badge key={g} variant="outline" className="capitalize text-muted-foreground">{g}</Badge>
            ))}
          </div>
        )}
        {info?.bio && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{info.bio}</p>}
        <div className="mt-2 flex items-center gap-1.5">
          <Button size="xs" variant={following ? "outline" : "default"} disabled={busy} onClick={toggleFollow}>
            {following ? "Following" : "Follow"}
          </Button>
        </div>
      </div>
    </div>
  );
}

// Ignore action: icon button with a hover tooltip naming exactly what it hides,
// and a confirmation dialog so a stray click can't silently bury an artist.
function IgnoreButton({
  icon,
  tooltip,
  title,
  description,
  disabled,
  onConfirm,
}: {
  icon: React.ReactNode;
  tooltip: string;
  title: string;
  description: string;
  disabled?: boolean;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog>
      <Tooltip>
        <TooltipTrigger asChild>
          <AlertDialogTrigger asChild>
            <Button aria-label={tooltip} size="icon-xs" variant="ghost" disabled={disabled}>
              {icon}
            </Button>
          </AlertDialogTrigger>
        </TooltipTrigger>
        <TooltipContent>{tooltip}</TooltipContent>
      </Tooltip>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Ignore</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// Row in the selected-day list under the calendar: source dot(s) + "Artist -- Album".
function CalEvent({ r, hidden }: { r: DiscoverItem; hidden: Set<string> }) {
  const inApp = albumHref(r);
  const { open, opening } = useOpenArtist();
  const srcs = itemSources(r).filter((s) => !hidden.has(s.key as string));
  const label = (r.artist ? r.artist + " - " : "") + (r.album || "");
  const inner = (
    <span className="flex items-center gap-2">
      {srcs.map((s) => (
        <span key={s.key} className={"size-2 shrink-0 rounded-full " + (SRC_DOT[s.key as string] ?? "bg-foreground")} />
      ))}
      <span className="truncate">{label}</span>
    </span>
  );
  // No album page to open (the source gave no title): fall back to the
  // artist's page here rather than leaving the app, creating them if we
  // don't have them yet.
  return inApp ? (
    <RouterLink to={inApp} className="block text-sm hover:underline">{inner}</RouterLink>
  ) : (
    <button
      type="button"
      onClick={() => open(r.artist, r.artist_id)}
      disabled={opening === (r.artist || "").trim()}
      className="block w-full text-left text-sm hover:underline disabled:opacity-60"
    >
      {inner}
    </button>
  );
}
