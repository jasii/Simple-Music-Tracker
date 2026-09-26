import * as React from "react";

import { art } from "../api";
import type { ArtworkOption } from "../types";
import { cn } from "../lib/utils";

/** One artwork candidate in the picker: a tile that hides itself if it 404s. */
export function ArtworkOptionTile({
  option,
  current,
  busy,
  onPick,
}: {
  option: ArtworkOption;
  current: string | null;
  busy: boolean;
  onPick: (url: string) => void;
}) {
  // A dead hotlink is the usual reason artwork is missing in the first place,
  // so a candidate that won't load is simply not offered.
  const [broken, setBroken] = React.useState(false);
  if (broken) return null;
  const isCurrent = option.url === current;
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => onPick(option.url)}
      title={option.label}
      className={cn(
        "group flex w-[104px] flex-col gap-1 rounded-md border p-1 text-left transition-colors hover:border-primary disabled:opacity-60",
        isCurrent ? "border-primary bg-muted/60" : "border-transparent",
      )}
    >
      <img
        src={art(option.url)}
        alt={option.label}
        onError={() => setBroken(true)}
        className="h-[96px] w-[96px] rounded object-cover"
        loading="lazy"
      />
      <span className="truncate text-[11px] leading-4 text-muted-foreground">
        {isCurrent ? "In use" : option.label}
      </span>
    </button>
  );
}
