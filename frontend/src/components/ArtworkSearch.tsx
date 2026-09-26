import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Search } from "lucide-react";

import { api } from "../api";
import { ArtworkOptionTile } from "./ArtworkOptionTile";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

/**
 * Look for an artist's photo under a different name.
 *
 * The automatic sources only ever search the name the library stores, and for
 * a good number of artists that name is not the one the services use: a stage
 * name written with a Greek letter (CHVRCHΞS against CHVRCHES), a
 * transliteration, a duo filed under one member. Typing what the services
 * would call them finds the picture their own row cannot.
 */
export function ArtworkSearch({
  name,
  busy,
  onPick,
}: {
  // Seeds the box, so a one-character correction is usually all it takes.
  name: string;
  busy?: boolean;
  onPick: (url: string) => void;
}) {
  const [text, setText] = React.useState(name);
  // Only searches when asked: each one costs a lookup per source.
  const [query, setQuery] = React.useState("");

  const { data, isFetching } = useQuery({
    queryKey: ["artworkSearch", query],
    queryFn: () => api.artworkSearch(query),
    enabled: query.length > 0,
    staleTime: 5 * 60_000,
  });
  const options = data?.options ?? [];

  function run() {
    const wanted = text.trim();
    if (wanted) setQuery(wanted);
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
          placeholder="Search a different spelling..."
          className="h-8 w-[min(100%,16rem)]"
          aria-label="Artist name to search for"
        />
        <Button size="sm" variant="outline" disabled={!text.trim() || isFetching} onClick={run}>
          {isFetching ? (
            <RefreshCw size={14} className="animate-spin" aria-hidden />
          ) : (
            <Search size={14} aria-hidden />
          )}
          Search
        </Button>
      </div>

      {query && !isFetching && options.length === 0 && (
        <p className="mt-2 text-sm text-muted-foreground">
          No source has a picture filed under "{query}".
        </p>
      )}
      {options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {options.map((option) => (
            <ArtworkOptionTile
              key={option.url}
              option={option}
              current={null}
              busy={!!busy}
              onPick={onPick}
            />
          ))}
        </div>
      )}
    </div>
  );
}
