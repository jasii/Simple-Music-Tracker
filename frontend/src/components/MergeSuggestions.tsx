// Artists that look like one person, offered for merging.
//
// A library built from file tags collects the same artist several times, and
// merging is destructive -- so this only ever suggests. Each group names the row
// everything would fold into (changeable), says why it thinks they match, and
// can be waved away, which sticks until the group itself changes.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { toast } from "sonner";
import { api } from "../api";
import type { DuplicateGroup, DuplicateMember } from "../types";
import { Button } from "./ui/button";
import { Card, CardContent } from "./ui/card";
import { Badge } from "./ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "./ui/collapsible";
import { RadioGroup, RadioGroupItem } from "./ui/radio-group";

function describe(m: DuplicateMember): string {
  const bits = [
    m.track_count ? `${m.track_count} tracks` : "no tracks",
    m.owned_albums ? `${m.owned_albums} albums` : null,
    m.subscription !== "none" ? m.subscription : null,
    m.ignored ? "ignored" : null,
    m.mbid ? "has MBID" : null,
  ].filter(Boolean);
  return bits.join(" · ");
}

export function MergeSuggestions({ onMerged }: { onMerged: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["duplicates"],
    queryFn: () => api.duplicates(),
    staleTime: 5 * 60_000,
  });
  const groups = data?.groups ?? [];
  if (!groups.length && !(data?.dismissed ?? 0)) return null;

  return (
    <Card className="mb-3">
      <CardContent className="py-3">
        <Collapsible open={open} onOpenChange={setOpen}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm">
              {groups.length
                ? `${groups.length} ${groups.length === 1 ? "artist looks" : "artists look"} like duplicates of another.`
                : "No duplicate artists to review."}
              {(data?.dismissed ?? 0) > 0 && (
                <span className="text-muted-foreground">
                  {" "}{data!.dismissed} set aside.
                </span>
              )}
            </p>
            <div className="flex items-center gap-2">
              {(data?.dismissed ?? 0) > 0 && (
                <Button
                  size="xs"
                  variant="ghost"
                  onClick={() =>
                    api.restoreDuplicates().then(() => {
                      qc.invalidateQueries({ queryKey: ["duplicates"] });
                      setOpen(true);
                    })
                  }
                >
                  Review the ones I set aside
                </Button>
              )}
              {groups.length > 0 && (
                <CollapsibleTrigger asChild>
                  <Button size="xs" variant="outline">{open ? "Hide" : "Review"}</Button>
                </CollapsibleTrigger>
              )}
            </div>
          </div>
          <CollapsibleContent>
            <div className="mt-3 flex flex-col gap-3">
              {groups.map((group) => (
                <GroupRow key={group.key} group={group} onMerged={onMerged} />
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  );
}

function GroupRow({ group, onMerged }: { group: DuplicateGroup; onMerged: () => void }) {
  const qc = useQueryClient();
  const all = [group.target, ...group.members];
  // Which row survives. Suggested: the one that knows the most about the
  // artist, but it's the user's call -- the others fold into it.
  const [keepId, setKeepId] = useState(String(group.target.id));
  const [busy, setBusy] = useState(false);

  function merge() {
    const keep = Number(keepId);
    const sources = all.filter((m) => m.id !== keep).map((m) => m.id);
    if (!sources.length) return;
    setBusy(true);
    api
      .merge(keep, sources)
      .then(() => {
        const kept = all.find((m) => m.id === keep)!;
        toast.success(`Merged ${sources.length + 1} rows into ${kept.name}.`);
        qc.invalidateQueries({ queryKey: ["duplicates"] });
        onMerged();
      })
      .catch(() => toast.error("Merge failed."))
      .finally(() => setBusy(false));
  }

  function dismiss() {
    setBusy(true);
    api
      .dismissDuplicate(group.key, group.signature)
      .then(() => qc.invalidateQueries({ queryKey: ["duplicates"] }))
      .finally(() => setBusy(false));
  }

  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Badge variant="outline" className="text-muted-foreground">{group.reason}</Badge>
        {group.dismissed && <Badge variant="secondary">previously set aside</Badge>}
      </div>
      <RadioGroup value={keepId} onValueChange={setKeepId} className="gap-1.5">
        {all.map((m) => (
          <label key={m.id} className="flex cursor-pointer items-center gap-2 text-sm">
            <RadioGroupItem value={String(m.id)} id={`keep-${group.key}-${m.id}`} />
            <RouterLink to={`/artist/${m.id}`} className="font-medium hover:underline">
              {m.name}
            </RouterLink>
            <span className="text-muted-foreground">{describe(m)}</span>
          </label>
        ))}
      </RadioGroup>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button size="xs" disabled={busy} onClick={merge}>
          Merge into {all.find((m) => String(m.id) === keepId)?.name}
        </Button>
        <Button size="xs" variant="outline" disabled={busy} onClick={dismiss}>
          Not duplicates
        </Button>
        <span className="text-xs text-muted-foreground">
          Merging moves tracks, owned albums and releases onto the kept row and deletes
          the others.
        </span>
      </div>
    </div>
  );
}
