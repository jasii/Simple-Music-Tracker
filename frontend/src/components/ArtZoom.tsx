import { art } from "../api";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "./ui/dialog";

// Both art sources put a size in the URL, and a list asks for a small one. The
// modal wants the biggest each of them serves: 770x0 for Last.fm (its bare
// path is *smaller*, oddly, so it's not the one to ask for) and front-1200 for
// the Cover Art Archive, which is 157KB against the thumbnail's 10KB.
const LASTFM_SMALL = /\/i\/u\/(?:\d+x\d+|\d+s|avatar\d+s)\//i;
const LASTFM_BIG = "/i/u/770x0/";
const CAA_SMALL = /(front|back)-(?:250|500)$/i;

export function fullArt(url?: string | null): string | null {
  if (!url) return null;
  let full = url;
  if (LASTFM_SMALL.test(full) && !full.includes(LASTFM_BIG)) {
    full = full.replace(LASTFM_SMALL, LASTFM_BIG);
  }
  return full.replace(CAA_SMALL, "$1-1200");
}

// Wraps a cover (or an artist photo) so clicking it opens the full-size image.
// The thumbnail stays exactly as it was; only the click is new, and a missing
// image just renders the children as before.
export function ArtZoom({
  src,
  alt,
  children,
  className,
}: {
  src?: string | null;
  alt: string;
  children: React.ReactNode;
  className?: string;
}) {
  const full = fullArt(src);
  if (!full) return <>{children}</>;
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          title={`Show ${alt} full size`}
          className={className ? `${className} cursor-zoom-in` : "cursor-zoom-in"}
        >
          {children}
        </button>
      </DialogTrigger>
      <DialogContent aria-describedby={undefined}>
        <DialogTitle className="sr-only">{alt}</DialogTitle>
        {/* Served through the app's own art proxy, like every other image. */}
        <img
          src={art(full)}
          alt={alt}
          className="max-h-[82vh] max-w-[min(88vw,1200px)] rounded"
        />
      </DialogContent>
    </Dialog>
  );
}
