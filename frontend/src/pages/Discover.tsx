import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import {
  LuBell,
  LuBellRing,
  LuBookmark,
  LuBookmarkCheck,
  LuDownload,
  LuEyeOff,
  LuLoaderCircle,
  LuPause,
  LuPlay,
  LuTrash2,
  LuUserX,
} from "react-icons/lu";
import { toast } from "sonner";
import { api } from "../api";
import { AlbumArt } from "../components/AlbumArt";
import { ArtistLink, linkArtistNames, useOpenArtist } from "../components/ArtistLink";
import type {
  DiscoverItem,
  DiscoverSourceStatus,
  DiscoverSourceTag,
  LastfmArtist,
  SimilarRanking,
  WishlistItem,
} from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import { useAppEvent } from "../lib/events";
import { useRowPlayer, type PlayRow, type Starting } from "../lib/listPlay";
import {
  continueOn,
  heardKey,
  heardReleases,
  samplerSize,
  setContinueOn,
  setSamplerSize,
} from "../lib/playPrefs";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Separator } from "../components/ui/separator";
import { Switch } from "../components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { ReleaseIcons, formatDate, relativeDays } from "../lib/format";

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
// Calendar day-dot colors per source.
const SRC_DOT: Record<string, string> = {
  lastfm: "bg-red-500",
  metacritic: "bg-yellow-500",
  aoty: "bg-blue-500",
  iing: "bg-emerald-500",
};
// Every other source gets one of these, the same one each time.
const PALETTE: [string, string][] = [
  ["bg-violet-500/15 text-violet-700 dark:text-violet-400", "bg-violet-500"],
  ["bg-orange-500/15 text-orange-700 dark:text-orange-400", "bg-orange-500"],
  ["bg-cyan-500/15 text-cyan-700 dark:text-cyan-400", "bg-cyan-500"],
  ["bg-pink-500/15 text-pink-700 dark:text-pink-400", "bg-pink-500"],
  ["bg-lime-500/15 text-lime-700 dark:text-lime-400", "bg-lime-500"],
  ["bg-indigo-500/15 text-indigo-700 dark:text-indigo-400", "bg-indigo-500"],
  ["bg-teal-500/15 text-teal-700 dark:text-teal-400", "bg-teal-500"],
  ["bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-400", "bg-fuchsia-500"],
  ["bg-amber-500/15 text-amber-700 dark:text-amber-400", "bg-amber-500"],
  ["bg-sky-500/15 text-sky-700 dark:text-sky-400", "bg-sky-500"],
  ["bg-rose-500/15 text-rose-700 dark:text-rose-400", "bg-rose-500"],
  ["bg-green-500/15 text-green-700 dark:text-green-400", "bg-green-500"],
];
function paletteFor(key: string): [string, string] {
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length]!;
}
const srcBadge = (key?: string) =>
  SRC_BADGE[key as string] ?? (key ? paletteFor(key)[0] : "bg-muted text-muted-foreground");
const srcDot = (key?: string) =>
  SRC_DOT[key as string] ?? (key ? paletteFor(key)[1] : "bg-foreground");

function itemSources(r: DiscoverItem): DiscoverSourceTag[] {
  return r.sources && r.sources.length ? r.sources : [{ key: r.source, label: r.source_label }];
}

function albumHref(r: { artist?: string | null; album?: string | null; mbid?: string | null; image?: string | null; normalized_date?: string | null; release_date?: string | null }): string | null {
  if (!r.artist || !r.album) return null;
  const qs = new URLSearchParams({ artist: r.artist, title: r.album, from: "discover" });
  if (r.mbid) qs.set("mbid", r.mbid);
  const d = r.normalized_date || r.release_date;
  if (d) qs.set("date", d);
  if (r.image) qs.set("img", r.image);
  return "/album?" + qs.toString();
}

type DiscoverCache = { sources: DiscoverSourceStatus[]; items: DiscoverItem[] };

// Which row a playing track came from, so that row can be marked.
function playKey(r: { artist?: string | null; album?: string | null }): string {
  return `discover:${(r.artist || "").toLowerCase()}|${(r.album || "").toLowerCase()}`;
}

function toPlayRow(r: DiscoverItem): PlayRow {
  return {
    key: playKey(r),
    artist: r.artist || "",
    album: r.album,
    mbid: r.mbid,
    artistId: r.artist_id,
    image: r.image,
    date: r.normalized_date,
  };
}

// Continue mode walks past what's already been listened to.
const skipHeard = (row: PlayRow) => heardReleases.has(heardKey(row.artist, row.album));

// Last.fm tags come as "hip.hop" and "rock_and_roll"; shown as words.
function genreLabel(g: string): string {
  return g.replace(/[._]/g, " ").trim();
}
function genreKey(g: string): string {
  return g.toLowerCase().replace(/[^a-z0-9]+/g, "");
}
// The artist's genres and the release's own, as match keys.
function itemGenreKeys(r: DiscoverItem): Set<string> {
  return new Set([...(r.artist_genres ?? []), ...(r.genres ?? [])].map(genreKey).filter(Boolean));
}

