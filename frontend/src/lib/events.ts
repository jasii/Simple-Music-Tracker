// A small app-wide event bus for things done to a release from somewhere other
// than the page showing it: the player marks a release heard, saves it, follows
// its artist or ignores it, and Discover's rows update to match without a
// reload. Plain DOM events, so nothing needs to be wired through context.
import { useEffect, useRef } from "react";

export type ReleaseEvent = { artist: string; album?: string | null };

type Events = {
  heard: ReleaseEvent & { heard: boolean };
  saved: ReleaseEvent & { saved: boolean };
  followed: ReleaseEvent & { following: boolean };
  ignored: ReleaseEvent;
  unignored: ReleaseEvent;
};

const PREFIX = "smt:";

export function emit<K extends keyof Events>(name: K, detail: Events[K]) {
  window.dispatchEvent(new CustomEvent(PREFIX + name, { detail }));
}

/** Run *handler* whenever *name* is emitted, for as long as the caller is mounted. */
export function useAppEvent<K extends keyof Events>(name: K, handler: (detail: Events[K]) => void) {
  // The latest handler, so callers can pass an inline function.
  const latest = useRef(handler);
  latest.current = handler;
  useEffect(() => {
    const listener = (e: Event) => latest.current((e as CustomEvent<Events[K]>).detail);
    window.addEventListener(PREFIX + name, listener);
    return () => window.removeEventListener(PREFIX + name, listener);
  }, [name]);
}
