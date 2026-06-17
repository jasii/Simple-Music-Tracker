// Thin fetch wrappers around the Flask JSON API. Mirrors the old window.SMT
// helpers (app/static/app.js) but typed.
import type {
  AlbumDetailResponse,
  ArtistDetail,
  ArtistsResponse,
  DiscographyResponse,
  DiscoverResponse,
  NavConfig,
  RefreshState,
  ScanState,
  Settings,
  Stats,
  Subscription,
  UpcomingRelease,
  Artist,
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
  refreshArtist: (id: number) => postJSON(`/api/artists/${id}/refresh`),
  setAlbumOwned: (id: number, title: string, owned: boolean, mbid?: string | null) =>
    postJSON<{ title: string; owned: boolean }>(`/api/artists/${id}/albums/owned`, { title, owned, mbid }),
  scanArtist: (id: number) =>
    postJSON<{ artist_id: number; files: number; albums: number; folders: number }>(`/api/artists/${id}/scan`),
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

  upcoming: (window: string) =>
    getJSON<{ window: string; count: number; releases: UpcomingRelease[] }>(
      "/api/upcoming?window=" + encodeURIComponent(window),
    ),
  upcomingRange: (from: string, to: string) =>
    getJSON<{ from: string; to: string; count: number; releases: UpcomingRelease[] }>(
      `/api/upcoming/releases?from=${from}&to=${to}`,
    ),

  discoverReleases: (refresh?: string) =>
    getJSON<DiscoverResponse>(
      "/api/discover/releases" + (refresh ? "?refresh=" + refresh : ""),
    ),

  album: (artist: string, title: string, mbid?: string, refresh = false) => {
    const qs = new URLSearchParams({ artist, title });
    if (mbid) qs.set("mbid", mbid);
    if (refresh) qs.set("refresh", "1");
    return getJSON<AlbumDetailResponse>("/api/album?" + qs.toString());
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
  healthLastfmCookie: () =>
    postJSON<{ ok: boolean; message: string }>("/api/health/lastfm-cookie"),
  cacheStats: () => getJSON<Record<string, unknown>>("/api/cache/stats"),
  cachePurge: () => postJSON<Record<string, unknown>>("/api/cache/purge"),
};

// Route a remote image through the on-disk cache (disk-first, URL fallback).
export function art(url?: string | null): string {
  return url ? "/art?u=" + encodeURIComponent(url) : "";
}
