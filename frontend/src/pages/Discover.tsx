import { Badge, Box, Button, Flex, HStack, Heading, IconButton, Image, Link as CLink, Text, Wrap } from "@chakra-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { LuRotateCw, LuX } from "react-icons/lu";
import { api, art } from "../api";
import type { DiscoverItem, DiscoverSourceStatus, DiscoverSourceTag } from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import { ReleaseIcons } from "../lib/format";

const SRC_COLORS: Record<string, string> = { lastfm: "#d51007", metacritic: "#ffcc33" };

function itemSources(r: DiscoverItem): DiscoverSourceTag[] {
  return r.sources && r.sources.length ? r.sources : [{ key: r.source, label: r.source_label }];
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function albumHref(r: DiscoverItem): string | null {
  if (!r.artist || !r.album) return null;
  const qs = new URLSearchParams({ artist: r.artist, title: r.album, from: "discover" });
  if (r.mbid) qs.set("mbid", r.mbid);
  return "/album?" + qs.toString();
}

export default function Discover() {
  const nav = useNav();
  const [items, setItems] = useState<DiscoverItem[]>([]);
  const [sources, setSources] = useState<DiscoverSourceStatus[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [status, setStatus] = useState("");
  const [loadingMsg, setLoadingMsg] = useState<string | null>("Loading...");
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("discoverHidden") || "[]")); } catch { return new Set(); }
  });
  const [view, setView] = useState<"agenda" | "calendar">(() => {
    try { return (localStorage.getItem("discoverView") as "agenda" | "calendar") || "agenda"; } catch { return "agenda"; }
  });
  const pollTimer = useRef<ReturnType<typeof setTimeout>>();

  const load = useCallback(
    (refresh?: string | false, poll = false) => {
      if (!poll) setLoadingMsg(loaded ? "Refreshing..." : "Loading...");
      const q = refresh ? "?refresh=" + encodeURIComponent(refresh) : "";
      api
        .getJSON<{ sources: DiscoverSourceStatus[]; items: DiscoverItem[]; count: number; refreshing: boolean }>(
          "/api/discover/releases" + q,
        )
        .then((data) => {
          setSources(data.sources || []);
          setItems(data.items || []);
          setLoaded(true);
          setLoadingMsg(null);
          const errs = (data.sources || []).filter((s) => s.error);
          const busy = (data.sources || []).filter((s) => s.refreshing);
          setStatus(
            `${data.count} releases from ${(data.sources || []).filter((s) => s.configured && !s.error).length} source(s)` +
              (busy.length ? ` · refreshing ${busy.map((s) => s.label).join(", ")}...` : "") +
              (errs.length ? ` · ${errs.map((s) => `${s.label}: ${s.error}`).join("; ")}` : ""),
          );
          if (pollTimer.current) clearTimeout(pollTimer.current);
          if (data.refreshing) pollTimer.current = setTimeout(() => load(undefined, true), 5000);
        })
        .catch(() => {
          if (!poll) setLoadingMsg("Failed to load.");
        });
    },
    [loaded],
  );

  useEffect(() => {
    load(false);
    return () => { if (pollTimer.current) clearTimeout(pollTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleSource(key: string, checked: boolean) {
    setHidden((prev) => {
      const next = new Set(prev);
      if (checked) next.delete(key); else next.add(key);
      try { localStorage.setItem("discoverHidden", JSON.stringify(Array.from(next))); } catch {}
      return next;
    });
  }
  function changeView(v: "agenda" | "calendar") {
    setView(v);
    try { localStorage.setItem("discoverView", v); } catch {}
  }

  function setFollowing(artist: string, following: boolean) {
    setItems((prev) =>
      prev.map((it) => ((it.artist || "").toLowerCase() === artist.toLowerCase() ? { ...it, following } : it)),
    );
  }
  function follow(artist: string) {
    api.trackByName(artist, "subscribed").then((r: any) => { if (!r.error) setFollowing(artist, true); });
  }
  function unfollow(artist: string) {
    api.trackByName(artist, "none").then((r: any) => { if (!r.error) setFollowing(artist, false); });
  }

  const configuredAny = sources.some((s) => s.configured);
  const visible = useMemo(
    () => items.filter((r) => itemSources(r).some((s) => !hidden.has(s.key as string))),
    [items, hidden],
  );

  const noSources = loaded && !configuredAny;

  return (
    <Box>
      <Flex align="center" justify="space-between" gap="3" wrap="wrap">
        <Heading size="xl">Discover</Heading>
        <HStack gap="2" wrap="wrap" fontSize="sm">
          <ViewToggle view={view} onChange={changeView} />
          <Box w="1px" alignSelf="stretch" minH="1.2em" bg="border" />
          <Wrap gap="2" align="center">
            {sources.map((s) =>
              !s.configured ? (
                <Badge key={s.key} variant="outline" colorPalette="gray">
                  {s.label} <CLink as={RouterLink} {...{ to: "/settings" }} ml="1">set up</CLink>
                </Badge>
              ) : (
                <HStack key={s.key} gap="1">
                  <input
                    type="checkbox"
                    aria-label={s.label}
                    checked={!hidden.has(s.key)}
                    onChange={(e) => toggleSource(s.key, e.target.checked)}
                  />
                  <Badge variant="outline" style={{ borderColor: SRC_COLORS[s.key], color: SRC_COLORS[s.key] }}>
                    {s.label}{s.error ? " (error)" : ""}
                    <IconButton aria-label={`Refresh ${s.label}`} title={`Refresh ${s.label}`} size="2xs" variant="ghost" ml="1" onClick={() => load(s.key)}>
                      <LuRotateCw />
                    </IconButton>
                  </Badge>
                </HStack>
              ),
            )}
          </Wrap>
          <Button size="sm" variant="outline" onClick={() => load("all")}>Refresh all</Button>
        </HStack>
      </Flex>
      {!nav.hide_page_descriptions && (
        <Text color="fg.muted" mt="1">Find new music to track that may not be in your library yet.</Text>
      )}
      <Text color="fg.muted" mb="3">{status}</Text>

      {loadingMsg ? (
        <Text color="fg.muted">{loadingMsg}</Text>
      ) : noSources ? (
        <Text color="fg.muted">
          No discovery sources configured yet. Add your Last.fm cookie in{" "}
          <CLink as={RouterLink} {...{ to: "/settings" }}>Settings</CLink>.
        </Text>
      ) : view === "calendar" ? (
        <Calendar items={visible} renderEvent={(r, k) => <CalEvent key={k} r={r} hidden={hidden} />} />
      ) : (
        <Agenda
          items={visible}
          renderItem={(r, k) => <AgendaRow key={k} r={r} onFollow={follow} onUnfollow={unfollow} />}
          emptyMsg="No releases to show. Pick a source or add one in Settings."
        />
      )}
    </Box>
  );
}

function AgendaRow({
  r,
  onFollow,
  onUnfollow,
}: {
  r: DiscoverItem;
  onFollow: (a: string) => void;
  onUnfollow: (a: string) => void;
}) {
  const href = albumHref(r);
  return (
    <Flex gap="3" py="2.5" align="center">
      <Box className={!r.image ? "vinyl-art" : undefined} boxSize="150px" flex="none" rounded="md" bg="bg.muted" overflow="hidden">
        {r.image && <Image src={art(r.image)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
      </Box>
      <Box flex="1" minW="0">
        <HStack gap="2" wrap="wrap">
          <Text fontWeight="semibold">
            {href ? (
              <CLink as={RouterLink} {...{ to: href }}>{r.album}</CLink>
            ) : r.album_url ? (
              <CLink href={r.album_url} target="_blank" rel="noopener">{r.album}</CLink>
            ) : (
              r.album
            )}
          </Text>
          {itemSources(r).map((s) => (
            <Badge key={s.key} variant="outline" style={{ borderColor: SRC_COLORS[s.key as string], color: SRC_COLORS[s.key as string] }}>
              {s.label}
            </Badge>
          ))}
        </HStack>
        <Box>
          {r.artist_url ? (
            <CLink href={r.artist_url} target="_blank" rel="noopener">{r.artist}</CLink>
          ) : (
            <Text as="span">{r.artist}</Text>
          )}
        </Box>
        {r.normalized_date && <Text color="fg.muted" fontSize="sm">{fmtDate(r.normalized_date)}</Text>}
        {r.context && <Text color="fg.muted" fontSize="sm">{r.context}</Text>}
        {r.genres && r.genres.length > 0 && (
          <Wrap gap="1.5" mt="1">
            {r.genres.map((g) => (
              <Badge key={g} variant="outline" colorPalette="gray" textTransform="capitalize">{g}</Badge>
            ))}
          </Wrap>
        )}
        {r.artist && r.album && <ReleaseIcons artist={r.artist} album={r.album} mbid={r.mbid} />}
        <Box mt="2">
          {r.following ? (
            <Badge colorPalette="green" variant="outline">
              following
              <IconButton aria-label="Unfollow" title="Unfollow" size="2xs" variant="ghost" ml="1" onClick={() => r.artist && onUnfollow(r.artist)}>
                <LuX />
              </IconButton>
            </Badge>
          ) : (
            <Button size="xs" variant="outline" onClick={() => r.artist && onFollow(r.artist)}>Follow</Button>
          )}
        </Box>
      </Box>
    </Flex>
  );
}

function CalEvent({ r, hidden }: { r: DiscoverItem; hidden: Set<string> }) {
  const label = (r.artist ? r.artist + " – " : "") + (r.album || "");
  const inApp = albumHref(r);
  const srcs = itemSources(r).filter((s) => !hidden.has(s.key as string));
  const colors = srcs.map((s) => SRC_COLORS[s.key as string]).filter(Boolean);
  const tip = srcs.map((s) => s.label).filter(Boolean).join(" + ") + ": " + label;

  const style: React.CSSProperties = {};
  if (colors.length > 1) {
    style.borderLeft = "none";
    style.boxShadow = colors.map((c, i) => `inset ${2 * (i + 1)}px 0 0 0 ${c}`).join(",");
    style.paddingLeft = `${2 * colors.length + 4}px`;
  }
  const borderColor = colors.length === 1 ? colors[0] : "var(--chakra-colors-green-solid)";

  const common = {
    display: "block",
    bg: "bg.muted",
    borderLeftWidth: colors.length > 1 ? undefined : "2px",
    rounded: "sm",
    px: "1",
    py: "0.5",
    mb: "0.5",
    fontSize: "xs",
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
    color: "fg",
    style: { ...style, borderLeftColor: borderColor },
    title: tip,
  };

  return inApp ? (
    <CLink as={RouterLink} {...{ to: inApp }} {...common}>{label}</CLink>
  ) : (
    <CLink href={r.album_url || r.artist_url || "#"} target="_blank" rel="noopener" {...common}>{label}</CLink>
  );
}
