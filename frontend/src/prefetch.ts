// Warming the pages before they're opened.
//
// Every page fetches on mount, so switching tabs meant watching a request go
// out and a "Loading..." sit there -- even for data that had been on screen a
// minute earlier. Two things fix that: a cache that outlives a detour to
// another page (see the QueryClient defaults), and asking for the data before
// the click, which is what this does. Hovering a nav link is enough warning;
// the rest is warmed once the first page has painted.
import type { QueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { DiscoverItem, DiscoverSourceStatus, UpcomingRelease } from "./types";

type Warmer = (qc: QueryClient) => Promise<unknown>;

// Path -> what that page reads. Keys must match the pages exactly, or the
// prefetch fills a slot nothing looks in.
const WARMERS: Record<string, Warmer[]> = {
  "/artists": [
    (qc) =>
      qc.prefetchQuery({
        queryKey: ["artists", { filter: "" }],
        queryFn: () => api.artists({}).then((d) => d.artists),
      }),
    (qc) => qc.prefetchQuery({ queryKey: ["stats"], queryFn: () => api.stats() }),
  ],
  "/subscriptions": [
    (qc) =>
      qc.prefetchQuery({
        queryKey: ["subscriptions"],
        queryFn: () => api.subscriptions().then((d) => d.artists),
      }),
    (qc) =>
      qc.prefetchQuery({
        queryKey: ["upcomingReleases"],
        queryFn: () => api.upcomingReleases().then((d: { releases: UpcomingRelease[] }) => d.releases || []),
      }),
  ],
  "/upcoming": [
    (qc) =>
      qc.prefetchQuery({
        queryKey: ["upcomingReleases"],
        queryFn: () => api.upcomingReleases().then((d: { releases: UpcomingRelease[] }) => d.releases || []),
      }),
  ],
  "/discover": [
    // Discover keeps its own shape in the cache (it polls while sources
    // refresh), so this fills that slot rather than a query of its own.
    async (qc) => {
      if (qc.getQueryData(["discover"])) return;
      const data = await api.getJSON<{ sources: DiscoverSourceStatus[]; items: DiscoverItem[] }>(
        "/api/discover/releases",
      );
      qc.setQueryData(["discover"], { sources: data.sources || [], items: data.items || [] });
    },
  ],
  "/ignored": [
    (qc) =>
      qc.prefetchQuery({
        queryKey: ["ignored"],
        queryFn: () => api.ignored().then((d) => d.artists),
      }),
  ],
};

/** Fetch what *path* will need, if it isn't already cached. */
export function prefetchRoute(qc: QueryClient, path: string) {
  for (const warm of WARMERS[path] ?? []) {
    void warm(qc).catch(() => {});
  }
}

/** Warm the heavy pages once the app is idle, so the first switch is instant too. */
export function warmRoutes(qc: QueryClient, paths: string[]) {
  const run = () => paths.forEach((path) => prefetchRoute(qc, path));
  const idle = (window as unknown as {
    requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
  }).requestIdleCallback;
  if (idle) idle(run, { timeout: 3000 });
  else setTimeout(run, 1200);
}
