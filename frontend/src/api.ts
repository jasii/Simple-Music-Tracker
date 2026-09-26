// Thin fetch wrappers around the Flask JSON API. Mirrors the old window.SMT
// helpers (app/static/app.js) but typed.
import type {
  AlbumDetailResponse,
  ArtworkOptionsResponse,
  AlbumExtras,
  ArtworkOption,
  ArtworkReviewResponse,
  MetadataFillState,
  MetadataGapsResponse,
  MetadataOrderResponse,
  ArtworkStatus,
  ArtistDetail,
  ArtistsResponse,
  AlbumLinksResponse,
  ArtistExclusives,
  ArtistTopTracksResponse,
  DiscographyResponse,
  DiscoverResponse,
  NavConfig,
  DiscoverIgnore,
  DuplicatesResponse,
  AutograbRun,
  AutograbStatus,
  GrabResult,
  LastfmTopArtists,
  QualityProfile,
  ScanQueue,
  SystemInfo,
  LibraryGapsResponse,
  PluginInfo,
  SimilarArtistInfo,
  SimilarEnrichState,
  SimilarRankingsResponse,
  SimilarScanState,
  SimilarArtistsResponse,
  RefreshState,
  ScanState,
  Settings,
  Stats,
  Subscription,
  UpcomingRelease,
  Artist,
  DownloadJob,
} from "./types";

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error("request failed: " + res.status);
  return res.json() as Promise<T>;
}

async function postJSON<T = unknown>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json() as Promise<T>;
}

async function delJSON<T = unknown>(url: string): Promise<T> {
  const res = await fetch(url, { method: "DELETE", headers: { Accept: "application/json" } });
  return res.json() as Promise<T>;
}

