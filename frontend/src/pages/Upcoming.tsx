import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { AlbumArt } from "../components/AlbumArt";
import type { UpcomingRelease } from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import { usePreviewPlayer } from "../components/PreviewPlayer";
import { ReleaseIcons, timeAgo } from "../lib/format";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { LuLoaderCircle, LuPlay, LuRadio } from "react-icons/lu";
import { toast } from "sonner";

const TYPES = [
  { value: "Album", label: "Albums" },
  { value: "EP", label: "EPs" },
  { value: "Single", label: "Singles" },
];

function albumHref(r: UpcomingRelease): string {
  const qs = new URLSearchParams({ artist: r.artist_name, title: r.title, from: "upcoming" });
  if (r.mbid) qs.set("mbid", r.mbid);
  if (r.normalized_date) qs.set("date", r.normalized_date);
  if (r.image_url) qs.set("img", r.image_url);
  return "/album?" + qs.toString();
}

/**
 * Build a playlist to get ready for the week's releases.
 *
 * None of the records on this page are out yet, so the playlist is made of
 * their artists' best-known songs -- the ones your own server already holds.
 * Only shown when a library that can take a playlist is set up.
 */
function HypeButtons() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const { toggle } = usePreviewPlayer();
  const { data } = useQuery({
    queryKey: ["hypePlaylist"],
    queryFn: () => api.hypePlaylistState(),
    staleTime: 60_000,
  });
  if (!data?.available) return null;
  const state = data;

  // The choices stick: the menu shows what the weekly rebuild will use, so
  // changing one here changes both rather than being forgotten on close.
  async function choose(key: string, value: string) {
    await api.saveSettings({ [key]: value });
    qc.invalidateQueries({ queryKey: ["hypePlaylist"] });
    qc.invalidateQueries({ queryKey: ["settings"] });
  }

  async function build() {
    setBusy(true);
    try {
      const r = await api.buildHypePlaylist();
      if (r.error) {
        toast.error(r.error);
      } else if (r.playlist) {
        toast.success(r.message);
        qc.invalidateQueries({ queryKey: ["hypePlaylist"] });
      } else {
        // Nothing to put in it: say why rather than claiming success.
        toast.info(r.message);
      }
    } catch {
      toast.error(`Could not build the ${state.name} playlist.`);
    } finally {
      setBusy(false);
    }
  }

  // Straight into the app's own player: these are tracks the library holds,
  // so each one streams from there like anything else.
  function play() {
    const queue = state.tracks.map((t) => ({
      title: t.title,
      artist: t.artist,
      album: t.album,
      note: state.label,
      noteIcon: state.icon,
      src: null,
    }));
    if (!queue.length) {
      toast.info(`${state.name} is empty. Build it first.`);
      return;
    }
    toggle(queue, 0);
  }

  return (
    <div className="flex items-center gap-2">
      {/* The button opens its own settings rather than guessing: how many
          songs each artist gets, and how far ahead to look. */}
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button size="icon-sm" variant="outline" disabled={busy}
                  aria-label={`Build the ${state.name} playlist`}
                  title={`Build "${state.name}" on ${state.label}`}>
            {busy ? <LuLoaderCircle className="animate-spin" aria-hidden /> : <LuRadio aria-hidden />}
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-[15rem]">
          <DropdownMenuLabel className="text-sm text-foreground">
            {`"${state.name}" on ${state.label}`}
          </DropdownMenuLabel>
          <DropdownMenuLabel className="pt-0">
            {state.schedule.last_run
              ? `Last built ${timeAgo(state.schedule.last_run)}`
              : "Never built yet"}
            {state.schedule.next_run
              ? ` · next ${new Date(state.schedule.next_run).toLocaleString(undefined, {
                  weekday: "short", hour: "numeric", minute: "2-digit",
                })}`
              : ""}
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>Songs per artist</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={String(state.schedule.per_artist)}
            onValueChange={(v) => choose("hype_playlist_per_artist", v)}
          >
            {state.schedule.per_artist_choices.map((n) => (
              <DropdownMenuRadioItem key={n} value={String(n)}
                                     onSelect={(e) => e.preventDefault()}>
                {n === 1 ? "1 song" : `${n} songs`}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>How far ahead</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={String(state.schedule.days)}
            onValueChange={(v) => choose("hype_playlist_days", v)}
          >
            {state.schedule.windows.map((w) => (
              <DropdownMenuRadioItem key={w.days} value={String(w.days)}
                                     onSelect={(e) => e.preventDefault()}>
                {w.label}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setTimeout(build, 0)} disabled={busy}>
            <LuRadio aria-hidden />
            {busy ? "Building..." : "Build it now"}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {state.tracks.length > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button size="icon-sm" variant="outline" onClick={play}
                    aria-label={`Play ${state.name}`}>
              <LuPlay aria-hidden />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {`Play "${state.name}" (${state.tracks.length} tracks)`}
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}

function AgendaRow({ r }: { r: UpcomingRelease }) {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <AlbumArt src={r.image_url} boxSize="150px" rounded="md" />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          <RouterLink to={albumHref(r)} className="hover:underline">{r.title}</RouterLink>
        </p>
        <div>
          <RouterLink to={`/artist/${r.artist_id}`} className="hover:underline">{r.artist_name}</RouterLink>
        </div>
        {r.primary_type && <Badge variant="outline" className="mt-1.5">{r.primary_type}</Badge>}
      </div>
      <div className="flex-none self-start">
        <ReleaseIcons artist={r.artist_name} album={r.title} mbid={r.mbid} />
      </div>
    </div>
  );
}

// Row in the selected-day list under the calendar: "Artist -- Title".
function CalEvent({ r }: { r: UpcomingRelease }) {
  return (
    <RouterLink to={`/artist/${r.artist_id}`} className="block text-sm hover:underline">
      {r.artist_name} - {r.title}
    </RouterLink>
  );
}

export default function Upcoming() {
  const nav = useNav();
  const { data: items } = useQuery({
    queryKey: ["upcomingReleases"],
    queryFn: () => api.upcomingReleases().then((d) => d.releases || []),
  });
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem("upcomingHiddenTypes") || "[]"));
    } catch {
      return new Set();
    }
  });
  const [view, setView] = useState<"agenda" | "calendar">(() => {
    try {
      return (localStorage.getItem("upcomingView") as "agenda" | "calendar") || "agenda";
    } catch {
      return "agenda";
    }
  });

  function toggleType(value: string, checked: boolean) {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (checked) next.delete(value); else next.add(value);
      try { localStorage.setItem("upcomingHiddenTypes", JSON.stringify(Array.from(next))); } catch {}
      return next;
    });
  }

  function changeView(v: "agenda" | "calendar") {
    setView(v);
    try { localStorage.setItem("upcomingView", v); } catch {}
  }

  const visible = useMemo(
    () => (items || []).filter((r) => !r.primary_type || !hiddenTypes.has(r.primary_type)),
    [items, hiddenTypes],
  );

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Upcoming Releases</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <div className="flex flex-wrap gap-2">
            {TYPES.map((t) => {
              const on = !hiddenTypes.has(t.value);
              return (
                <Button
                  key={t.value}
                  size="sm"
                  variant={on ? "secondary" : "outline"}
                  onClick={() => toggleType(t.value, !on)}
                >
                  {t.label}
                </Button>
              );
            })}
          </div>
          <HypeButtons />
          <ViewToggle view={view} onChange={changeView} />
        </div>
      </div>
      {!nav.hide_page_descriptions && (
        <p className="mt-1 mb-3 text-muted-foreground">New and upcoming albums from artists you follow.</p>
      )}

      {/* Its own spacing: with page descriptions hidden there's no paragraph
          above to hold the view off the toolbar. */}
      <div className="mt-4">
      {!items ? (
        <p className="text-muted-foreground">Loading...</p>
      ) : view === "calendar" ? (
        <Calendar items={visible} itemDots={() => ["bg-green-500"]} renderEvent={(r, k) => <CalEvent key={k} r={r} />} />
      ) : (
        <Agenda
          items={visible}
          renderItem={(r, k) => <AgendaRow key={k} r={r} />}
          emptyMsg="No upcoming releases from artists you follow."
        />
      )}
      </div>
    </div>
  );
}
