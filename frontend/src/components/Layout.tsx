import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Link as RouterLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api";
import { ColorModeButton } from "./ui/color-mode";
import { GradientDefs } from "./GradientDefs";
import { PreviewPlayerProvider } from "./PreviewPlayer";
import { ScrollMemory } from "./ScrollMemory";
import { useNav } from "../nav";
import { prefetchRoute, warmRoutes } from "../prefetch";

export default function Layout() {
  const nav = useNav();
  const qc = useQueryClient();
  const { pathname } = useLocation();
  // Releases Discover has found since you last looked, for a count on its tab.
  const { data: unseen } = useQuery({
    queryKey: ["discoverUnseen"],
    queryFn: () => api.discoverUnseen(),
    refetchInterval: 15 * 60_000,
    staleTime: 5 * 60_000,
  });
  const newOnDiscover = pathname.startsWith("/discover") ? 0 : unseen?.count ?? 0;

  // Warm the pages behind the nav once this one has painted, so the first
  // switch doesn't wait on a request either.
  useEffect(() => {
    warmRoutes(qc, ["/artists", "/subscriptions", "/upcoming"]);
  }, [qc]);
  return (
    // The sample player lives above the outlet, so playback survives a page
    // change and every page can reach the same bar.
    <PreviewPlayerProvider>
    <ScrollMemory />
    <GradientDefs />
    <div className="flex min-h-[100dvh] flex-col">
      <header className="sticky top-0 z-10 border-b bg-background px-4 py-2.5">
        <div className="mx-auto flex max-w-[60rem] flex-wrap items-center gap-3">
          <RouterLink to={nav.home_path} className="font-bold text-foreground">
            Simple Music Tracker
          </RouterLink>
          <div className="flex-1" />
          <nav className="flex flex-wrap items-center gap-4">
            {nav.items
              .filter((item) => !item.hidden)
              .map((item) => (
                <NavLink
                  key={item.key}
                  to={item.path}
                  // Hovering is plenty of warning: by the time the click lands
                  // the data is usually already here.
                  onMouseEnter={() => prefetchRoute(qc, item.path)}
                  onFocus={() => prefetchRoute(qc, item.path)}
                  onTouchStart={() => prefetchRoute(qc, item.path)}
                  className={({ isActive }) =>
                    "text-foreground hover:underline" + (isActive ? " font-bold underline" : "")
                  }
                >
                  {item.label}
                  {item.key === "discover" && newOnDiscover > 0 && (
                    <span
                      className="ml-1 inline-flex min-w-5 items-center justify-center rounded-full bg-sky-500/20 px-1.5 text-xs font-semibold text-sky-700 no-underline dark:text-sky-300"
                      title={`${newOnDiscover} new since your last visit`}
                    >
                      {newOnDiscover > 99 ? "99+" : newOnDiscover}
                    </span>
                  )}
                </NavLink>
              ))}
            <ColorModeButton />
          </nav>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[60rem] flex-1 px-4 py-4">
        <Outlet />
      </main>

      <footer className="mx-auto mt-8 mb-12 w-full max-w-[60rem] border-t px-4 pt-4">
        <p className="text-sm text-muted-foreground">
          Simple Music Tracker
          <span className="mx-2">/</span>
          <a href="/api/upcoming?window=week" className="hover:underline">API</a>
        </p>
      </footer>
    </div>
    </PreviewPlayerProvider>
  );
}
