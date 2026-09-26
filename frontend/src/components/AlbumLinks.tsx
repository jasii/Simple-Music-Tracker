import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LuLink, LuLoaderCircle, LuX } from "react-icons/lu";
import { toast } from "sonner";
import { api } from "../api";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "./ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./ui/select";
import { shortQuality } from "../lib/format";

// Connecting a library album to a MusicBrainz release group by hand, for the
// pairs no title rule will agree on. Matching by name already handles most of
// them ("Young Heartache" is "Young Heartache EP"), so this only has to cover
// the leftovers: a record your library names something else entirely, or two
// releases a loose match wrongly treats as one.
export function AlbumLinks({
  artistId,
  onChange,
  open: openProp,
  onOpenChange,
}: {
  artistId: number;
  onChange: () => void;
  /** Controlled from outside (the artist page's tools menu) when given. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [ownOpen, setOwnOpen] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? !!openProp : ownOpen;
  const setOpen = controlled ? (onOpenChange ?? (() => {})) : setOwnOpen;
  const [busy, setBusy] = useState<string | null>(null);
  const { data, refetch, isPending } = useQuery({
    queryKey: ["albumLinks", artistId],
    queryFn: () => api.albumLinks(artistId),
    enabled: open,
  });

  const releases = data?.releases ?? [];
  const byTitle = new Map(releases.map((r) => [r.title, r]));

  function save(albumKey: string, rgMbid: string, linked: boolean | null, label: string) {
    setBusy(albumKey + rgMbid);
    api
      .setAlbumLink(artistId, albumKey, rgMbid, linked)
      .then(() => {
        refetch();
        onChange();
        toast.success(label);
      })
      .catch(() => toast.error("Could not save that match."))
      .finally(() => setBusy(null));
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {!controlled && (
        <DialogTrigger asChild>
          <Button size="xs" variant="outline">
            <LuLink aria-hidden /> Match albums
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="w-[min(92vw,44rem)] max-w-none p-4">
        <DialogTitle>Match albums to releases</DialogTitle>
        <p className="mt-1 text-sm text-muted-foreground">
          What each album in your library counts as. Connect one the names don't
          match, or disconnect a pair that isn't really the same record.
        </p>
        {isPending && (
          <p className="mt-3 flex items-center gap-2 text-muted-foreground">
            <LuLoaderCircle aria-hidden className="animate-spin" /> Reading your library
          </p>
        )}
        <div className="mt-3 max-h-[60vh] divide-y overflow-y-auto">
          {(data?.library ?? []).map((album) => (
            <div key={album.album_key} className="py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{album.album_key}</span>
                {album.format && <Badge variant="outline">{shortQuality(album.format)}</Badge>}
                <span className="text-xs text-muted-foreground">
                  {album.sources.join(", ")}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {album.matched_releases.length === 0 && (
                  <span className="text-sm text-muted-foreground">
                    not counted as any release
                  </span>
                )}
                {album.matched_releases.map((title) => (
                  <Badge key={title} variant="secondary" className="gap-1">
                    {title}
                    <button
                      type="button"
                      aria-label={`Disconnect ${album.album_key} from ${title}`}
                      title="These are not the same record"
                      disabled={busy !== null}
                      onClick={() => {
                        const rg = byTitle.get(title);
                        if (rg?.mbid)
                          save(album.album_key, rg.mbid, false, `Disconnected ${title}.`);
                      }}
                    >
                      <LuX aria-hidden />
                    </button>
                  </Badge>
                ))}
                <Select
                  value=""
                  onValueChange={(mbid) => {
                    const rg = releases.find((r) => r.mbid === mbid);
                    save(album.album_key, mbid, true, `Connected to ${rg?.title ?? "release"}.`);
                  }}
                >
                  <SelectTrigger className="h-7 w-auto text-xs" aria-label="Connect to a release">
                    <SelectValue placeholder="Connect to..." />
                  </SelectTrigger>
                  <SelectContent>
                    {releases
                      .filter((r) => r.mbid && !album.matched_releases.includes(r.title))
                      .map((r) => (
                        <SelectItem key={r.mbid} value={r.mbid!}>
                          {r.title}
                          {r.type ? ` (${r.type})` : ""}
                          {r.owned ? " - owned" : ""}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                {album.links.length > 0 && (
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={busy !== null}
                    onClick={() => {
                      // Forget every instruction for this album at once.
                      album.links.forEach((l) =>
                        save(album.album_key, l.rg_mbid, null, "Back to matching by name."),
                      );
                    }}
                  >
                    Reset
                  </Button>
                )}
              </div>
            </div>
          ))}
          {!isPending && (data?.library ?? []).length === 0 && (
            <p className="py-2 text-muted-foreground">
              No albums by this artist in your library yet.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
