// Artists with no usable artwork, offered for a human to sort out.
//
// The automatic passes take whatever downloads from the first source that has
// something. What reaches here is the tail they can't settle: a joint credit
// nobody has photographed, a band whose only picture was a hotlink to a site
// that has closed, a name spelled differently on every service. Each one is
// shown with whatever the sources do offer, so picking is one click -- and an
// artist can be set aside when there is genuinely nothing to find.
//
// Stays hidden until an artwork pass has run: before that, "no artwork"
// describes the whole library.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { toast } from "sonner";
import { ImageOff, Search } from "lucide-react";

import { api } from "../api";
import type { ArtworkReviewArtist } from "../types";
import { ArtworkOptionTile } from "./ArtworkOptionTile";
import { ArtworkSearch } from "./ArtworkSearch";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { Separator } from "./ui/separator";

// One artist's row: whatever the sources offer, and a way to skip them.
function ArtistRow({
  artist,
  onDone,
}: {
  artist: ArtworkReviewArtist;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [searching, setSearching] = useState(false);
  // The same per-artist lookup the picker on the artist page uses, so opening
  // this after using that one costs nothing.
  const { data, isFetching } = useQuery({
    queryKey: ["artistArtwork", artist.id],
    queryFn: () => api.artistArtwork(artist.id),
    staleTime: 5 * 60_000,
  });
  // The stored URL is the one that doesn't work, so it isn't offered back.
  const options = (data?.options ?? []).filter((o) => o.source !== "current");

  async function choose(url: string) {
    setBusy(true);
    try {
      await api.setArtistArtwork(artist.id, url);
      toast.success(`Artwork set for ${artist.name}`);
      qc.invalidateQueries({ queryKey: ["artists"] });
      qc.invalidateQueries({ queryKey: ["artist", artist.id] });
      onDone();
    } finally {
      setBusy(false);
    }
  }

  async function skip() {
    setBusy(true);
    try {
      await api.artworkReviewDismiss(artist.id);
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-start gap-3 border-t py-3 first:border-t-0">
      <div className="min-w-[12rem] flex-1">
        <p className="flex flex-wrap items-center gap-2">
          <RouterLink to={`/artist/${artist.id}`} className="font-medium hover:underline">
            {artist.name}
          </RouterLink>
          {artist.followed && <Badge variant="secondary">Following</Badge>}
          <span className="text-xs text-muted-foreground">
            {artist.track_count} tracks
          </span>
        </p>
        <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
          <ImageOff size={12} aria-hidden />
          {artist.image_url ? "stored link serves nothing" : "no artwork stored"}
        </p>
      </div>

      <div className="flex flex-wrap items-start gap-2">
        {isFetching && options.length === 0 && (
          <span className="text-sm text-muted-foreground">Looking...</span>
        )}
        {!isFetching && options.length === 0 && (
          <span className="text-sm text-muted-foreground">
            No source has a picture of them.
          </span>
        )}
        {options.slice(0, 4).map((option) => (
          <ArtworkOptionTile
            key={option.url}
            option={option}
            current={null}
            busy={busy}
            onPick={choose}
          />
        ))}
        <Button
          size="xs"
          variant="ghost"
          disabled={busy}
          onClick={() => setSearching((v) => !v)}
        >
          <Search size={12} aria-hidden />
          {searching ? "Close" : "Search"}
        </Button>
        <Button size="xs" variant="ghost" disabled={busy} onClick={skip}>
          Skip
        </Button>
      </div>

      {/* For the artists whose own name finds nothing: try the spelling the
          services use. */}
      {searching && (
        <div className="w-full">
          <ArtworkSearch name={artist.name} busy={busy} onPick={choose} />
        </div>
      )}
    </div>
  );
}

// Each row asks its sources for candidates as soon as it appears, and some are
// paced at a second or more per call -- so a page of 25 would spend a while
// showing "Looking...". Ten at a time, with more on request.
const PAGE = 10;

/** How many artists are waiting, for the Tools menu to say so. */
export function useArtworkReviewCount() {
  const { data } = useQuery({
    queryKey: ["artworkReview", 0],
    queryFn: () => api.artworkReview(0),
    staleTime: 60_000,
  });
  return {
    ready: !!data?.ready,
    total: data?.total ?? 0,
    dismissed: data?.dismissed ?? 0,
  };
}

/**
 * The review itself, in a dialog opened from the Artists page Tools menu.
 *
 * It lives behind the menu rather than on the page: it is a job you go and do
 * now and then, not a banner to read every time the list loads.
 */
export function ArtworkReviewDialog({
  open,
  onOpenChange,
  ids,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Given, the review covers only these artists (a table selection). */
  ids?: number[];
}) {
  const qc = useQueryClient();
  const [limit, setLimit] = useState(PAGE);
  const scoped = (ids?.length ?? 0) > 0;
  const { data } = useQuery({
    queryKey: ["artworkReview", limit, ids ?? null],
    queryFn: () => api.artworkReview(limit, false, ids),
    enabled: open,
    staleTime: 60_000,
  });

  const reload = () => qc.invalidateQueries({ queryKey: ["artworkReview"] });
  const total = data?.total ?? 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[86vh] w-[min(94vw,52rem)] overflow-y-auto p-4">
        <DialogTitle>
          {scoped ? `Find artwork for ${ids!.length} selected` : "Find artwork"}
        </DialogTitle>
        <p className="mt-1 text-sm text-muted-foreground">
          {total
            ? `${total} ${total === 1 ? "artist has" : "artists have"} no artwork the app can load - a dead link, or nothing found at all.`
            : scoped
              ? "Every artist you selected already has artwork that loads."
              : "No artists left to find artwork for."}
          {(data?.dismissed ?? 0) > 0 && !scoped && ` ${data!.dismissed} set aside.`}
        </p>

        <div className="mt-2">
          {(data?.artists ?? []).map((artist) => (
            <ArtistRow key={artist.id} artist={artist} onDone={reload} />
          ))}
        </div>

        {total > (data?.artists.length ?? 0) && (
          <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            Showing {data?.artists.length ?? 0} of {total}.
            <Button size="xs" variant="outline" onClick={() => setLimit((n) => n + PAGE)}>
              Show {Math.min(PAGE, total - (data?.artists.length ?? 0))} more
            </Button>
          </p>
        )}

        <Separator className="mt-4 mb-3" />
        <div className="flex flex-wrap items-center gap-2">
          {(data?.dismissed ?? 0) > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={async () => {
                await api.artworkReviewRestore();
                reload();
              }}
            >
              Bring back {data!.dismissed} set aside
            </Button>
          )}
          <span className="text-xs text-muted-foreground">
            Filling these automatically, and the counts behind them, live in
            Settings under Metadata.
          </span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
