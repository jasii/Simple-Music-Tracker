import { Badge, Box, Button, CheckboxCard, Flex, HStack, Heading, IconButton, Image, Link as CLink, Text, Wrap } from "@chakra-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { LuBell, LuBellRing, LuRotateCw } from "react-icons/lu";
import { api, art } from "../api";
import type { DiscoverItem, DiscoverSourceStatus, DiscoverSourceTag } from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import { ReleaseIcons } from "../lib/format";

// Chakra color palettes for the source badges.
const SRC_PALETTE: Record<string, string> = { lastfm: "red", metacritic: "yellow" };

function itemSources(r: DiscoverItem): DiscoverSourceTag[] {
  return r.sources && r.sources.length ? r.sources : [{ key: r.source, label: r.source_label }];
}

function albumHref(r: DiscoverItem): string | null {
  if (!r.artist || !r.album) return null;
  const qs = new URLSearchParams({ artist: r.artist, title: r.album, from: "discover" });
  if (r.mbid) qs.set("mbid", r.mbid);
  return "/album?" + qs.toString();
}

type DiscoverCache = { sources: DiscoverSourceStatus[]; items: DiscoverItem[] };

export default function Discover() {
  const nav = useNav();
  const qc = useQueryClient();
  // Seed from the cross-navigation cache so returning to Discover paints
  // instantly instead of re-blanking to "Loading...".
  const cached = qc.getQueryData<DiscoverCache>(["discover"]);
  const [items, setItems] = useState<DiscoverItem[]>(cached?.items ?? []);
  const [sources, setSources] = useState<DiscoverSourceStatus[]>(cached?.sources ?? []);
  const [loaded, setLoaded] = useState(!!cached);
  const [loadingMsg, setLoadingMsg] = useState<string | null>(cached ? null : "Loading...");
  const [hidden, setHidden] = useState<Set<string>>(() => {
    try { return new Set(JSON.parse(localStorage.getItem("discoverHidden") || "[]")); } catch { return new Set(); }
  });
  const [view, setView] = useState<"agenda" | "calendar">(() => {
    try { return (localStorage.getItem("discoverView") as "agenda" | "calendar") || "agenda"; } catch { return "agenda"; }
  });
  const pollTimer = useRef<ReturnType<typeof setTimeout>>();

  const load = useCallback(
    (refresh?: string | false, poll = false) => {
      // Only blank the list on the very first load. Refreshes keep the current
      // items on screen (status line shows progress) so the window doesn't flash.
      if (!poll && !loaded) setLoadingMsg("Loading...");
      const q = refresh ? "?refresh=" + encodeURIComponent(refresh) : "";
      api
        .getJSON<{ sources: DiscoverSourceStatus[]; items: DiscoverItem[]; count: number; refreshing: boolean }>(
          "/api/discover/releases" + q,
        )
        .then((data) => {
          const srcs = data.sources || [];
          const its = data.items || [];
          setSources(srcs);
          setItems(its);
          setLoaded(true);
          setLoadingMsg(null);
          // Persist across navigation so the next visit shows cached results.
          qc.setQueryData<DiscoverCache>(["discover"], { sources: srcs, items: its });
          if (pollTimer.current) clearTimeout(pollTimer.current);
          if (data.refreshing) pollTimer.current = setTimeout(() => load(undefined, true), 5000);
        })
        .catch(() => {
          if (!poll && !loaded) setLoadingMsg("Failed to load.");
        });
    },
    [loaded, qc],
  );

  useEffect(() => {
    // Refresh in the background; stay silent when we already have cached data.
    load(false, !!cached);
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
    return api.trackByName(artist, "subscribed").then((r: any) => { if (!r.error) setFollowing(artist, true); });
  }
  function unfollow(artist: string) {
    return api.trackByName(artist, "none").then((r: any) => { if (!r.error) setFollowing(artist, false); });
  }
  function setNotify(artist: string, on: boolean) {
    return api.trackByName(artist, on ? "notify" : "subscribed").then(() => {});
  }

  const configuredAny = sources.some((s) => s.configured);
  const visible = useMemo(
    () => items.filter((r) => itemSources(r).some((s) => !hidden.has(s.key as string))),
    [items, hidden],
  );

  const noSources = loaded && !configuredAny;

  // Live status line: counts reflect the source checkboxes (client-side filter),
  // so toggling Last.fm / Metacritic updates the number immediately.
  const shownCount = sources.filter((s) => s.configured && !s.error && !hidden.has(s.key)).length;
  const busy = sources.filter((s) => s.refreshing);
  const errs = sources.filter((s) => s.error);
  const statusLine =
    shownCount === 0
      ? "No releases to show. Pick a source or add one in Settings."
      : `${visible.length} releases from ${shownCount} ${shownCount === 1 ? "source" : "sources"}` +
        (busy.length ? ` · refreshing ${busy.map((s) => s.label).join(", ")}...` : "") +
        (errs.length ? ` · ${errs.map((s) => `${s.label}: ${s.error}`).join("; ")}` : "");

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
                <CheckboxCard.Root
                  key={s.key}
                  size="sm"
                  w="auto"
                  variant="surface"
                  colorPalette={SRC_PALETTE[s.key] ?? "gray"}
                  checked={!hidden.has(s.key)}
                  onCheckedChange={(e) => toggleSource(s.key, !!e.checked)}
                >
                  <CheckboxCard.HiddenInput />
                  <CheckboxCard.Control py="1" px="2" gap="1.5" minH="0" alignItems="center" cursor="pointer">
                    <CheckboxCard.Label>{s.label}{s.error ? " (error)" : ""}</CheckboxCard.Label>
                    <IconButton
                      aria-label={`Refresh ${s.label}`}
                      title={`Refresh ${s.label}`}
                      size="2xs"
                      variant="ghost"
                      color={hidden.has(s.key) ? "fg" : undefined}
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); load(s.key); }}
                    >
                      <LuRotateCw />
                    </IconButton>
                  </CheckboxCard.Control>
                </CheckboxCard.Root>
              ),
            )}
          </Wrap>
          <Button size="sm" variant="outline" onClick={() => load("all")}>Refresh all</Button>
        </HStack>
      </Flex>
      {!nav.hide_page_descriptions && (
        <Text color="fg.muted" mt="1">Find new music to track that may not be in your library yet.</Text>
      )}
      <Text color="fg.muted" mb="3">{statusLine}</Text>

      {loadingMsg ? (
        <Text color="fg.muted">{loadingMsg}</Text>
      ) : noSources ? (
        <Text color="fg.muted">
          No discovery sources configured yet. Add your Last.fm cookie in{" "}
          <CLink as={RouterLink} {...{ to: "/settings" }}>Settings</CLink>.
        </Text>
      ) : shownCount === 0 ? null : view === "calendar" ? (
        <Calendar items={visible} renderEvent={(r, k) => <CalEvent key={k} r={r} hidden={hidden} />} />
      ) : (
        <Agenda
          items={visible}
          renderItem={(r, k) => <AgendaRow key={k} r={r} hidden={hidden} onFollow={follow} onUnfollow={unfollow} onNotify={setNotify} />}
          emptyMsg="No releases to show. Pick a source or add one in Settings."
        />
      )}
    </Box>
  );
}