export const api = {
  getJSON,
  postJSON,

  nav: () => getJSON<NavConfig>("/api/nav"),
  stats: () => getJSON<Stats>("/api/stats"),

  artists: (params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return getJSON<ArtistsResponse>("/api/artists" + (qs ? "?" + qs : ""));
  },
  artist: (id: number) => getJSON<ArtistDetail>(`/api/artists/${id}`),
  ignored: () => getJSON<{ artists: Artist[] }>("/api/ignored"),
  subscriptions: () => getJSON<{ artists: Artist[] }>("/api/subscriptions"),

  setSubscription: (id: number, state: Subscription) =>
    postJSON(`/api/artists/${id}/subscription`, { state }),
  setIgnore: (id: number, ignored: boolean) =>
    postJSON(`/api/artists/${id}/ignore`, { ignored }),
  setMonitorTypes: (id: number, types: string[]) =>
    postJSON(`/api/artists/${id}/monitor-types`, { types }),
  setMbid: (id: number, link: string) =>
    postJSON(`/api/artists/${id}/mbid`, { link }),
  merge: (id: number, sourceIds: number[], name?: string) =>
    postJSON(`/api/artists/${id}/merge`, { source_ids: sourceIds, name }),
  // Artists that look like the same person, with a suggested merge target.
  duplicates: (includeDismissed = false) =>
    getJSON<DuplicatesResponse>(
      "/api/artists/duplicates" + (includeDismissed ? "?dismissed=1" : ""),
    ),
  dismissDuplicate: (key: string, signature: string) =>
    postJSON<{ dismissed: string }>("/api/artists/duplicates/dismiss", { key, signature }),
  restoreDuplicates: (key?: string) =>
    postJSON<{ restored: string }>(
      "/api/artists/duplicates/dismiss",
      key ? { key, undo: true } : { all: true, undo: true },
    ),
  refreshArtist: (id: number) => postJSON(`/api/artists/${id}/refresh`),
  // The same work for a selection: queued metadata refresh, scoped rescan.
  refreshArtists: (ids: number[]) =>
    postJSON<{ queued: number }>("/api/artists/refresh", { ids }),
  scanArtists: (ids: number[]) =>
    postJSON<{ scanned: number; failed: number }>("/api/artists/scan", { ids }),

  // Release metadata beyond the tracklist: write-up, tags, label, credits.
  albumExtras: (artist: string, title: string, mbid?: string | null) => {
    const qs = new URLSearchParams({ artist, title });
    if (mbid) qs.set("mbid", mbid);
    return getJSON<AlbumExtras>("/api/album/extras?" + qs.toString());
  },

  // What the followed artists are missing, and filling it from the sources.
  metadataGaps: () => getJSON<MetadataGapsResponse>("/api/metadata/gaps"),
  metadataFill: (kinds?: string[]) =>
    postJSON<MetadataFillState & { started?: boolean; error?: string }>(
      "/api/metadata/fill", kinds ? { kinds } : {},
    ),
  metadataFillStatus: () => getJSON<MetadataFillState>("/api/metadata/fill/status"),
  metadataFillCancel: () => postJSON<MetadataFillState>("/api/metadata/fill/cancel"),

  // Which metadata source answers which field, and in what order.
  metadataOrder: () => getJSON<MetadataOrderResponse>("/api/metadata/order"),
  setMetadataOrder: (capability: string, keys: string[]) =>
    postJSON<{ capability: string; order: string[] }>(
      "/api/metadata/order", { capability, keys },
    ),

  // Artist photos for any spelling of a name, not just the stored one.
  artworkSearch: (q: string) =>
    getJSON<{ query: string; options: ArtworkOption[] }>(
      "/api/artwork/search?" + new URLSearchParams({ q }).toString(),
    ),

  // The "Get Hyped" playlist: where it would go, and building it.
  hypePlaylistState: () =>
    getJSON<{
      available: boolean;
      label: string | null;
      icon: string | null;
      name: string;
      tracks: { artist: string | null; title: string; album: string | null; duration: number | null }[];
      schedule: {
        enabled: boolean;
        day: number;
        day_name: string;
        time: string;
        days: number;
        per_artist: number;
        per_artist_choices: number[];
        windows: { days: number; label: string }[];
        last_run: number | null;
        next_run: string | null;
      };
    }>("/api/upcoming/playlist"),
  buildHypePlaylist: (days?: number, perArtist?: number) =>
    postJSON<{
      playlist: { id: string; name: string; count: number; label: string; icon?: string | null } | null;
      tracks: number;
      artists: number;
      considered: number;
      missing: string[];
      days: number;
      per_artist: number;
      message: string;
      error?: string;
    }>("/api/upcoming/playlist", {
      ...(days ? { days } : {}),
      ...(perArtist ? { per_artist: perArtist } : {}),
    }),

  // Artists whose artwork is missing or dead, for review by hand.
  artworkReview: (limit = 25, includeDismissed = false, ids?: number[]) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (includeDismissed) qs.set("dismissed", "1");
    // Given ids, the answer covers only those artists.
    if (ids?.length) qs.set("ids", ids.join(","));
    return getJSON<ArtworkReviewResponse>("/api/artwork/review?" + qs.toString());
  },
  artworkReviewDismiss: (artistId: number) =>
    postJSON<{ dismissed: boolean }>("/api/artwork/review/dismiss", { artist_id: artistId }),
  artworkReviewRestore: (artistId?: number) =>
    postJSON<{ restored: number }>("/api/artwork/review/dismiss",
      artistId ? { artist_id: artistId, restore: true } : { restore: true }),

  // Artwork the app can offer for one artist, and the pick the user makes.
  artistArtwork: (id: number, refresh = false) =>
    getJSON<ArtworkOptionsResponse>(
      `/api/artists/${id}/artwork` + (refresh ? "?refresh=1" : ""),
    ),
  // url null clears the choice and goes back to the automatic backfill.
  setArtistArtwork: (id: number, url: string | null) =>
    postJSON<{ id: number; image_url: string | null; locked: boolean }>(
      `/api/artists/${id}/artwork`, { url },
    ),
  setAlbumOwned: (id: number, title: string, owned: boolean, mbid?: string | null) =>
    postJSON<{ title: string; owned: boolean }>(`/api/artists/${id}/albums/owned`, { title, owned, mbid }),
  scanArtist: (id: number) =>
    postJSON<{
      artist_id: number;
      files: number;
      albums: number;
      folders: number;
      sources?: { key: string; label: string; albums: number }[];
    }>(`/api/artists/${id}/scan`),
  discography: (id: number, refresh = false) =>
    getJSON<DiscographyResponse>(
      `/api/artists/${id}/discography` + (refresh ? "?refresh=1" : ""),
    ),
  trackByName: (name: string, state: Subscription) =>
    postJSON<{ id: number | null; created: boolean; subscription: Subscription }>(
      "/api/artists/track-by-name",
      { name, state },
    ),
  addByLink: (link: string, state: Subscription) =>
    postJSON<{ error?: string; name?: string; created?: boolean }>(
      "/api/artists/add",
      { link, state },
    ),
  bulkSubscription: (ids: number[], state: Subscription) =>
    postJSON("/api/artists/subscriptions", { ids, state }),
  bulkIgnore: (ids: number[], ignored: boolean) =>
    postJSON("/api/artists/ignore", { ids, ignored }),

  // Version, paths and scheduler state, for the settings Help & Info pane.
  system: () => getJSON<SystemInfo>("/api/system"),
  // The quality profile (accepted qualities, best first) plus client order.
  quality: () => getJSON<QualityProfile>("/api/quality"),
  autograbStatus: () => getJSON<AutograbStatus>("/api/autograb/status"),
  autograbRun: () => postJSON<AutograbRun>("/api/autograb/run"),

  // Releases the library is missing / owns only in part. Both are computed
  // from cached data, so they cost no upstream calls.
  libraryGaps: (kind: "missing" | "incomplete", params: Record<string, string | number> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)]),
    ).toString();
    return getJSON<LibraryGapsResponse>(`/api/library/${kind}` + (qs ? "?" + qs : ""));
  },
  // Send one release to the first download client that finds it.
  grab: (artist: string, title: string) =>
    postJSON<GrabResult>("/api/library/grab", { artist, title }),
  // Fetch the tracks a partly-owned album lacks. Without `tracks` the server
  // asks the libraries which ones are missing.
  grabMissing: (body: {
    artist: string;
    title: string;
    mbid?: string;
    tracks?: string[];
    owned_format?: string;
  }) => postJSON<GrabResult>("/api/library/grab-missing", body),

  // Soulseek downloads followed file by file (app/downloads).
  downloads: (active = false) =>
    getJSON<{ jobs: DownloadJob[] }>("/api/downloads" + (active ? "?active=1" : "")),
  downloadAction: (id: number, action: "retry" | "another-peer" | "cancel") =>
    postJSON<DownloadJob & { error?: string }>(`/api/downloads/${id}/${action}`),
  downloadDelete: (id: number) => delJSON<{ ok: boolean }>(`/api/downloads/${id}`),
  downloadsClear: () => postJSON<{ removed: number }>("/api/downloads/clear"),

  upcoming: (window: string) =>
    getJSON<{ window: string; count: number; releases: UpcomingRelease[] }>(
      "/api/upcoming?window=" + encodeURIComponent(window),
    ),
  // Every tracked upcoming release (no window) -- shared by Upcoming and Following.
  upcomingReleases: () =>
    getJSON<{ releases: UpcomingRelease[] }>("/api/upcoming/releases"),
  upcomingRange: (from: string, to: string) =>
    getJSON<{ from: string; to: string; count: number; releases: UpcomingRelease[] }>(
      `/api/upcoming/releases?from=${from}&to=${to}`,
    ),

  discoverReleases: (refresh?: string) =>
    getJSON<DiscoverResponse>(
      "/api/discover/releases" + (refresh ? "?refresh=" + refresh : ""),
    ),
  // Discover ignore rules: hide an artist (no album) or one release from the feed.
  discoverIgnores: () => getJSON<{ ignores: DiscoverIgnore[] }>("/api/discover/ignores"),
  addDiscoverIgnore: (artist: string, album?: string) =>
    postJSON<{ ignores: DiscoverIgnore[] }>("/api/discover/ignores", { artist, album }),
  removeDiscoverIgnore: (id: number) =>
    delJSON<{ removed: boolean }>(`/api/discover/ignores/${id}`),

  plugins: (kind?: string) =>
    getJSON<{ plugins: PluginInfo[] }>(
      "/api/plugins" + (kind ? "?kind=" + encodeURIComponent(kind) : ""),
    ),
  pluginTest: (kind: string, key: string) =>
    postJSON<{ ok: boolean; message: string }>(`/api/plugins/${kind}/${key}/test`),
  pluginRefresh: (kind: string, key: string) =>
    postJSON<{ refreshing: boolean }>(`/api/plugins/${kind}/${key}/refresh`),
  libraryScan: (key: string, quick = false) =>
    postJSON<{ scanning?: boolean; error?: string; state?: string; position?: number | null }>(
      `/api/plugins/library/${key}/scan` + (quick ? "?quick=1" : ""),
    ),
  // Stop one source's scan, or drop it from the queue.
  libraryScanCancel: (key: string) =>
    postJSON<{ stopping: string | null; dequeued: string[] }>(
      `/api/plugins/library/${key}/cancel`,
    ),
  // The whole scan queue: one runs, the rest wait.
  scanQueue: () => getJSON<ScanQueue>("/api/scans"),
  scanQueueCancel: () => postJSON<{ stopping: string | null; dequeued: string[] }>("/api/scans/cancel"),
  scanCancel: () => postJSON<{ stopping: string | null; dequeued: string[] }>("/api/scan/cancel"),
  // Who sounds like one artist, from every similar-artist source.
  similarArtists: (artist: string) =>
    getJSON<SimilarArtistsResponse>(
      "/api/similar/artist?artist=" + encodeURIComponent(artist),
    ),
  // Create (or top up) the library row for an artist we only know by name and
  // return its id, so every artist name in the app can open a real artist page.
  // The server scrapes their image/bio/genres/MusicBrainz id before answering.
  artistFromSimilar: (name: string) =>
    postJSON<{
      id: number;
      name: string;
      created: boolean;
      subscription: Subscription;
      mbid: string | null;
      genres: string[];
    }>(
      "/api/artists/from-similar",
      { name },
    ),
  // An artist's best-known tracks plus a 30-second sample for each, if one
  // exists. Cached server-side, so this is cheap after the first open.
  // Which of a release's tracks can be played, and from where. The first call
  // starts the background pass; poll while running to fill rows in as they land.
  albumPlayable: (artist: string, title: string, mbid?: string) =>
    getJSON<{
      ready: boolean;
      running: boolean;
      progress?: { done?: number; total?: number };
      tracks: Record<string, {
        kind: string;
        label?: string;
        stream?: string;
        youtube_id?: string;
        // The source's own page for this song, for the credit line.
        source_url?: string | null;
        icon?: string | null;
      }>;
    }>(
      "/api/album/playable?artist=" + encodeURIComponent(artist) +
        "&title=" + encodeURIComponent(title) +
        (mbid ? "&mbid=" + encodeURIComponent(mbid) : ""),
    ),
  // Where one track can be played from: an in-app stream (library copy or
  // sample), else the YouTube id Last.fm's own player uses.
  trackSource: (artist: string, title: string, url?: string | null) =>
    getJSON<{
      kind: "library" | "sample" | "youtube" | "none";
      label?: string;
      stream?: string;
      youtube_id?: string;
      source_url?: string | null;
      icon?: string | null;
    }>(
      "/api/track-source?artist=" + encodeURIComponent(artist) +
        "&title=" + encodeURIComponent(title) +
        (url ? "&url=" + encodeURIComponent(url) : ""),
    ),
  // What each library album counts as, for the match dialog.
  albumLinks: (id: number) =>
    getJSON<AlbumLinksResponse>(`/api/artists/${id}/album-links`),
  // Connect a library album to a release group (linked true), say they are not
  // the same record (false), or forget the instruction (null).
  setAlbumLink: (id: number, albumKey: string, rgMbid: string, linked: boolean | null) =>
    postJSON<{ album_key: string; rg_mbid: string; linked: boolean | null; error?: string }>(
      `/api/artists/${id}/album-links`,
      { album_key: albumKey, rg_mbid: rgMbid, linked },
    ),
  // Unique-song counts for an artist's EPs and singles. The first call starts
  // the background pass and answers running=true; poll until it clears.
  artistExclusives: (id: number) =>
    getJSON<ArtistExclusives>(`/api/artists/${id}/exclusives`),
  artistTopTracks: (id: number, limit = 5) =>
    getJSON<ArtistTopTracksResponse>(`/api/artists/${id}/top-tracks?limit=${limit}`),
  similarRankings: () =>
    getJSON<SimilarRankingsResponse>("/api/similar/rankings"),
  // Image/genres/bio for one suggested artist (cached a week server-side).
  similarArtistInfo: (artist: string) =>
    getJSON<SimilarArtistInfo>(
      "/api/similar/artist-info?artist=" + encodeURIComponent(artist),
    ),
  similarScanStart: () => postJSON<SimilarScanState>("/api/similar/scan"),
  similarScanStop: () => postJSON<SimilarScanState>("/api/similar/scan", { stop: true }),
  similarScanStatus: () => getJSON<SimilarScanState>("/api/similar/scan/status"),
  // Bulk genre lookup for the ranking (best-ranked artists first). limit 0 = all.
  similarEnrichStart: (limit = 0) =>
    postJSON<SimilarEnrichState>("/api/similar/enrich", { limit }),
  similarEnrichStop: () =>
    postJSON<SimilarEnrichState>("/api/similar/enrich", { stop: true }),
  similarEnrichStatus: () =>
    getJSON<SimilarEnrichState>("/api/similar/enrich/status"),

  album: (artist: string, title: string, mbid?: string, refresh = false) => {
    const qs = new URLSearchParams({ artist, title });
    if (mbid) qs.set("mbid", mbid);
    if (refresh) qs.set("refresh", "1");
    return getJSON<AlbumDetailResponse>("/api/album?" + qs.toString());
  },
  // Resolve a working cover-art URL (Last.fm, CAA fallback) for a release whose
  // seeded discography art 404'd.
  albumArt: (artist: string, title: string, mbid?: string) => {
    const qs = new URLSearchParams({ artist, title });
    if (mbid) qs.set("mbid", mbid);
    return getJSON<{ image_url: string | null }>("/api/album-art?" + qs.toString());
  },

  scan: (quick: boolean) => postJSON("/api/scan", { quick }),
  scanStatus: () => getJSON<ScanState>("/api/scan/status"),
  refreshAll: () => postJSON("/api/refresh"),
  refreshStatus: () => getJSON<RefreshState>("/api/refresh/status"),

  settings: () => getJSON<Settings>("/api/settings"),
  saveSettings: (values: Record<string, string>) =>
    postJSON<{ updated: Record<string, string> }>("/api/settings", values),
  testWebhook: () => postJSON<{ ok: boolean; message: string }>("/api/webhook/test"),
  healthLastfmKey: () =>
    postJSON<{ ok: boolean; message: string }>("/api/health/lastfm-key"),
  healthLastfmUser: () =>
    postJSON<{ ok: boolean; message: string }>("/api/health/lastfm-user"),
  // The user's most-played Last.fm artists, flagged against the library.
  lastfmTopArtists: (period: string, unowned: boolean) =>
    getJSON<LastfmTopArtists>(
      `/api/lastfm/top-artists?period=${encodeURIComponent(period)}` +
        (unowned ? "&unowned=1" : ""),
    ),
  healthLastfmCookie: () =>
    postJSON<{ ok: boolean; message: string }>("/api/health/lastfm-cookie"),
  // Subscribe-in-your-calendar URL for upcoming releases (token-protected).
  calendarUrl: () => getJSON<{ url: string }>("/api/calendar"),
  calendarReset: () => postJSON<{ url: string }>("/api/calendar/reset"),
  cacheStats: () => getJSON<Record<string, unknown>>("/api/cache/stats"),
  cachePurge: () => postJSON<Record<string, unknown>>("/api/cache/purge"),
  // Artist artwork: how much is cached locally, and a way to fetch the rest.
  artworkStatus: () => getJSON<ArtworkStatus>("/api/artwork/status"),
  artworkWarm: (force = false) =>
    postJSON<ArtworkStatus & { started: boolean }>("/api/artwork/warm", { force }),
  artworkWarmStop: () => postJSON<ArtworkStatus>("/api/artwork/warm", { stop: true }),

  // Checkpoint the WAL and VACUUM: reclaims the free pages a purge leaves behind.
  dbCompact: () =>
    postJSON<{ freed_bytes: number; size_bytes: number; vacuumed: boolean }>("/api/db/compact"),
};

// Route a remote image through the on-disk cache (disk-first, URL fallback).
export function art(url?: string | null): string {
  return url ? "/art?u=" + encodeURIComponent(url) : "";
}
