// Release/album artwork with a vinyl placeholder. Shows the cover when present,
// and falls back to the `.vinyl-art` placeholder both when there's no image_url
// AND when the cover fails to load (many MusicBrainz release-groups have no art,
// so the constructed coverartarchive URL 404s -- without this fallback the row
// shows a broken image instead of the placeholder).
//
// When `resolve` is given, a failed cover is first retried against the art
// resolver (Last.fm, the same source the album page uses) before giving up to
// the placeholder, so discography rows recover real art instead of a vinyl.
import { useEffect, useState } from "react";
import { api, art } from "../api";

const ROUNDED: Record<string, string> = { sm: "rounded-sm", md: "rounded-md", lg: "rounded-lg", full: "rounded-full" };

export interface ResolveArt {
  artist: string;
  title: string;
  mbid?: string | null;
}

export function AlbumArt({
  src,
  boxSize,
  rounded = "sm",
  resolve,
}: {
  src?: string | null;
  boxSize: string;
  rounded?: string;
  resolve?: ResolveArt;
}) {
  // `current` is the URL actually being shown; it can swap to a resolved
  // fallback after the original `src` fails to load.
  const [current, setCurrent] = useState<string | null | undefined>(src);
  const [failed, setFailed] = useState(false);
  const [triedResolve, setTriedResolve] = useState(false);
  // Hidden until onLoad: a failed load would otherwise paint the browser's
  // broken-image outline over the placeholder while onError (and the resolver
  // retry) are still in flight.
  const [loaded, setLoaded] = useState(false);

  // Reset when the row is reused for a different release (TanStack recycles rows).
  useEffect(() => {
    setCurrent(src);
    setFailed(false);
    setTriedResolve(false);
    setLoaded(false);
  }, [src]);

  async function onError() {
    // First failure with a resolver available: ask the backend for a working
    // URL (Last.fm) and retry once before falling back to the placeholder.
    if (resolve && !triedResolve) {
      setTriedResolve(true);
      try {
        const r = await api.albumArt(resolve.artist, resolve.title, resolve.mbid ?? undefined);
        if (r.image_url && r.image_url !== src) {
          setCurrent(r.image_url);
          return;
        }
      } catch {
        // fall through to placeholder
      }
    }
    setFailed(true);
  }

  const show = !!current && !failed;
  return (
    <div
      // The vinyl placeholder is always the background, so it shows immediately;
      // the cover loads lazily on top and covers it once painted (or it stays
      // visible if the cover is missing/fails). Muted fill + vinyl image live in
      // the `.vinyl-art` class (not a `background` shorthand that would clear it).
      className={`vinyl-art flex-none overflow-hidden ${ROUNDED[rounded] ?? "rounded-sm"}`}
      style={{ width: boxSize, height: boxSize }}
    >
      {show && (
        <img
          src={art(current)}
          alt=""
          className={`h-full w-full object-cover ${loaded ? "" : "opacity-0"}`}
          loading="lazy"
          decoding="async"
          onLoad={() => setLoaded(true)}
          onError={onError}
        />
      )}
    </div>
  );
}