function AgendaRow({
  r,
  hidden,
  onFollow,
  onUnfollow,
  onNotify,
}: {
  r: DiscoverItem;
  hidden: Set<string>;
  onFollow: (a: string) => Promise<void>;
  onUnfollow: (a: string) => Promise<void>;
  onNotify: (a: string, on: boolean) => Promise<void>;
}) {
  const href = albumHref(r);
  const [busy, setBusy] = useState(false);
  const [notifyOn, setNotifyOn] = useState(false);
  const [bellBusy, setBellBusy] = useState(false);
  function toggleFollow() {
    if (!r.artist) return;
    setBusy(true);
    if (r.following) setNotifyOn(false);
    (r.following ? onUnfollow(r.artist) : onFollow(r.artist)).finally(() => setBusy(false));
  }
  function toggleNotify() {
    if (!r.artist) return;
    const next = !notifyOn;
    setBellBusy(true);
    onNotify(r.artist, next).then(() => setNotifyOn(next)).finally(() => setBellBusy(false));
  }
  return (
    <Flex gap="3" py="2.5" align="center">
      <Box className={!r.image ? "vinyl-art" : undefined} boxSize="150px" flex="none" rounded="md" bg="bg.muted" overflow="hidden">
        {r.image && <Image src={art(r.image)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
      </Box>
      <Box flex="1" minW="0">
        <Text fontWeight="semibold">
          {href ? (
            <CLink as={RouterLink} {...{ to: href }}>{r.album}</CLink>
          ) : r.album_url ? (
            <CLink href={r.album_url} target="_blank" rel="noopener">{r.album}</CLink>
          ) : (
            r.album
          )}
        </Text>
        <Box>
          {r.artist_url ? (
            <CLink href={r.artist_url} target="_blank" rel="noopener">{r.artist}</CLink>
          ) : (
            <Text as="span">{r.artist}</Text>
          )}
        </Box>
        {r.context && <Text color="fg.muted" fontSize="sm">{r.context}</Text>}
        {r.genres && r.genres.length > 0 && (
          <Wrap gap="1.5" mt="1">
            {r.genres.map((g) => (
              <Badge key={g} variant="outline" colorPalette="gray" textTransform="capitalize">{g}</Badge>
            ))}
          </Wrap>
        )}
        <Wrap gap="1.5" mt="1.5">
          {itemSources(r).filter((s) => !hidden.has(s.key as string)).map((s) => (
            <Badge key={s.key} colorPalette={SRC_PALETTE[s.key as string] ?? "gray"} variant="surface">
              {s.label}
            </Badge>
          ))}
        </Wrap>
        <HStack mt="2" gap="1.5">
          <Button
            size="xs"
            variant="surface"
            colorPalette="gray"
            bg={r.following ? undefined : "fg"}
            color={r.following ? undefined : "bg"}
            _hover={r.following ? undefined : { bg: "fg.muted" }}
            loading={busy}
            onClick={toggleFollow}
          >
            {r.following ? "Following" : "Follow"}
          </Button>
          {r.following && (
            <IconButton
              aria-label={notifyOn ? "Disable new-release notifications" : "Notify on new release"}
              title={notifyOn ? "Disable new-release notifications" : "Notify on new release"}
              size="xs"
              variant={notifyOn ? "solid" : "surface"}
              colorPalette="gray"
              bg={notifyOn ? "fg" : undefined}
              color={notifyOn ? "bg" : "fg"}
              _hover={notifyOn ? { bg: "fg.muted" } : undefined}
              loading={bellBusy}
              onClick={toggleNotify}
            >
              {notifyOn ? <LuBellRing /> : <LuBell />}
            </IconButton>
          )}
        </HStack>
      </Box>
      {r.artist && r.album && (
        <Box alignSelf="flex-start" flex="none">
          <ReleaseIcons artist={r.artist} album={r.album} mbid={r.mbid} />
        </Box>
      )}
    </Flex>
  );
}

function CalEvent({ r, hidden }: { r: DiscoverItem; hidden: Set<string> }) {
  const label = r.artist || r.album || "";
  const inApp = albumHref(r);
  const srcs = itemSources(r).filter((s) => !hidden.has(s.key as string));
  const palette = SRC_PALETTE[srcs[0]?.key as string] ?? "gray";
  const tip = srcs.map((s) => s.label).filter(Boolean).join(" + ") + ": " + (r.artist ? r.artist + " – " : "") + (r.album || "");
  const common = { size: "sm" as const, variant: "surface" as const, colorPalette: palette, fontSize: "2xs", px: "1", py: "0", title: tip };

  return inApp ? (
    <Badge as={RouterLink} {...{ to: inApp }} {...common}>{label}</Badge>
  ) : (
    <Badge asChild {...common}>
      <a href={r.album_url || r.artist_url || "#"} target="_blank" rel="noopener">{label}</a>
    </Badge>
  );
}
