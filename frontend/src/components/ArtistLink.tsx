import { useCallback, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { LuLoaderCircle } from "react-icons/lu";
import { toast } from "sonner";
import { api } from "../api";
import { cn } from "../lib/utils";
import type { DiscoverItem } from "../types";

type DiscoverCache = { sources: unknown[]; items: DiscoverItem[] };

// Opening an artist by name. Every artist name in the app leads to a page here,
// never off to Last.fm: an artist we don't have yet is created on the way, the
// server scraping their image, bio, genres and MusicBrainz id first so the page
// has content the moment it opens (the rest lands on the refresh queue, which
// the new artist goes to the front of). Created artists are parked on the
// Ignored page, so looking one up doesn't pad the library.
export function useOpenArtist() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [opening, setOpening] = useState<string | null>(null);
  const open = useCallback(
    (name: string | null | undefined, artistId?: number | null) => {
      if (artistId) {
        navigate(`/artist/${artistId}`);
        return;
      }
      const clean = (name || "").trim();
      if (!clean) return;
      setOpening(clean);
      api
        .artistFromSimilar(clean)
        .then((r) => {
          if (!r.id) {
            toast.error(`Could not open ${clean}.`);
            return;
          }
          // The name now has an id: stamp it onto the cached Discover feed and
          // the similar rankings so going back shows a plain link, not another
          // round trip through here.
          qc.setQueryData<DiscoverCache>(["discover"], (old) =>
            old
              ? {
                  ...old,
                  items: old.items.map((it) =>
                    (it.artist || "").toLowerCase() === clean.toLowerCase()
                      ? { ...it, artist_id: r.id, in_library: true }
                      : it,
                  ),
                }
              : old,
          );
          qc.invalidateQueries({ queryKey: ["similarRankings"] });
          navigate(`/artist/${r.id}`);
        })
        .catch(() => toast.error(`Could not open ${clean}.`))
        .finally(() => setOpening(null));
    },
    [navigate, qc],
  );
  return { open, opening };
}

// An artist name as a link to their page here. Known artists are a router
// link; an unknown name is a button that creates them first (see useOpenArtist)
// and looks the same either way.
export function ArtistLink({
  name,
  artistId,
  className,
  children,
}: {
  name: string | null | undefined;
  artistId?: number | null;
  className?: string;
  children?: React.ReactNode;
}) {
  const { open, opening } = useOpenArtist();
  const label = children ?? name;
  if (!name) return <span className={className}>{label}</span>;
  if (artistId) {
    return (
      <RouterLink to={`/artist/${artistId}`} className={cn("hover:underline", className)}>
        {label}
      </RouterLink>
    );
  }
  const busy = opening === name.trim();
  return (
    <button
      type="button"
      onClick={() => open(name)}
      disabled={busy}
      title={`Open ${name}`}
      className={cn(
        "inline-flex items-center gap-1.5 text-left hover:underline disabled:opacity-60",
        className,
      )}
    >
      {label}
      {/* Creating the artist takes a moment -- their details are scraped on
          the way -- so the name spins rather than sprouting an ellipsis. */}
      {busy && <LuLoaderCircle aria-label="Opening" className="animate-spin" />}
    </button>
  );
}

// Flatten a source's flavor text ("Similar to Quantic, Beanfield and
// Jazzanova") into nodes with each named artist linked to their page here.
// Names are matched longest-first so one name inside another still links whole.
export function linkArtistNames(text: string, names: string[] | null | undefined) {
  if (!text || !names || !names.length) return text;
  const wanted = [...new Set(names.filter(Boolean))].sort((a, b) => b.length - a.length);
  const nodes: React.ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    let at = -1;
    let hit = "";
    for (const n of wanted) {
      const i = rest.indexOf(n);
      if (i >= 0 && (at < 0 || i < at)) {
        at = i;
        hit = n;
      }
    }
    if (at < 0) {
      nodes.push(rest);
      break;
    }
    if (at > 0) nodes.push(rest.slice(0, at));
    nodes.push(<ArtistLink key={key++} name={hit} className="underline decoration-dotted" />);
    rest = rest.slice(at + hit.length);
  }
  return nodes;
}
