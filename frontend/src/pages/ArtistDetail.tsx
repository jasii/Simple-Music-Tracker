import {
  Accordion,
  ActionBar,
  Badge,
  Box,
  Button,
  ButtonGroup,
  Checkbox,
  CloseButton,
  Flex,
  Grid,
  HStack,
  Heading,
  IconButton,
  Image,
  Input,
  Link as CLink,
  Pagination,
  Portal,
  RadioGroup,
  Stack,
  Table,
  Text,
} from "@chakra-ui/react";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LuChevronLeft, LuChevronRight } from "react-icons/lu";
import { Link as RouterLink, useParams } from "react-router-dom";
import { BoxCheck } from "../components/BoxCheck";
import { api, art } from "../api";
import type { Artist, ArtistDetail as ArtistDetailT, DiscographyItem, DiscographyResponse, Subscription } from "../types";
import { formatDate } from "../lib/format";

const CATS: [keyof DiscographyResponse["groups"], string][] = [
  ["album", "Albums"],
  ["ep", "EPs"],
  ["single", "Singles"],
];
const PAGE_SIZE = 25;

export default function ArtistDetail() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const qc = useQueryClient();

  const { data: artist } = useQuery({
    queryKey: ["artist", id],
    queryFn: () => api.artist(id),
  });
  const { data: settings } = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.settings(),
  });
  const {
    data: discoData,
    isError: discoError,
    refetch: refetchDisco,
  } = useQuery({
    queryKey: ["discography", id],
    queryFn: () => api.discography(id),
  });

  const [hiddenCats, setHiddenCats] = useState<Set<string>>(new Set());
  const [sub, setSub] = useState<Subscription>("none");
  const [mtypes, setMtypes] = useState<Set<string>>(new Set());
  const [mtypeResult, setMtypeResult] = useState("");
  const [refreshLabel, setRefreshLabel] = useState("Refresh now");
  const [scanBusy, setScanBusy] = useState(false);

  // Discography table: search / owned filter / row selection / per-type page.
  const [discoSearch, setDiscoSearch] = useState("");
  const [ownedFilter, setOwnedFilter] = useState<"all" | "owned" | "unowned">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pages, setPages] = useState<Record<string, number>>({});

  // Seed editable local state from the fetched artist / settings.
  useEffect(() => {
    if (!artist) return;
    setSub(artist.subscription);
    setMtypes(new Set((artist.monitor_types ?? "album,ep").split(",").filter(Boolean)));
  }, [artist]);
  useEffect(() => {
    if (!settings) return;
    setHiddenCats(new Set((settings.discography_autohide || "").split(",").filter(Boolean)));
  }, [settings]);

  // Derive discography display state from the query result.
  const disco: DiscographyResponse | null = discoData && discoData.mbid ? discoData : null;
  const discoSource = disco ? (disco.error ? "(MusicBrainz error)" : "(from MusicBrainz)") : "";
  const discoMsg = discoError
    ? "Failed to load discography."
    : !discoData
      ? "Loading from MusicBrainz..."
      : "No MusicBrainz match for this artist yet. Use Tools above to match a MusicBrainz URL.";

  // Flatten the grouped discography into typed rows.
  type Row = { it: DiscographyItem; type: string; label: string };
  const tagged: Row[] = disco
    ? CATS.flatMap(([key, label]) => (disco.groups[key] || []).map((it) => ({ it, type: key as string, label })))
    : [];
  // The headline count reflects only the release types you're monitoring.
  const monitored = tagged.filter((t) => mtypes.has(t.type));
  const totalCount = monitored.length;
  const ownedCount = monitored.filter((t) => t.it.owned).length;

  const rowKey = (it: DiscographyItem) => it.mbid || it.title;
  const q = discoSearch.trim().toLowerCase();
  const matches = tagged
    .filter((t) => !q || t.it.title.toLowerCase().includes(q))
    .filter((t) => ownedFilter === "all" || (ownedFilter === "owned" ? !!t.it.owned : !t.it.owned));
  const rowsFor = (type: string) =>
    matches.filter((t) => t.type === type).sort((a, b) => (b.it.release_date || "").localeCompare(a.it.release_date || ""));
  const statFor = (type: string) => {
    const all = tagged.filter((t) => t.type === type);
    return { owned: all.filter((t) => t.it.owned).length, total: all.length };
  };
  // Accordions start open unless the type is auto-hidden in settings.
  const openCats = ["album", "ep", "single"].filter((k) => !hiddenCats.has(k));

  function albumHref(it: DiscographyItem) {
    const qs = new URLSearchParams({ artist: artist?.name || "", title: it.title, from: "artist", artist_id: String(id) });
    if (it.mbid) qs.set("mbid", it.mbid);
    return "/album?" + qs.toString();
  }
  function toggleSelect(k: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(k); else next.delete(k);
      return next;
    });
  }
  function bulkOwned(owned: boolean) {
    tagged.filter((t) => selected.has(rowKey(t.it))).forEach((t) => toggleOwned(t.it, owned));
    setSelected(new Set());
  }

  function renderTable(rows: Row[], type: string) {
    if (!rows.length) return <Text color="fg.muted" fontSize="sm" mt="2">No albums match.</Text>;
    const keys = rows.map((t) => rowKey(t.it));
    const allSel = keys.every((k) => selected.has(k));
    const setAll = (checked: boolean) =>
      setSelected((prev) => {
        const next = new Set(prev);
        keys.forEach((k) => (checked ? next.add(k) : next.delete(k)));
        return next;
      });
    const totalPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    const page = Math.min(pages[type] || 1, totalPages);
    const pageRows = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    return (
      <>
      <Table.Root size="sm" interactive stickyHeader mt="2">
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader w="2.5rem" textAlign="center">
              <BoxCheck checked={allSel} onChange={setAll} label="Select all" />
            </Table.ColumnHeader>
            <Table.ColumnHeader>Title</Table.ColumnHeader>
            <Table.ColumnHeader w="9rem">Released</Table.ColumnHeader>
            <Table.ColumnHeader w="4rem" textAlign="center">Owned</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {pageRows.map(({ it }) => {
            const k = rowKey(it);
            return (
              <Table.Row key={k}>
                <Table.Cell textAlign="center">
                  <BoxCheck checked={selected.has(k)} onChange={(v) => toggleSelect(k, v)} label={`Select ${it.title}`} />
                </Table.Cell>
                <Table.Cell>
                  <HStack gap="2" minW="0">
                    <Box className={!it.image_url ? "vinyl-art" : undefined} boxSize="36px" flex="none" rounded="sm" bg="bg.muted" overflow="hidden">
                      {it.image_url && <Image src={art(it.image_url)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
                    </Box>
                    <CLink as={RouterLink} {...{ to: albumHref(it) }}>{it.title}</CLink>
                  </HStack>
                </Table.Cell>
                <Table.Cell color="fg.muted">{it.release_date ? formatDate(it.release_date) : "TBA"}</Table.Cell>
                <Table.Cell textAlign="center">
                  <BoxCheck
                    checked={!!it.owned}
                    onChange={(v) => toggleOwned(it, v)}
                    label={it.owned ? `Mark ${it.title} not owned` : `Mark ${it.title} owned`}
                  />
                </Table.Cell>
              </Table.Row>
            );
          })}
        </Table.Body>
      </Table.Root>
      {rows.length > PAGE_SIZE && (
        <Pagination.Root
          count={rows.length}
          pageSize={PAGE_SIZE}
          page={page}
          onPageChange={(e) => setPages((prev) => ({ ...prev, [type]: e.page }))}
          mt="2"
        >
          <ButtonGroup variant="ghost" size="sm">
            <Pagination.PrevTrigger asChild>
              <IconButton aria-label="Previous page"><LuChevronLeft /></IconButton>
            </Pagination.PrevTrigger>
            <Pagination.Items
              render={(p) => (
                <IconButton aria-label={`Page ${p.value}`} variant={{ base: "ghost", _selected: "outline" }}>
                  {p.value}
                </IconButton>
              )}
            />
            <Pagination.NextTrigger asChild>
              <IconButton aria-label="Next page"><LuChevronRight /></IconButton>
            </Pagination.NextTrigger>
          </ButtonGroup>
        </Pagination.Root>
      )}
      </>
    );
  }

  function changeSub(state: Subscription) {
    setSub(state);
    api.setSubscription(id, state);
  }

  function toggleOwned(item: DiscographyItem, owned: boolean) {
    // Optimistically flip the flag in the cached discography, then persist.
    qc.setQueryData<DiscographyResponse>(["discography", id], (prev) => {
      if (!prev) return prev;
      const groups = { ...prev.groups };
      (Object.keys(groups) as (keyof typeof groups)[]).forEach((k) => {
        groups[k] = groups[k].map((it) => (it === item ? { ...it, owned } : it));
      });
      return { ...prev, groups };
    });
    api.setAlbumOwned(id, item.title, owned, item.mbid).catch(() =>
      qc.invalidateQueries({ queryKey: ["discography", id] }),
    );
  }

  // Rescan only this artist's folders (fast), then refresh owned flags.
  function rescanOwned() {
    setScanBusy(true);
    api.scanArtist(id)
      .then(() => qc.invalidateQueries({ queryKey: ["artist", id] }))
      .then(() => refetchDisco())
      .finally(() => setScanBusy(false));
  }

  function toggleMtype(value: string, checked: boolean) {
    const next = new Set(mtypes);
    if (checked) next.add(value); else next.delete(value);
    setMtypes(next);
    setMtypeResult("Saving...");
    api
      .setMonitorTypes(id, Array.from(next))
      .then((r: any) => {
        const kept: string[] = r.monitor_types || Array.from(next);
        setMtypes(new Set(kept));
        // Keep the cached artist in sync so navigating back doesn't re-seed the
        // old monitor types (which would revert the headline count).
        qc.setQueryData<ArtistDetailT>(["artist", id], (prev) =>
          prev ? { ...prev, monitor_types: kept.join(",") } : prev,
        );
        setMtypeResult(kept.length ? `Saved (${kept.join(", ")})` : "Saved");
        setTimeout(() => setMtypeResult(""), 2500);
      })
      .catch(() => setMtypeResult("Failed."));
  }

  function refresh() {
    setRefreshLabel("Refreshing...");
    api.refreshArtist(id).then(() => setTimeout(() => setRefreshLabel("Refresh now"), 3000));
  }

  if (!artist) return <Text color="fg.muted">Loading...</Text>;

  const lastfmHref = artist.lastfm_url || "https://www.last.fm/music/" + encodeURIComponent(artist.name);

  return (
    <Box>
      <Text mb="3"><CLink as={RouterLink} {...{ to: "/artists" }}>← All artists</CLink></Text>

      <Flex gap="4" align="flex-start" wrap="wrap">
        {artist.image_url && (
          <Image src={art(artist.image_url)} alt="" boxSize="120px" objectFit="cover" rounded="md" loading="lazy" />
        )}
        <Box>
          <Heading size="xl">{artist.name}</Heading>
          <HStack gap="3" my="1">
            {artist.mbid && (
              <CLink href={`https://musicbrainz.org/artist/${artist.mbid}`} target="_blank" rel="noopener noreferrer">
                <Image src="/static/musicbrainz.svg" alt="MusicBrainz" h="24px" w="24px" />
              </CLink>
            )}
            <CLink href={lastfmHref} target="_blank" rel="noopener noreferrer">
              <Image src="/static/last-fm.svg" alt="Last.fm" h="24px" w="24px" />
            </CLink>
            <CLink href={`https://music.youtube.com/search?q=${encodeURIComponent(artist.name)}`} target="_blank" rel="noopener noreferrer">
              <Image src="/static/youtube-music.svg" alt="YouTube Music" h="24px" w="24px" />
            </CLink>
          </HStack>
          <Text color="fg.muted">
            {artist.track_count} tracks in library
            {artist.last_checked && <> <Text as="span" mx="1">/</Text> checked {artist.last_checked}</>}
          </Text>
          <HStack gap="4" my="2" wrap="wrap">
            <RadioGroup.Root value={sub} onValueChange={(e) => changeSub(e.value as Subscription)}>
              <HStack gap="4" wrap="wrap">
                {([["none", "Not following"], ["subscribed", "Follow"], ["notify", "Follow + Notify"]] as [Subscription, string][]).map(
                  ([value, label]) => (
                    <RadioGroup.Item key={value} value={value}>
                      <RadioGroup.ItemHiddenInput />
                      <RadioGroup.ItemIndicator />
                      <RadioGroup.ItemText>{label}</RadioGroup.ItemText>
                    </RadioGroup.Item>
                  ),
                )}
              </HStack>
            </RadioGroup.Root>
            <Button size="sm" variant="outline" onClick={refresh}>{refreshLabel}</Button>
          </HStack>
          <HStack gap="4" wrap="wrap">
            <Text color="fg.muted">Monitor:</Text>
            {[["album", "Albums"], ["ep", "EPs"], ["single", "Singles"]].map(([value, label]) => (
              <Checkbox.Root key={value} size="sm" checked={mtypes.has(value)} onCheckedChange={(e) => toggleMtype(value, !!e.checked)}>
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label>{label}</Checkbox.Label>
              </Checkbox.Root>
            ))}
            <Text color="fg.muted">{mtypeResult}</Text>
          </HStack>
        </Box>
      </Flex>

      {artist.bio && (
        <Box my="4" color="fg.muted" dangerouslySetInnerHTML={{ __html: artist.bio }} />
      )}

      <MergeTools artistId={id} onMerged={() => window.location.reload()} mbid={artist.mbid} onMatched={() => refetchDisco()} />

      <Flex align="center" gap="3" mt="6" mb="2" wrap="wrap">
        <Heading size="lg">
          Discography <Text as="span" color="fg.muted">{discoSource}</Text>
        </Heading>
        {disco && totalCount > 0 && (
          <Badge colorPalette={ownedCount ? "green" : "gray"} variant="surface">
            {ownedCount}/{totalCount} owned
          </Badge>
        )}
        {disco && (
          <Button size="xs" variant="outline" ml="auto" loading={scanBusy} onClick={rescanOwned}>
            Rescan owned
          </Button>
        )}
      </Flex>
      {!disco ? (
        <Text color="fg.muted">{discoMsg}</Text>
      ) : (
        <>
          <Flex gap="3" align="center" wrap="wrap" mb="3">
            <Input
              flex="1 1 14rem"
              size="sm"
              type="search"
              placeholder="Filter albums..."
              value={discoSearch}
              onChange={(e) => setDiscoSearch(e.target.value)}
            />
            <HStack gap="1">
              {(["all", "owned", "unowned"] as const).map((f) => (
                <Button
                  key={f}
                  size="xs"
                  variant={ownedFilter === f ? "surface" : "ghost"}
                  colorPalette="gray"
                  onClick={() => setOwnedFilter(f)}
                >
                  {f === "all" ? "All" : f === "owned" ? "Owned" : "Not owned"}
                </Button>
              ))}
            </HStack>
          </Flex>

          <ActionBar.Root open={selected.size > 0} onOpenChange={(e) => { if (!e.open) setSelected(new Set()); }}>
            <Portal>
              <ActionBar.Positioner>
                <ActionBar.Content>
                  <ActionBar.SelectionTrigger>{selected.size} selected</ActionBar.SelectionTrigger>
                  <ActionBar.Separator />
                  <Button size="sm" variant="surface" colorPalette="green" onClick={() => bulkOwned(true)}>Mark owned</Button>
                  <Button size="sm" variant="surface" colorPalette="gray" onClick={() => bulkOwned(false)}>Mark not owned</Button>
                  <ActionBar.CloseTrigger asChild>
                    <CloseButton size="sm" aria-label="Clear selection" />
                  </ActionBar.CloseTrigger>
                </ActionBar.Content>
              </ActionBar.Positioner>
            </Portal>
          </ActionBar.Root>

          <Accordion.Root multiple collapsible defaultValue={openCats}>
            {([["album", "Albums"], ["ep", "EPs"], ["single", "Singles"]] as const).map(([key, label]) => {
              const st = statFor(key);
              return (
                <Accordion.Item key={key} value={key}>
                  <Accordion.ItemTrigger>
                    <Heading as="h3" size="sm" flex="1" textAlign="start" fontWeight="semibold">
                      {label} <Text as="span" color="fg.muted" fontWeight="normal">({st.total})</Text>
                    </Heading>
                    {st.total > 0 && (
                      <Badge colorPalette={st.owned ? "green" : "gray"} variant="surface" mr="2">
                        {st.owned}/{st.total} owned
                      </Badge>
                    )}
                    <Accordion.ItemIndicator />
                  </Accordion.ItemTrigger>
                  <Accordion.ItemContent>
                    <Accordion.ItemBody>{renderTable(rowsFor(key), key)}</Accordion.ItemBody>
                  </Accordion.ItemContent>
                </Accordion.Item>
              );
            })}
          </Accordion.Root>
        </>
      )}
    </Box>
  );
}

function MergeTools({
  artistId,
  mbid,
  onMatched,
  onMerged,
}: {
  artistId: number;
  mbid: string | null;
  onMatched: () => void;
  onMerged: () => void;
}) {
  const [matchLink, setMatchLink] = useState(mbid ? `https://musicbrainz.org/artist/${mbid}` : "");
  const [matchResult, setMatchResult] = useState("");
  const [mergeSearch, setMergeSearch] = useState("");
  const [matches, setMatches] = useState<Artist[]>([]);
  const [compare, setCompare] = useState<{ target: ArtistDetailT; source: ArtistDetailT } | null>(null);
  const [keepName, setKeepName] = useState<"target" | "source">("target");
  const [mergeResult, setMergeResult] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout>>();

  function doMatch() {
    const link = matchLink.trim();
    if (!link) return;
    setMatchResult("Matching...");
    api.setMbid(artistId, link).then((r: any) => {
      if (r.error) { setMatchResult(r.error); return; }
      setMatchResult("Matched: " + r.matched_name);
      onMatched();
    }).catch(() => setMatchResult("Failed."));
  }

  function onSearch(q: string) {
    setMergeSearch(q);
    setCompare(null);
    clearTimeout(timer.current);
    if (q.trim().length < 2) { setMatches([]); return; }
    timer.current = setTimeout(() => {
      api.artists({ ignored: "all", q: q.trim() }).then((d) => setMatches(d.artists.filter((a) => a.id !== artistId).slice(0, 20)));
    }, 250);
  }

  function showCompare(sourceId: number) {
    Promise.all([api.artist(artistId), api.artist(sourceId)]).then(([target, source]) => {
      setCompare({ target, source });
      setKeepName("target");
    });
  }

  function confirmMerge() {
    if (!compare) return;
    const name = keepName === "source" ? compare.source.name : compare.target.name;
    setMergeResult("Merging...");
    api.merge(artistId, [compare.source.id], name).then((r: any) => {
      if (r.error) { setMergeResult(r.error); return; }
      setMergeResult(`Merged into "${r.name}". Reloading...`);
      setTimeout(onMerged, 800);
    }).catch(() => setMergeResult("Failed."));
  }

  return (
    <Box as="details" borderWidth="1px" rounded="md" px="3" py="2" mt="4">
      <Box as="summary" cursor="pointer" fontWeight="semibold">Tools: match / merge</Box>

      <Box mt="3">
        <Text fontWeight="semibold" mb="1">Match to a MusicBrainz artist URL or ID</Text>
        <HStack gap="2" wrap="wrap">
          <Input flex="1 1 18rem" value={matchLink} onChange={(e) => setMatchLink(e.target.value)} placeholder="https://musicbrainz.org/artist/..." />
          <Button variant="outline" onClick={doMatch}>Match</Button>
          <Text color="fg.muted">{matchResult}</Text>
        </HStack>
        <Text color="fg.muted" fontSize="sm" mt="1">Sets the MusicBrainz id used to fetch this artist's releases.</Text>
      </Box>

      <Box mt="4">
        <Text fontWeight="semibold" mb="1">Merge another artist into this one</Text>
        <HStack gap="2" wrap="wrap">
          <Input flex="1 1 18rem" type="search" value={mergeSearch} onChange={(e) => onSearch(e.target.value)} placeholder="Search your library..." />
          <Text color="fg.muted">{mergeResult}</Text>
        </HStack>
        {matches.length > 0 && (
          <Stack gap="1" mt="2">
            {matches.map((a) => (
              <Button key={a.id} variant="ghost" justifyContent="flex-start" size="sm" onClick={() => showCompare(a.id)}>
                {a.name} <Text as="span" color="fg.muted" ml="1">({a.track_count || 0} tracks)</Text>
              </Button>
            ))}
          </Stack>
        )}
        {compare && (
          <Box mt="3">
            <Grid templateColumns={{ base: "1fr", sm: "1fr 1fr" }} gap="3">
              <CompareCard a={compare.target} role="Keep (this artist)" />
              <CompareCard a={compare.source} role="Merge in & remove" />
            </Grid>
            <RadioGroup.Root value={keepName} onValueChange={(e) => setKeepName(e.value as "target" | "source")} my="3">
              <HStack gap="4" wrap="wrap">
                <Text fontWeight="semibold">Keep name:</Text>
                <RadioGroup.Item value="target">
                  <RadioGroup.ItemHiddenInput />
                  <RadioGroup.ItemIndicator />
                  <RadioGroup.ItemText>{compare.target.name}</RadioGroup.ItemText>
                </RadioGroup.Item>
                {compare.source.name.toLowerCase() !== compare.target.name.toLowerCase() && (
                  <RadioGroup.Item value="source">
                    <RadioGroup.ItemHiddenInput />
                    <RadioGroup.ItemIndicator />
                    <RadioGroup.ItemText>{compare.source.name}</RadioGroup.ItemText>
                  </RadioGroup.Item>
                )}
              </HStack>
            </RadioGroup.Root>
            <HStack gap="2">
              <Button onClick={confirmMerge}>Merge these</Button>
              <Button variant="outline" onClick={() => setCompare(null)}>Cancel</Button>
            </HStack>
          </Box>
        )}
        <Text color="fg.muted" fontSize="sm" mt="2">The other artist's tracks and releases move here; it is then removed.</Text>
      </Box>
    </Box>
  );
}

function CompareCard({ a, role }: { a: ArtistDetailT; role: string }) {
  return (
    <Box borderWidth="1px" rounded="md" p="2.5">
      <Text fontSize="xs" textTransform="uppercase" color="fg.muted">{role}</Text>
      {a.image_url && <Image src={art(a.image_url)} alt="" boxSize="72px" objectFit="cover" rounded="md" my="1" />}
      <Text fontWeight="bold">{a.name}</Text>
      <Text color="fg.muted">{a.track_count || 0} tracks · {a.releases ? a.releases.length : 0} releases</Text>
      <Text color="fg.muted">MusicBrainz: {a.mbid ? "matched" : "none"}</Text>
      <Text color="fg.muted">Status: {a.subscription || "none"}</Text>
    </Box>
  );
}
