import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { Link as RouterLink } from "react-router-dom";
import { LuBell, LuBellRing } from "react-icons/lu";
import { api } from "../api";
import { useNav } from "../nav";
import { AlbumArt } from "../components/AlbumArt";
import { DataTable, type PageSizeOption } from "../components/DataTable";
import { formatDate, relativeDays } from "../lib/format";
import type { Artist, UpcomingRelease } from "../types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

const PAGE_SIZES: PageSizeOption[] = [
  { label: "25", value: "25" },
  { label: "50", value: "50" },
  { label: "100", value: "100" },
  { label: "All", value: "all" },
];

// A followed artist plus their soonest upcoming release (if any).
type FollowRow = Artist & { next?: UpcomingRelease };

const col = createColumnHelper<FollowRow>();

// Link to the album detail page, mirroring the Upcoming page.
function albumHref(r: UpcomingRelease): string {
  const qs = new URLSearchParams({ artist: r.artist_name, title: r.title, from: "following" });
  if (r.mbid) qs.set("mbid", r.mbid);
  if (r.normalized_date) qs.set("date", r.normalized_date);
  if (r.image_url) qs.set("img", r.image_url);
  return "/album?" + qs.toString();
}

export default function Following() {
  const nav = useNav();
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [onlyUpcoming, setOnlyUpcoming] = useState(false);

  const { data: artists } = useQuery({
    queryKey: ["subscriptions"],
    queryFn: () => api.subscriptions().then((d) => d.artists),
  });
  // Same key as the Upcoming page: one fetch serves both.
  const { data: upcoming } = useQuery({
    queryKey: ["upcomingReleases"],
    queryFn: () => api.upcomingReleases().then((d) => d.releases || []),
  });

  function unfollow(id: number) {
    api.setSubscription(id, "none").then(() => qc.invalidateQueries({ queryKey: ["subscriptions"] }));
  }
  function setNotify(id: number, on: boolean) {
    api.setSubscription(id, on ? "notify" : "subscribed").then(() =>
      qc.invalidateQueries({ queryKey: ["subscriptions"] }),
    );
  }

  // Soonest upcoming release per artist.
  const nextByArtist = useMemo(() => {
    const m = new Map<number, UpcomingRelease>();
    for (const r of upcoming || []) {
      const cur = m.get(r.artist_id);
      if (!cur || (r.normalized_date && cur.normalized_date && r.normalized_date < cur.normalized_date)) {
        m.set(r.artist_id, r);
      }
    }
    return m;
  }, [upcoming]);

  const rows = useMemo<FollowRow[]>(() => {
    const q = search.trim().toLowerCase();
    return (artists || [])
      .map((a) => ({ ...a, next: nextByArtist.get(a.id) }))
      .filter((a) => (q ? a.name.toLowerCase().includes(q) : true))
      .filter((a) => (onlyUpcoming ? !!a.next : true));
  }, [artists, nextByArtist, search, onlyUpcoming]);

  const withUpcoming = useMemo(() => rows.filter((r) => r.next).length, [rows]);

  const columns = [
    col.accessor("name", {
      header: "Artist",
      enableSorting: false,
      cell: ({ row }) => {
        const a = row.original;
        return (
          <div className="flex items-center gap-3">
            {/* Last.fm stopped serving artist photos, so plenty of artists have
                none. Their next release's sleeve is a better answer than a
                blank square. */}
            <AlbumArt src={a.image_url || a.next?.image_url} boxSize="40px" rounded="md" />
            <div className="min-w-0">
              <RouterLink to={`/artist/${a.id}`} className="hover:underline">{a.name}</RouterLink>
            </div>
          </div>
        );
      },
    }),
    col.accessor((a) => a.next?.normalized_date, {
      id: "next",
      header: "Next release",
      sortUndefined: "last",
      cell: ({ row }) => {
        const r = row.original.next;
        if (!r) return <span className="text-muted-foreground">-</span>;
        return (
          <div className="min-w-0">
            <RouterLink to={albumHref(r)} className="font-medium hover:underline">{r.title}</RouterLink>
            {r.primary_type && (
              <Badge variant="outline" className="ml-2">{r.primary_type}</Badge>
            )}
          </div>
        );
      },
    }),
    col.accessor((a) => a.next?.normalized_date, {
      id: "date",
      header: "Date",
      sortUndefined: "last",
      cell: ({ row }) => {
        const r = row.original.next;
        if (!r) return <span className="text-muted-foreground">-</span>;
        return (
          <div className="whitespace-nowrap">
            <div>{formatDate(r.normalized_date)}</div>
            <div className="text-sm text-muted-foreground">{relativeDays(r.days_until)}</div>
          </div>
        );
      },
      meta: { width: "9rem" },
    }),
    col.display({
      id: "actions",
      header: "",
      cell: ({ row }) => {
        const a = row.original;
        const on = a.subscription === "notify";
        return (
          <div className="flex items-center justify-end gap-1.5">
            <Button size="xs" variant="outline" onClick={() => unfollow(a.id)}>Following</Button>
            <Tooltip>
              <TooltipTrigger asChild>
                {/* Labelled rather than a bare bell: two icon buttons side by
                    side said nothing about which state they were in. */}
                <Button
                  aria-label={on ? "Notifications on for this artist" : "Notify me about this artist"}
                  aria-pressed={on}
                  size="xs"
                  variant={on ? "default" : "outline"}
                  onClick={() => setNotify(a.id, !on)}
                >
                  {on ? <LuBellRing /> : <LuBell />}
                  {on ? "Notifying" : "Notify"}
                </Button>
              </TooltipTrigger>
              <TooltipContent>
                {on
                  ? `You'll be notified when ${a.name} releases something. Click to stop.`
                  : `Get notified when ${a.name} releases something.`}
              </TooltipContent>
            </Tooltip>
          </div>
        );
      },
      meta: { width: "14rem", align: "end" },
    }),
  ];

  return (
    <div>
      <h1 className="mb-2 text-2xl font-bold">Following</h1>
      {!nav.hide_page_descriptions && (
        <p className="mb-3 text-muted-foreground">
          Artists you follow and their next upcoming release. "Notify" artists also trigger your webhook on a
          new release.
        </p>
      )}

      {!artists ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : artists.length === 0 ? (
        <p className="text-muted-foreground">Not following anyone yet. Follow artists from the Artists page.</p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Input
              className="min-w-[14rem] flex-1"
              type="search"
              placeholder="Filter following..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <Button
              variant={onlyUpcoming ? "secondary" : "outline"}
              onClick={() => setOnlyUpcoming((v) => !v)}
            >
              Only with upcoming
            </Button>
          </div>

          <DataTable
            data={rows}
            columns={columns}
            getRowId={(a) => String(a.id)}
            initialSorting={[{ id: "date", desc: false }]}
            initialPageSize={50}
            pageSizeOptions={PAGE_SIZES}
            emptyText="No artists match."
            summary={(shown, total) =>
              total ? `Showing ${shown} of ${total} following · ${withUpcoming} with upcoming` : ""
            }
          />
        </>
      )}
    </div>
  );
}
