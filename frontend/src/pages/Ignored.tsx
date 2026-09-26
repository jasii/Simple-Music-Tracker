import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import type { Artist, DiscoverIgnore } from "../types";
import { AlbumArt } from "../components/AlbumArt";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";

export default function Ignored() {
  const qc = useQueryClient();
  const { data: all } = useQuery({
    queryKey: ["ignored"],
    queryFn: () => api.ignored().then((d) => d.artists),
  });
  const [search, setSearch] = useState("");

  const shown = useMemo(() => {
    if (!all) return [];
    const q = search.trim().toLowerCase();
    return q ? all.filter((a) => a.name.toLowerCase().includes(q)) : all;
  }, [all, search]);

  const { data: discoverIgnores } = useQuery({
    queryKey: ["discoverIgnores"],
    queryFn: () => api.discoverIgnores().then((d) => d.ignores),
  });

  function unignore(id: number) {
    api.setIgnore(id, false).then(() =>
      qc.setQueryData<Artist[]>(["ignored"], (prev) => (prev ? prev.filter((a) => a.id !== id) : prev)),
    );
  }
  // Following an artist takes them off this page: the backend un-ignores on
  // subscribe, so the row goes with it.
  function follow(id: number) {
    api.setSubscription(id, "subscribed").then(() =>
      qc.setQueryData<Artist[]>(["ignored"], (prev) => (prev ? prev.filter((a) => a.id !== id) : prev)),
    );
  }
  function removeDiscoverIgnore(id: number) {
    api.removeDiscoverIgnore(id).then(() =>
      qc.setQueryData<DiscoverIgnore[]>(["discoverIgnores"], (prev) =>
        prev ? prev.filter((r) => r.id !== id) : prev,
      ),
    );
  }
  function unignoreAllShown() {
    const ids = shown.map((a) => a.id);
    if (!ids.length) return;
    api.bulkIgnore(ids, false).then(() => qc.invalidateQueries({ queryKey: ["ignored"] }));
  }

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Ignored Artists</h1>
      <p className="mb-3 text-muted-foreground">
        Artists hidden from your library list - ones you ignored yourself, and ones you
        opened from a suggestion. Everything fetched about them is kept, so their page
        loads instantly; follow one to move it back into your library.
      </p>

      <div className="mb-3 flex flex-wrap gap-2">
        <Input
          className="min-w-[14rem] flex-1"
          type="search"
          placeholder="Filter ignored..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <Button variant="outline" onClick={unignoreAllShown}>Unignore all shown</Button>
      </div>

      {!all ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : shown.length === 0 ? (
        <p className="text-muted-foreground">
          {all.length
            ? "No matches."
            : 'No ignored artists. Use "Ignore" on the Artists page to hide ones you don\'t want cluttering your library.'}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {shown.map((a) => {
            const genres = (a.genres || "").split(",").filter(Boolean).slice(0, 4);
            return (
              <div key={a.id} className="flex items-center gap-3 rounded-md border p-2.5">
                {/* Everything fetched about them is still here, so the row can
                    show who they are rather than just a name. */}
                <AlbumArt src={a.image_url} boxSize="44px" rounded="md" />
                <div className="min-w-0 flex-1">
                  <RouterLink to={`/artist/${a.id}`} className="hover:underline">{a.name}</RouterLink>
                  <p className="text-sm text-muted-foreground">
                    {a.track_count ? `${a.track_count} tracks` : "nothing owned"}
                    {a.last_checked ? ` · checked ${a.last_checked.slice(0, 10)}` : ""}
                  </p>
                  {genres.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {genres.map((g) => (
                        <Badge key={g} variant="outline" className="capitalize text-muted-foreground">
                          {g.replace(/[._]/g, " ")}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                <Button size="sm" variant="outline" onClick={() => follow(a.id)}>Follow</Button>
                <Button size="sm" variant="outline" onClick={() => unignore(a.id)}>Unignore</Button>
              </div>
            );
          })}
        </div>
      )}

      {(discoverIgnores?.length ?? 0) > 0 && (
        <>
          <h2 className="mt-8 mb-2 text-xl font-bold">Hidden from Discover</h2>
          <p className="mb-3 text-muted-foreground">
            Artists and releases you've ignored on the Discover page. Remove a rule to let it
            show up in the feed again.
          </p>
          <div className="flex flex-col gap-2">
            {discoverIgnores!.map((r) => (
              <div key={r.id} className="flex items-center gap-3 rounded-md border p-2.5">
                <div className="min-w-0 flex-1">
                  <span>{r.artist}</span>
                  {r.album ? (
                    <span className="text-muted-foreground"> - {r.album}</span>
                  ) : (
                    <span className="text-muted-foreground"> (all releases)</span>
                  )}
                </div>
                <Button size="sm" variant="outline" onClick={() => removeDiscoverIgnore(r.id)}>
                  Unignore
                </Button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
