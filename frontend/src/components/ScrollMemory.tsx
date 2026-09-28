// Scroll position per history entry, so going back to a long list (Discover,
// Artists, Missing) lands where you left it rather than at the top.
//
// The browser's own restoration can't do this for a single-page app: it
// restores during the history pop, before the page it's restoring has
// rendered (and before its data has arrived), so it lands on a short page and
// gets clamped to the top. Here each entry's position is remembered as you
// scroll, and put back once the page is tall enough to hold it -- retried
// for a few seconds while it loads, and abandoned the moment you scroll.
//
// A new page (a link, not back/forward) starts at the top.
import { useEffect, useLayoutEffect, useState } from "react";
import { useLocation, useNavigate, useNavigationType } from "react-router-dom";

type Entry = { pathname: string; search: string };

const POSITIONS_KEY = "smtScrollPositions";
const ORIGINS_KEY = "smtScrollOrigins";
// Entries remembered: plenty for a session, small enough for sessionStorage.
const MAX_ENTRIES = 200;
// How long a restore keeps trying while the page fills in.
const RESTORE_FOR_MS = 5000;

function read<T>(key: string): Record<string, T> {
  try {
    return JSON.parse(sessionStorage.getItem(key) || "{}");
  } catch {
    return {};
  }
}

function write(key: string, map: Record<string, unknown>) {
  const keys = Object.keys(map);
  // Oldest first (insertion order): drop what's beyond the cap.
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) delete map[k];
  try {
    sessionStorage.setItem(key, JSON.stringify(map));
  } catch {
    /* full or blocked: positions just aren't kept across a reload */
  }
}

// Kept in memory and mirrored to sessionStorage, so a reload keeps them too.
const positions = read<number>(POSITIONS_KEY);
// history entry key -> the page it was opened from (see useBackLink).
const origins = read<Entry>(ORIGINS_KEY);
let current: { key: string; entry: Entry } | null = null;

function restore(y: number) {
  let stopped = false;
  const stop = () => {
    stopped = true;
  };
  // Scrolling (or pressing a key) yourself wins over a restore still waiting.
  const events = ["wheel", "touchstart", "keydown", "mousedown"] as const;
  events.forEach((e) => window.addEventListener(e, stop, { passive: true, once: true }));
  const until = Date.now() + RESTORE_FOR_MS;
  let settled = 0;
  const tick = () => {
    if (stopped) return;
    const room = document.documentElement.scrollHeight - window.innerHeight;
    window.scrollTo(0, Math.min(y, Math.max(0, room)));
    // Reached, and held for a few frames (late images and rows can still
    // shift things): done.
    if (room >= y && Math.abs(window.scrollY - y) < 2) settled += 1;
    else settled = 0;
    if (settled < 10 && Date.now() < until) requestAnimationFrame(tick);
    else events.forEach((e) => window.removeEventListener(e, stop));
  };
  tick();
}

export function ScrollMemory() {
  const location = useLocation();
  const navType = useNavigationType();

  useLayoutEffect(() => {
    if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  }, []);

  // Remember where this entry came from, and where to put it on the way back.
  useLayoutEffect(() => {
    const entry = { pathname: location.pathname, search: location.search };
    if (current && current.key !== location.key) {
      if (navType === "PUSH") origins[location.key] = current.entry;
      // A replace (a settings tab in the URL) is the same visit.
      else if (navType === "REPLACE" && origins[current.key]) origins[location.key] = origins[current.key]!;
      write(ORIGINS_KEY, origins);
    }
    current = { key: location.key, entry };

    if (navType === "POP") {
      const y = positions[location.key];
      if (y) restore(y);
    } else if (navType === "PUSH") {
      window.scrollTo(0, 0);
    }
  }, [location.key, location.pathname, location.search, navType]);

  // Keep the position current as you scroll (not on leaving: by then the
  // next page has rendered and the window has already been clamped).
  useEffect(() => {
    const key = location.key;
    let frame = 0;
    let saveTimer: ReturnType<typeof setTimeout> | undefined;
    const onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        // Scroll events land a frame late: one caused by leaving (the next
        // page scrolled to the top, or clamped shorter) must not overwrite
        // where this page was left.
        if (current?.key !== key) return;
        positions[key] = window.scrollY;
        clearTimeout(saveTimer);
        saveTimer = setTimeout(() => write(POSITIONS_KEY, positions), 300);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
      clearTimeout(saveTimer);
      write(POSITIONS_KEY, positions);
    };
  }, [location.key]);

  return null;
}

const PAGE_LABELS: Record<string, string> = {
  "/discover": "Discover",
  "/upcoming": "Upcoming",
  "/artists": "All artists",
  "/missing": "Missing",
  "/subscriptions": "Following",
  "/ignored": "Ignored",
  "/settings": "Settings",
  "/album": "Album",
};

function pageLabel(pathname: string): string {
  if (pathname.startsWith("/artist/")) return "Artist";
  return PAGE_LABELS[pathname] ?? "Back";
}

/**
 * A page's "back" link. When the page it points at is the one you came from,
 * it goes back in history instead of opening that page afresh -- which is
 * what brings back your place in it (see ScrollMemory). With *followHistory*,
 * it goes back to wherever you came from, named after that page, and only
 * falls back to *to* when you arrived from outside the app.
 */
export function useBackLink(to: string, label: string, followHistory = false) {
  const location = useLocation();
  const navigate = useNavigate();
  const target = to.split("?")[0];
  // The page renders before ScrollMemory (its parent) has recorded where this
  // entry came from, so it's read again once that has happened.
  const [from, setFrom] = useState<Entry | undefined>(() => origins[location.key]);
  useEffect(() => setFrom(origins[location.key]), [location.key]);
  const onClick = (e: React.MouseEvent) => {
    // Let a modified click open a new tab as usual.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    // Read at the click, not the render: this is the one that must be right.
    const came = origins[location.key];
    if (came && (came.pathname === target || followHistory)) {
      e.preventDefault();
      navigate(-1);
    }
  };
  if (from && from.pathname !== target && followHistory) {
    return { to: from.pathname + from.search, label: pageLabel(from.pathname), onClick };
  }
  return { to, label, onClick };
}