function scoreClass(score: number): string {
  if (score >= 75) return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400";
  if (score >= 50) return "bg-amber-500/15 text-amber-700 dark:text-amber-400";
  return "bg-red-500/15 text-red-700 dark:text-red-400";
}

type SortMode = "date" | "foryou" | "score";

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}
function storedList(key: string): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
function remember(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* not remembered */ }
}

/** Has this element come near the screen yet? Stays true once it has. */
function useSeen<T extends Element>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen || typeof IntersectionObserver === "undefined") return;
    const watcher = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          watcher.disconnect();
        }
      },
      { rootMargin: "300px" },
    );
    watcher.observe(el);
    return () => watcher.disconnect();
  }, [seen]);
  return [ref, seen] as const;
}

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
  const [hidden, setHidden] = useState<Set<string>>(() => new Set(storedList("discoverHidden")));
  const [view, setView] = useState<"agenda" | "calendar">(
    () => stored("discoverView", "agenda") as "agenda" | "calendar",
  );
  const [tab, setTab] = useState<string>(() => stored("discoverTab", "releases"));
  // Agenda only: whether weeks before the current one are shown.
  const [showPast, setShowPast] = useState<boolean>(() => stored("discoverShowPast", "true") !== "false");
  // Hide artists already followed. Artists followed *during this session* stay
  // visible (so the "Following" confirmation isn't yanked away mid-click);
  // they drop out on the next feed load.
  const [hideFollowed, setHideFollowed] = useState<boolean>(() => stored("discoverHideFollowed", "false") === "true");
  // Hide releases the library already owns (album-level, not just the artist).
  const [hideOwned, setHideOwned] = useState<boolean>(() => stored("discoverHideOwned", "false") === "true");
  // Hide releases already played. Like follows, ones heard this visit stay.
  const [hideHeard, setHideHeard] = useState<boolean>(() => stored("discoverHideHeard", "false") === "true");
  const [sort, setSort] = useState<SortMode>(() => stored("discoverSort", "date") as SortMode);
  // Genre filter: show rows carrying any of `genres`, hide any carrying
  // `hideGenres`. Labels as shown; matched loosely (see genreKey).
  const [genres, setGenres] = useState<string[]>(() => storedList("discoverGenres"));
  const [hideGenres, setHideGenres] = useState<string[]>(() => storedList("discoverHideGenres"));
  const [justFollowed, setJustFollowed] = useState<Set<string>>(new Set());
  const [justHeard, setJustHeard] = useState<Set<string>>(new Set());
  // "New since your last visit" is measured from this (null: first visit).
  const [lastVisit, setLastVisit] = useState<number | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout>>();
  const sourceAnchor = useComboboxAnchor();
  const genreAnchor = useComboboxAnchor();
  const hideGenreAnchor = useComboboxAnchor();
  const rowPlayer = useRowPlayer();
  // When a release's tracks run out, carry on with the next one down the list.
  const [continueNext, setContinueNext] = useState<boolean>(continueOn);
  const [sampler, setSampler] = useState<number>(samplerSize);

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
          for (const it of its) {
            if (it.heard && it.artist) heardReleases.add(heardKey(it.artist, it.album));
          }
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
    // This look counts as a visit: what's new is measured from the one before.
    api
      .discoverVisit()
      .then((r) => {
        setLastVisit(r.last_visit);
        qc.setQueryData(["discoverUnseen"], { count: 0 });
      })
      .catch(() => {});
    return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // What the player (or another page) did to a release, reflected here.
  const sameRelease = (it: DiscoverItem, artist: string, album?: string | null) =>
    (it.artist || "").toLowerCase() === artist.toLowerCase() &&
    (album == null || (it.album || "").toLowerCase() === album.toLowerCase());
  useAppEvent("heard", (e) => {
    const key = heardKey(e.artist, e.album);
    if (e.heard) heardReleases.add(key); else heardReleases.delete(key);
    if (e.heard) setJustHeard((prev) => new Set(prev).add(key));
    setItems((prev) => prev.map((it) => (sameRelease(it, e.artist, e.album ?? "") ? { ...it, heard: e.heard } : it)));
  });
  useAppEvent("saved", (e) => {
    setItems((prev) => prev.map((it) => (sameRelease(it, e.artist, e.album ?? "") ? { ...it, saved: e.saved } : it)));
    qc.invalidateQueries({ queryKey: ["wishlist"] });
  });
  useAppEvent("followed", (e) => {
    setFollowing(e.artist, e.following);
    if (e.following) setJustFollowed((prev) => new Set(prev).add(e.artist.toLowerCase()));
  });
  useAppEvent("ignored", (e) => {
    setItems((prev) => prev.filter((it) => !sameRelease(it, e.artist, e.album ?? null)));
    qc.invalidateQueries({ queryKey: ["discoverIgnores"] });
  });
  useAppEvent("unignored", () => {
    load(false, true);
    qc.invalidateQueries({ queryKey: ["discoverIgnores"] });
  });

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
      remember("discoverHidden", JSON.stringify(Array.from(next)));
      return next;
    });
  }
  function changeView(v: "agenda" | "calendar") {
    setView(v);
    remember("discoverView", v);
  }
  function changeTab(v: string) {
    setTab(v);
    remember("discoverTab", v);
  }
  function changeShowPast(v: boolean) {
    setShowPast(v);
    remember("discoverShowPast", String(v));
  }
  function changeHideFollowed(v: boolean) {
    setHideFollowed(v);
    remember("discoverHideFollowed", String(v));
  }
  function changeHideOwned(v: boolean) {
    setHideOwned(v);
    remember("discoverHideOwned", String(v));
  }
  function changeHideHeard(v: boolean) {
    setHideHeard(v);
    remember("discoverHideHeard", String(v));
  }
  function changeSort(v: SortMode) {
    setSort(v);
    remember("discoverSort", v);
  }
  function changeGenres(v: string[]) {
    setGenres(v);
    remember("discoverGenres", JSON.stringify(v));
  }
  function changeHideGenres(v: string[]) {
    setHideGenres(v);
    remember("discoverHideGenres", JSON.stringify(v));
  }
  function addGenre(g: string) {
    const label = genreLabel(g);
    if (!genres.some((x) => genreKey(x) === genreKey(label))) changeGenres([...genres, label]);
  }
  function changeContinue(v: boolean) {
    setContinueNext(v);
    setContinueOn(v);
  }
  function changeSampler(v: string) {
    const n = Number(v) || 0;
    setSampler(n);
    setSamplerSize(n);
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
  // Save for later, or take it off the list again.
  function toggleSaved(r: DiscoverItem) {
    if (!r.artist || !r.album) return Promise.resolve();
    const artist = r.artist;
    const album = r.album;
    const call = r.saved
      ? api.wishlistRemove(artist, album)
      : api.wishlistAdd({ artist, album, mbid: r.mbid, release_date: r.normalized_date, image: r.image });
    return call
      .then(() => {
        setItems((prev) => prev.map((it) => (sameRelease(it, artist, album) ? { ...it, saved: !r.saved } : it)));
        qc.invalidateQueries({ queryKey: ["wishlist"] });
        if (!r.saved) toast.success(`Saved "${album}" for later`);
      })
      .catch(() => toast.error("Could not update the saved list."));
  }
  function unheard(r: DiscoverItem) {
    if (!r.artist) return;
    const artist = r.artist;
    api.markHeard(artist, r.album, false).then(() => {
      heardReleases.delete(heardKey(artist, r.album));
      setItems((prev) => prev.map((it) => (sameRelease(it, artist, r.album ?? "") ? { ...it, heard: false } : it)));
    });
  }

  const configuredAny = sources.some((s) => s.configured);
  const configuredSources = useMemo(() => sources.filter((s) => s.configured), [sources]);
  const sourceLabels = useMemo(() => configuredSources.map((s) => s.label), [configuredSources]);
  const selectedSources = useMemo(
    () => configuredSources.filter((s) => !hidden.has(s.key)).map((s) => s.label),
    [configuredSources, hidden],
  );
  // Everything but the genre filter: what the genre options are counted over.
  const unfiltered = useMemo(
    () =>
      items
        .filter((r) => itemSources(r).some((s) => !hidden.has(s.key as string)))
        .filter(
          (r) =>
            !hideFollowed ||
            !r.following ||
            justFollowed.has((r.artist || "").toLowerCase()),
        )
        .filter((r) => !hideOwned || !r.owned)
        .filter((r) => !hideHeard || !r.heard || justHeard.has(heardKey(r.artist || "", r.album))),
    [items, hidden, hideFollowed, hideOwned, hideHeard, justFollowed, justHeard],
  );
  // Genre -> how many rows carry it, most common first.
  const genreOptions = useMemo(() => {
    const counts = new Map<string, { label: string; n: number }>();
    for (const r of unfiltered) {
      const seen = new Set<string>();
      for (const g of [...(r.artist_genres ?? []), ...(r.genres ?? [])]) {
        const k = genreKey(g);
        if (!k || seen.has(k)) continue;
        seen.add(k);
        const entry = counts.get(k) ?? { label: genreLabel(g).toLowerCase(), n: 0 };
        entry.n += 1;
        counts.set(k, entry);
      }
    }
    return Array.from(counts.values()).sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
  }, [unfiltered]);
  const genreLabels = useMemo(() => genreOptions.map((g) => g.label), [genreOptions]);
  const genreCount = useMemo(() => new Map(genreOptions.map((g) => [g.label, g.n])), [genreOptions]);
  const untagged = useMemo(() => unfiltered.filter((r) => itemGenreKeys(r).size === 0).length, [unfiltered]);
  const visible = useMemo(() => {
    const want = genres.map(genreKey);
    const avoid = hideGenres.map(genreKey);
    if (!want.length && !avoid.length) return unfiltered;
    return unfiltered.filter((r) => {
      const have = itemGenreKeys(r);
      if (avoid.some((g) => have.has(g))) return false;
      return !want.length || want.some((g) => have.has(g));
    });
  }, [unfiltered, genres, hideGenres]);
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
  // The list in the order it's shown: by date (the agenda's order), or ranked.
  const listed = useMemo(() => {
    if (sort === "date") return agendaItems;
    const byDate = (a: DiscoverItem, b: DiscoverItem) =>
      (a.normalized_date || "9999").localeCompare(b.normalized_date || "9999");
    return [...agendaItems].sort((a, b) =>
      sort === "score"
        ? (b.score ?? -1) - (a.score ?? -1) || (b.for_you ?? 0) - (a.for_you ?? 0) || byDate(a, b)
        : (b.for_you ?? 0) - (a.for_you ?? 0) || byDate(a, b),
    );
  }, [agendaItems, sort]);
  const playRows = useMemo(() => listed.filter((r) => r.artist).map(toPlayRow), [listed]);

  // Genre lookup for the rows that have none, when a genre filter is in use.
  const filtering = genres.length > 0 || hideGenres.length > 0;
  const { data: enrich } = useQuery({
    queryKey: ["similarEnrich"],
    queryFn: () => api.similarEnrichStatus(),
    enabled: filtering,
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
  const enrichWasRunning = useRef(false);
  useEffect(() => {
    // A finished lookup: reload so the new tags reach the rows.
    if (enrichWasRunning.current && enrich && !enrich.running) load(false, true);
    enrichWasRunning.current = !!enrich?.running;
  }, [enrich, load]);
  function lookUpGenres() {
    api
      .discoverGenres()
      .then((state) => {
        qc.setQueryData(["similarEnrich"], state);
        if (!state.started && !state.running) toast.info("Every artist here already has genre tags.");
      })
      .catch(() => toast.error("Could not start the genre lookup."));
  }

  const noSources = loaded && !configuredAny;

  // Live status line: counts reflect the source checkboxes (client-side filter),
  // so toggling Last.fm / Metacritic updates the number immediately.
  const shownCount = sources.filter((s) => s.configured && !s.error && !hidden.has(s.key)).length;
  const busy = sources.filter((s) => s.refreshing);
  const errs = sources.filter((s) => s.error);
  const newCount = lastVisit ? visible.filter((r) => (r.first_seen ?? 0) > lastVisit).length : 0;
  const statusLine =
    shownCount === 0
      ? "" // empty state below carries the (linked) message instead
      : `${visible.length} releases from ${shownCount} ${shownCount === 1 ? "source" : "sources"}` +
        (newCount ? ` · ${newCount} new since your last visit` : "") +
        (busy.length ? ` · refreshing ${busy.map((s) => s.label).join(", ")}...` : "") +
        (errs.length ? ` · ${errs.map((s) => `${s.label}: ${s.error}`).join("; ")}` : "");

  const emptyState = (
    <p className="text-muted-foreground">
      No releases to show. Pick a source or add one in{" "}
      <RouterLink to="/settings?tab=discovery" className="hover:underline">Settings</RouterLink>.
    </p>
  );

  const { data: saved } = useQuery({
    queryKey: ["wishlist"],
    queryFn: () => api.wishlist(),
    staleTime: 60_000,
  });

  const renderRow = (r: DiscoverItem, k: number, ranked = false) => {
    const key = playKey(r);
    return (
      <AgendaRow
        key={k}
        r={r}
        hidden={hidden}
        onFollow={follow}
        onUnfollow={unfollow}
        onNotify={setNotify}
        onIgnore={ignore}
        onSave={toggleSaved}
        onUnheard={unheard}
        onGenre={addGenre}
        onPlay={(row) => rowPlayer.play(toPlayRow(row), playRows, skipHeard)}
        starting={rowPlayer.starting?.key === key ? rowPlayer.starting : null}
        active={rowPlayer.activeKey === key}
        playing={rowPlayer.activeKey === key && rowPlayer.playing}
        isNew={!!lastVisit && (r.first_seen ?? 0) > lastVisit}
        showDate={ranked}
        showReasons={sort === "foryou"}
      />
    );
  };

  return (
    <div>
      <Tabs value={tab} onValueChange={changeTab}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-bold">Discover</h1>
          <TabsList>
            <TabsTrigger value="releases">New Releases</TabsTrigger>
            <TabsTrigger value="saved">
              Saved{saved?.items.length ? ` (${saved.items.length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="similar">Similar Artists</TabsTrigger>
            <TabsTrigger value="scrobbles">Your Last.fm</TabsTrigger>
          </TabsList>
        </div>
        {!nav.hide_page_descriptions && (
          <p className="text-muted-foreground">Find new music to track that may not be in your library yet.</p>
        )}

        <TabsContent value="releases" className="mt-1">
          <div className="mb-2 flex flex-wrap items-center justify-end gap-2 text-sm">
            <ViewToggle view={view} onChange={changeView} />
            {view === "agenda" && (
              <Select value={sort} onValueChange={(v) => changeSort(v as SortMode)}>
                <SelectTrigger size="sm" className="w-auto" aria-label="Sort">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="date">By date</SelectItem>
                  <SelectItem value="foryou">For you</SelectItem>
                  <SelectItem value="score">Critic score</SelectItem>
                </SelectContent>
              </Select>
            )}
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
            <Separator orientation="vertical" className="h-6" />
            <div className="flex items-center gap-2">
              <Switch id="discover-hide-heard" checked={hideHeard} onCheckedChange={changeHideHeard} />
              <Label htmlFor="discover-hide-heard">Hide heard</Label>
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
          <div className="mb-3 flex flex-wrap items-center justify-end gap-2 text-sm">
            <GenrePicker
              anchor={genreAnchor}
              options={genreLabels}
              counts={genreCount}
              value={genres}
              onChange={changeGenres}
              placeholder="Genres"
            />
            <GenrePicker
              anchor={hideGenreAnchor}
              options={genreLabels}
              counts={genreCount}
              value={hideGenres}
              onChange={changeHideGenres}
              placeholder="Hide genres"
            />
            {filtering && (
              <Button size="xs" variant="ghost" onClick={() => { changeGenres([]); changeHideGenres([]); }}>
                Clear genres
              </Button>
            )}
            {view === "agenda" && (
              <>
                <Separator orientation="vertical" className="h-6" />
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center gap-2">
                      <Switch id="discover-continue" checked={continueNext} onCheckedChange={changeContinue} />
                      <Label htmlFor="discover-continue">Continue to next artist</Label>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    When a release's tracks finish playing, go on to the next one down the list,
                    past anything you've already heard.
                  </TooltipContent>
                </Tooltip>
                <Select value={String(sampler)} onValueChange={changeSampler}>
                  <SelectTrigger size="sm" className="w-auto" aria-label="Tracks per release">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="0">Every track</SelectItem>
                    <SelectItem value="1">1 track per release</SelectItem>
                    <SelectItem value="2">2 tracks per release</SelectItem>
                    <SelectItem value="3">3 tracks per release</SelectItem>
                    <SelectItem value="5">5 tracks per release</SelectItem>
                  </SelectContent>
                </Select>
              </>
            )}
          </div>
          {statusLine && <p className="mb-3 text-muted-foreground">{statusLine}</p>}
          {filtering && untagged > 0 && (
            <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>
                {enrich?.running
                  ? `Looking up genres: ${enrich.done}/${enrich.total}${enrich.current ? ` - ${enrich.current}` : ""}`
                  : `${untagged} release${untagged === 1 ? " has" : "s have"} no genre tags yet${genres.length ? " and are hidden by the genre filter" : ""}.`}
              </span>
              {!enrich?.running && (
                <Button size="xs" variant="outline" onClick={lookUpGenres}>Look up genres</Button>
              )}
            </p>
          )}
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
                  .map((s) => srcDot(s.key as string))
              }
              renderEvent={(r, k) => <CalEvent key={k} r={r} hidden={hidden} />}
            />
          ) : sort === "date" ? (
            <Agenda items={agendaItems} renderItem={(r, k) => renderRow(r, k)} emptyMsg={emptyState} />
          ) : listed.length ? (
            <div className="flex flex-col">{listed.map((r, k) => renderRow(r, k, true))}</div>
          ) : (
            emptyState
          )}
        </TabsContent>

        <TabsContent value="saved" className="mt-3">
          <SavedReleases items={saved?.items} autograb={!!saved?.autograb} />
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

// A multi-select of genre names, most common first, with how many rows carry each.
function GenrePicker({
  anchor,
  options,
  counts,
  value,
  onChange,
  placeholder,
}: {
  anchor: ReturnType<typeof useComboboxAnchor>;
  options: string[];
  counts: Map<string, number>;
  value: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
}) {
  // Chosen genres stay pickable even when no row carries them right now.
  const all = useMemo(() => Array.from(new Set([...value, ...options])), [value, options]);
  return (
    <Combobox multiple autoHighlight items={all} value={value} onValueChange={onChange}>
      <ComboboxChips ref={anchor} className="min-w-[160px]">
        <ComboboxValue>
          {(values: string[]) => (
            <>
              {values.map((v) => (
                <ComboboxChip key={v} className="capitalize">{v}</ComboboxChip>
              ))}
              <ComboboxChipsInput placeholder={values.length ? "" : placeholder} />
            </>
          )}
        </ComboboxValue>
      </ComboboxChips>
      <ComboboxContent anchor={anchor}>
        <ComboboxEmpty>No genres.</ComboboxEmpty>
        <ComboboxList>
          {(item: string) => (
            <ComboboxItem key={item} value={item}>
              <span className="capitalize">{item}</span>
              <span className="ml-2 text-muted-foreground">{counts.get(item) ?? 0}</span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

function AgendaRow({
  r,
  hidden,
  onFollow,
  onUnfollow,
  onNotify,
  onIgnore,
  onSave,
  onUnheard,
  onGenre,
  onPlay,
  starting,
  active,
  playing,
  isNew,
  showDate,
  showReasons,
}: {
  r: DiscoverItem;
  hidden: Set<string>;
  onFollow: (a: string) => Promise<void>;
  onUnfollow: (a: string) => Promise<void>;
  onNotify: (a: string, on: boolean) => Promise<void>;
  onIgnore: (a: string, album?: string) => Promise<void>;
  onSave: (r: DiscoverItem) => Promise<unknown>;
  onUnheard: (r: DiscoverItem) => void;
  onGenre: (g: string) => void;
  onPlay: (r: DiscoverItem) => void;
  starting: Starting | null;
  active: boolean;
  playing: boolean;
  isNew: boolean;
  showDate: boolean;
  showReasons: boolean;
}) {
  const href = albumHref(r);
  const [rowRef, seen] = useSeen<HTMLDivElement>();
  // Walking down the list moves the mark out of view: follow it. Only when
  // it arrives, not when the page opens on a row that was already playing.
  const wasActive = useRef(active);
  useEffect(() => {
    if (active && !wasActive.current) {
      rowRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    wasActive.current = active;
  }, [active, rowRef]);
  // The artist's own tags. The feed carries them when they're known; else
  // they're looked up once the row nears the screen (one lookup at a time,
  // shared with Similar Artists, cached for a week).
  const known = r.artist_genres ?? [];
  const { data: info } = useQuery({
    queryKey: ["similarInfo", (r.artist || "").toLowerCase()],
    queryFn: () => queuedSimilarInfo(r.artist!),
    enabled: seen && !!r.artist && !known.length,
    staleTime: Infinity,
  });
  // Shown once each: "Singer-Songwriter" and "singer/songwriter" are one tag.
  const seenTags = new Set<string>();
  const fresh = (g: string) => {
    const key = genreKey(g);
    if (!key || seenTags.has(key)) return false;
    seenTags.add(key);
    return true;
  };
  const artistGenres = (known.length ? known : info?.genres ?? []).map(genreLabel).filter(fresh).slice(0, 6);
  const releaseGenres = (r.genres ?? []).map(genreLabel).filter(fresh);
  const [busy, setBusy] = useState(false);
  // Seeded from the feed, which knows whether this artist is set to Notify --
  // a bell that starts "off" for an artist who already notifies is a lie.
  const [notifyOn, setNotifyOn] = useState(!!r.notify);
  const [bellBusy, setBellBusy] = useState(false);
  const [hideBusy, setHideBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
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
  function toggleSave() {
    setSaveBusy(true);
    onSave(r).finally(() => setSaveBusy(false));
  }
  const playLabel = active && playing ? "Pause" : active ? "Resume" : "Play this release";
  const reasons = showReasons ? (r.reasons ?? []).slice(0, 3) : [];
  return (
    <div
      ref={rowRef}
      className={
        "-mx-2 flex scroll-mt-20 scroll-mb-36 items-center gap-3 rounded-lg px-2 py-2.5 transition-colors" +
        (active ? " bg-primary/10 ring-1 ring-primary/40" : "")
      }
    >
      <div className={"relative flex-none" + (r.heard && !active ? " opacity-60" : "")}>
        <AlbumArt src={r.image} boxSize="150px" rounded="md" />
        {r.artist && (
          <Button
            size="icon-sm"
            variant={active ? "default" : "secondary"}
            aria-label={playLabel}
            title={playLabel}
            disabled={!!starting}
            onClick={() => onPlay(r)}
            className="absolute right-1.5 bottom-1.5 rounded-full shadow-md"
          >
            {starting ? (
              <LuLoaderCircle className="animate-spin" />
            ) : active && playing ? (
              <LuPause />
            ) : (
              <LuPlay />
            )}
          </Button>
        )}
      </div>
      <div className={"min-w-0 flex-1" + (r.heard && !active ? " opacity-75" : "")}>
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
          {href ? (
            <RouterLink to={href} className="hover:underline">{r.album}</RouterLink>
          ) : r.album_url ? (
            <a href={r.album_url} target="_blank" rel="noopener" className="hover:underline">{r.album}</a>
          ) : (
            r.album
          )}
          {isNew && (
            <Badge className="bg-sky-500/15 font-medium text-sky-700 dark:text-sky-400">New</Badge>
          )}
          {r.heard && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Badge
                  asChild
                  variant="outline"
                  className="cursor-pointer font-medium text-muted-foreground"
                >
                  <button type="button" onClick={() => onUnheard(r)}>Heard</button>
                </Badge>
              </TooltipTrigger>
              <TooltipContent>You've played this. Click to mark it unheard.</TooltipContent>
            </Tooltip>
          )}
        </p>
        <div>
          {/* Always our own artist page, even for an artist we don't have yet:
              opening one creates them and scrapes their details. Their Last.fm
              page is linked from there. */}
          <ArtistLink name={r.artist} artistId={r.artist_id} />
        </div>
        {showDate && (
          <p className="text-sm text-muted-foreground">
            {formatDate(r.normalized_date)}
            {r.normalized_date
              ? ` · ${relativeDays(Math.round((new Date(r.normalized_date + "T00:00:00").getTime() - new Date().setHours(0, 0, 0, 0)) / 86400000))}`
              : ""}
          </p>
        )}
        {reasons.length > 0 && (
          <p className="text-sm text-primary/80">{reasons.join(" · ")}</p>
        )}
        {r.context && (
          <p className="text-sm text-muted-foreground">
            {linkArtistNames(r.context, r.context_artists)}
          </p>
        )}
        {(artistGenres.length > 0 || releaseGenres.length > 0) && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {/* The artist's genres filled, the release's own outlined. Click
                one to filter the list to it. */}
            {artistGenres.map((g) => (
              <Badge key={"a:" + g} asChild variant="secondary" className="cursor-pointer capitalize">
                <button type="button" title={`${r.artist}: ${g}. Click to show only ${g}.`} onClick={() => onGenre(g)}>
                  {g}
                </button>
              </Badge>
            ))}
            {releaseGenres.map((g) => (
              <Badge key={"r:" + g} asChild variant="outline" className="cursor-pointer capitalize text-muted-foreground">
                <button type="button" title={`This release: ${g}. Click to show only ${g}.`} onClick={() => onGenre(g)}>
                  {g}
                </button>
              </Badge>
            ))}
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {r.score != null && (
            <Badge asChild variant="secondary" className={scoreClass(r.score)}>
              <a href={r.score_url || undefined} target="_blank" rel="noopener noreferrer" title="Metacritic critic score">
                Metascore {r.score}
              </a>
            </Badge>
          )}
          {itemSources(r).filter((s) => !hidden.has(s.key as string)).map((s) => (
            <Badge key={s.key} variant="secondary" className={srcBadge(s.key as string)}>{s.label}</Badge>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
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
          {r.artist && r.album && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label={r.saved ? "Saved for later" : "Save for later"}
                  aria-pressed={!!r.saved}
                  size="icon-xs"
                  variant={r.saved ? "default" : "ghost"}
                  disabled={saveBusy}
                  onClick={toggleSave}
                >
                  {r.saved ? <LuBookmarkCheck /> : <LuBookmark />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {r.saved
                  ? "Saved for later (see the Saved tab). Click to take it off."
                  : "Save for later: you'll be told on its release day."}
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
          {starting && (
            <span className="text-xs text-muted-foreground">
              finding audio{starting.total ? ` ${starting.done}/${starting.total}` : ""}...
            </span>
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

// The saved-for-later list: soonest first, each announced on its release day
// (and grabbed then, when that's switched on).
function SavedReleases({ items, autograb }: { items?: WishlistItem[]; autograb: boolean }) {
  const qc = useQueryClient();
  const rowPlayer = useRowPlayer();
  const { data: downloaderData } = useQuery({
    queryKey: ["plugins", "downloader"],
    queryFn: () => api.plugins("downloader"),
    staleTime: 5 * 60_000,
  });
  const downloader = (downloaderData?.plugins ?? []).find((p) => p.configured);
  const [busy, setBusy] = useState<number | null>(null);
  if (!items) return <p className="text-muted-foreground">Loading...</p>;
  if (!items.length) {
    return (
      <p className="text-muted-foreground">
        Nothing saved yet. Press the bookmark on a release (or thumbs up in the player) to keep
        it here without following the artist; you'll be told on its release day.
      </p>
    );
  }
  const rows: PlayRow[] = items.map((it) => ({
    key: playKey(it),
    artist: it.artist,
    album: it.album,
    mbid: it.mbid,
    image: it.image,
    date: it.release_date,
  }));
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  function remove(it: WishlistItem) {
    setBusy(it.id);
    api
      .wishlistRemove(it.artist, it.album)
      .then(() => {
        qc.invalidateQueries({ queryKey: ["wishlist"] });
        qc.invalidateQueries({ queryKey: ["discover"] });
      })
      .finally(() => setBusy(null));
  }
  function grab(it: WishlistItem) {
    setBusy(it.id);
    api
      .grab(it.artist, it.album)
      .then((r) => {
        if (r.error) toast.error(r.error);
        else toast.success(`${r.client}: ${r.message}`);
      })
      .catch(() => toast.error("Could not reach the server."))
      .finally(() => setBusy(null));
  }
  return (
    <div>
      <p className="mb-3 text-sm text-muted-foreground">
        {items.length} saved. Each is announced on its release day to the notifiers with
        "Saved release out today" ticked
        {autograb ? ", and sent to the download client" : ""} (Settings).
      </p>
      <div className="flex flex-col">
        {items.map((it, i) => {
          const key = rows[i]!.key;
          const active = rowPlayer.activeKey === key;
          const days = it.release_date
            ? Math.round((new Date(it.release_date + "T00:00:00").getTime() - today.getTime()) / 86400000)
            : null;
          const href = albumHref({ artist: it.artist, album: it.album, mbid: it.mbid, image: it.image, normalized_date: it.release_date });
          const starting = rowPlayer.starting?.key === key;
          return (
            <div
              key={it.id}
              className={
                "-mx-2 flex items-center gap-3 rounded-lg px-2 py-2" +
                (active ? " bg-primary/10 ring-1 ring-primary/40" : "")
              }
            >
              <div className="relative flex-none">
                <AlbumArt src={it.image} boxSize="72px" rounded="md" resolve={{ artist: it.artist, title: it.album, mbid: it.mbid }} />
                <Button
                  size="icon-xs"
                  variant={active ? "default" : "secondary"}
                  aria-label={active && rowPlayer.playing ? "Pause" : "Play"}
                  disabled={starting}
                  onClick={() => rowPlayer.play(rows[i]!, rows)}
                  className="absolute right-1 bottom-1 rounded-full shadow-md"
                >
                  {starting ? <LuLoaderCircle className="animate-spin" /> : active && rowPlayer.playing ? <LuPause /> : <LuPlay />}
                </Button>
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate font-semibold">
                  {href ? <RouterLink to={href} className="hover:underline">{it.album}</RouterLink> : it.album}
                </p>
                <ArtistLink name={it.artist} />
                <p className="text-sm text-muted-foreground">
                  {formatDate(it.release_date)}
                  {days != null ? ` · ${days === 0 ? "out today" : days < 0 ? "out now" : relativeDays(days)}` : ""}
                  {it.grabbed_at ? " · sent to the download client" : ""}
                </p>
              </div>
              <div className="flex flex-none items-center gap-1">
                {downloader && (days == null || days <= 0) && (
                  <Button size="xs" variant="outline" disabled={busy === it.id} onClick={() => grab(it)}
                          title={`Search ${downloader.label} for this release and download it`}>
                    <LuDownload /> Grab
                  </Button>
                )}
                <Button size="icon-xs" variant="ghost" aria-label={`Remove ${it.album}`}
                        disabled={busy === it.id} onClick={() => remove(it)}>
                  <LuTrash2 />
                </Button>
              </div>
            </div>
          );
        })}
      </div>
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
  const rowPlayer = useRowPlayer();

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
  // Play an artist's top tracks; with continue on, then the next one down.
  const playRows: PlayRow[] = shown.map((s) => ({
    key: "similar:" + s.name.toLowerCase(),
    artist: s.name,
    artistId: s.artist_id,
    topTracksOnly: true,
  }));
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
        {shown.map((s, i) => {
          const row = playRows[i]!;
          return (
            <SimilarRow
              key={s.name}
              s={s}
              onPlay={() => rowPlayer.play(row, playRows)}
              starting={rowPlayer.starting?.key === row.key}
              active={rowPlayer.activeKey === row.key}
              playing={rowPlayer.activeKey === row.key && rowPlayer.playing}
            />
          );
        })}
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
function SimilarRow({
  s,
  onPlay,
  starting,
  active,
  playing,
}: {
  s: SimilarRanking;
  onPlay: () => void;
  starting: boolean;
  active: boolean;
  playing: boolean;
}) {
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
  const playLabel = active && playing ? "Pause" : active ? "Resume" : `Play ${s.name}'s top tracks`;
  return (
    <div
      className={
        "-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 transition-colors" +
        (active ? " bg-primary/10 ring-1 ring-primary/40" : "")
      }
    >
      <div className="relative flex-none">
        <AlbumArt src={info?.image_url} boxSize="150px" rounded="md" />
        <Button
          size="icon-sm"
          variant={active ? "default" : "secondary"}
          aria-label={playLabel}
          title={playLabel}
          disabled={starting}
          onClick={onPlay}
          className="absolute right-1.5 bottom-1.5 rounded-full shadow-md"
        >
          {starting ? <LuLoaderCircle className="animate-spin" /> : active && playing ? <LuPause /> : <LuPlay />}
        </Button>
      </div>
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
        <span key={s.key} className={"size-2 shrink-0 rounded-full " + srcDot(s.key as string)} />
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
