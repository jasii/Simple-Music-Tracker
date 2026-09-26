import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ImageOff, Pencil, RefreshCw, Wand2 } from "lucide-react";

import { api, art } from "../api";
import { ArtZoom } from "./ArtZoom";
import { ArtworkOptionTile } from "./ArtworkOptionTile";
import { ArtworkSearch } from "./ArtworkSearch";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { cn } from "../lib/utils";

// Initials for the stand-in tile: "Black Moth Super Rainbow" -> BM.
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  return words.slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");
}

// The stand-in shown while an artist has no usable photo: their initials on a
// muted tile, so the header keeps its shape instead of collapsing (and an
// artist whose stored URL has rotted stops rendering as an empty box).
function Placeholder({ name, size }: { name: string; size: number }) {
  return (
    <div
      style={{ width: size, height: size }}
      className="flex flex-col items-center justify-center gap-1 rounded-md border border-dashed bg-muted/40 text-muted-foreground"
    >
      <span className="text-2xl font-semibold tracking-wide">{initials(name)}</span>
      <span className="flex items-center gap-1 text-[10px]">
        <ImageOff size={11} aria-hidden />
        no artwork
      </span>
    </div>
  );
}

/**
 * An artist's photo, with the artwork picker behind a hover button.
 *
 * Sources disagree and rot: Last.fm has no photo for plenty of bands, and a
 * stored image is sometimes a hotlink that has since gone dead (which is what
 * an empty box in the header actually means). So the picker offers everything
 * the app can find -- Last.fm, Deezer, the artist's own sleeves --
 * plus a URL box, and the choice is remembered against later refreshes.
 */
export function ArtistArtwork({
  artistId,
  name,
  imageUrl,
  locked,
  size = 120,
}: {
  artistId: number;
  name: string;
  imageUrl?: string | null;
  locked?: boolean;
  size?: number;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = React.useState(false);
  const [broken, setBroken] = React.useState(false);
  const [custom, setCustom] = React.useState("");

  // Reset the failure flag when the artist (or their image) changes.
  React.useEffect(() => setBroken(false), [imageUrl]);

  const { data, isFetching, refetch } = useQuery({
    queryKey: ["artistArtwork", artistId],
    queryFn: () => api.artistArtwork(artistId),
    enabled: open,
    staleTime: 60_000,
  });

  const pick = useMutation({
    mutationFn: (url: string | null) => api.setArtistArtwork(artistId, url),
    onSuccess: (_res, url) => {
      setBroken(false);
      setCustom("");
      setOpen(false);
      toast.success(url ? "Artwork updated" : "Back to automatic artwork");
      qc.invalidateQueries({ queryKey: ["artist", artistId] });
      qc.invalidateQueries({ queryKey: ["artists"] });
      qc.invalidateQueries({ queryKey: ["artistArtwork", artistId] });
    },
    onError: () => toast.error("Could not save that artwork"),
  });

  const showImage = !!imageUrl && !broken;
  const options = data?.options ?? [];

  return (
    <>
      <div className="group relative flex-none" style={{ width: size }}>
        {showImage ? (
          <ArtZoom src={imageUrl} alt={`${name} photo`}>
            <img
              src={art(imageUrl)}
              alt=""
              onError={() => setBroken(true)}
              style={{ width: size, height: size }}
              className="rounded-md object-cover"
              loading="lazy"
            />
          </ArtZoom>
        ) : (
          <Placeholder name={name} size={size} />
        )}
        <Button
          size="xs"
          variant="secondary"
          onClick={() => setOpen(true)}
          title="Choose artwork"
          // Always visible while there's nothing to look at, so an empty tile
          // advertises the fix; otherwise it appears on hover or keyboard focus.
          className={cn(
            "absolute right-1 bottom-1 gap-1 px-1.5 shadow transition-opacity focus-visible:opacity-100",
            showImage ? "opacity-0 group-hover:opacity-100" : "opacity-100",
          )}
        >
          <Pencil size={12} aria-hidden />
          Edit
        </Button>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="w-[min(92vw,44rem)] p-4">
          <DialogTitle>Artwork for {name}</DialogTitle>
          <p className="mt-1 text-sm text-muted-foreground">
            {locked
              ? "Chosen by hand, so refreshes leave it alone."
              : "Picked automatically. Choosing one here keeps it through refreshes."}
          </p>

          <div className="mt-3 flex min-h-[112px] flex-wrap gap-2">
            {isFetching && options.length === 0 && (
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                <RefreshCw size={14} className="animate-spin" aria-hidden />
                Looking for artwork...
              </span>
            )}
            {!isFetching && options.length === 0 && (
              <span className="text-sm text-muted-foreground">
                No artwork found for this artist. Paste a URL below.
              </span>
            )}
            {options.map((option) => (
              <ArtworkOptionTile
                key={option.url}
                option={option}
                current={data?.current ?? null}
                busy={pick.isPending}
                onPick={(url) => pick.mutate(url)}
              />
            ))}
          </div>

          {/* Nothing here fits? Ask the sources under another name. */}
          <div className="mt-3 border-t pt-3">
            <ArtworkSearch
              name={name}
              busy={pick.isPending}
              onPick={(url) => pick.mutate(url)}
            />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
            <Input
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
              placeholder="https://example.com/photo.jpg"
              className="h-8 w-[min(100%,20rem)]"
            />
            <Button
              size="sm"
              disabled={!custom.trim() || pick.isPending}
              onClick={() => pick.mutate(custom.trim())}
            >
              Use this URL
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={isFetching}
              onClick={() => refetch()}
              title="Ask the sources again"
            >
              <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} aria-hidden />
              Look again
            </Button>
            {(locked || imageUrl) && (
              <Button
                size="sm"
                variant="ghost"
                disabled={pick.isPending}
                onClick={() => pick.mutate(null)}
                title="Clear the choice and let the app fill it in again"
              >
                <Wand2 size={14} aria-hidden />
                Automatic
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
