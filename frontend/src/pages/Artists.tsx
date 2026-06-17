import {
  ActionBar,
  Box,
  Button,
  ButtonGroup,
  CloseButton,
  HStack,
  Heading,
  IconButton,
  Input,
  NativeSelect,
  Pagination,
  Portal,
  Select,
  Spacer,
  Table,
  Text,
  Wrap,
  createListCollection,
} from "@chakra-ui/react";
import { useEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { LuChevronLeft, LuChevronRight } from "react-icons/lu";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { BoxCheck } from "../components/BoxCheck";
import type { Artist, Stats, Subscription } from "../types";

const PAGE_SIZES = createListCollection({
  items: [
    { label: "25 / page", value: "25" },
    { label: "50 / page", value: "50" },
    { label: "100 / page", value: "100" },
    { label: "200 / page", value: "200" },
    { label: "All", value: "all" },
  ],
});

export default function Artists() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("name");
  const [progress, setProgress] = useState<string | null>(null);
  const [pageSize, setPageSize] = useState("50");
  const [page, setPage] = useState(1);

  // Jump back to page 1 whenever the result set changes.
  useEffect(() => { setPage(1); }, [debouncedSearch, filter, sort]);

  // Add-by-link state.
  const [mbLink, setMbLink] = useState("");
  const [mbState, setMbState] = useState<Subscription>("subscribed");
  const [mbResult, setMbResult] = useState("");
  const [mbBusy, setMbBusy] = useState(false);

  // Debounce the search box so each keystroke doesn't refetch.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 200);
    return () => clearTimeout(t);
  }, [search]);

  const artistsKey = ["artists", { sort, q: debouncedSearch.trim(), filter }] as const;
  const { data: artists = [], isPending: loading } = useQuery({
    queryKey: artistsKey,
    queryFn: () => {
      const params: Record<string, string> = { sort };
      if (debouncedSearch.trim()) params.q = debouncedSearch.trim();
      if (filter) params.subscription = filter;
      return api.artists(params).then((d) => d.artists);
    },
    // Keep the previous list visible while a new query loads — no blank flash
    // when changing search/sort/filter.
    placeholderData: keepPreviousData,
  });
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: () => api.stats() });

  const reloadArtists = () => qc.invalidateQueries({ queryKey: ["artists"] });
  const reloadStats = () => qc.invalidateQueries({ queryKey: ["stats"] });

  useEffect(() => {
    // Resume a running scan/refresh on mount.
    api.scanStatus().then((s) => { if (s.running) pollScan(); }).catch(() => {});
    api.refreshStatus().then((s) => { if (s.running) pollRefresh(); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function setSubscription(id: number, state: Subscription) {
    await api.setSubscription(id, state);
    qc.setQueryData<Artist[]>(artistsKey, (prev) =>
      prev?.map((a) => (a.id === id ? { ...a, subscription: state } : a)),
    );
    reloadStats();
  }

  function toggleSub(a: Artist, checked: boolean) {
    const notify = a.subscription === "notify";
    const state: Subscription = checked ? (notify ? "notify" : "subscribed") : "none";
    setSubscription(a.id, state);
  }

  function toggleNotify(a: Artist, checked: boolean) {
    const subbed = a.subscription === "subscribed" || a.subscription === "notify";
    const state: Subscription = checked ? "notify" : subbed ? "subscribed" : "none";
    setSubscription(a.id, state);
  }

  function ignoreOne(id: number) {
    api.setIgnore(id, true).then(() => {
      qc.setQueryData<Artist[]>(artistsKey, (prev) => prev?.filter((x) => x.id !== id));
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      reloadStats();
    });
  }

  function toggleSelect(id: number, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id); else next.delete(id);
      return next;
    });
  }

  const allChecked = artists.length > 0 && artists.every((a) => selected.has(a.id));
  function toggleSelectAll(checked: boolean) {
    setSelected(checked ? new Set(artists.map((a) => a.id)) : new Set());
  }

  function bulkSubscription(state: Subscription) {
    api.bulkSubscription(Array.from(selected), state).then(() => {
      setSelected(new Set());
      reloadArtists();
      reloadStats();
    });
  }
  function bulkIgnore() {
    api.bulkIgnore(Array.from(selected), true).then(() => {
      setSelected(new Set());
      reloadArtists();
      reloadStats();
    });
  }

  // --- scan / refresh polling ---
  const scanTick = useRef(0);
  function pollScan() {
    api.scanStatus().then((s: any) => {
      if (s.running) {
        setProgress(
          (s.mode === "quick" ? "Quick scan" : "Full scan") +
            `: ${s.files_seen} files seen, ${s.artists_found} artists. ${s.message || ""}`,
        );
        scanTick.current += 1;
        if (scanTick.current % 3 === 0) { reloadArtists(); reloadStats(); }
        setTimeout(pollScan, 1000);
      } else {
        setProgress("Scan finished: " + (s.message || ""));
        scanTick.current = 0;
        reloadArtists();
        reloadStats();
        setTimeout(() => setProgress(null), 4000);
      }
    });
  }
  function pollRefresh() {
    api.refreshStatus().then((s: any) => {
      if (s.running) {
        const now = s.current
          ? ` (on: ${s.current}${s.elapsed ? " " + s.elapsed + "s" : ""})`
          : s.message ? ` (last: ${s.message})` : "";
        setProgress(`Refreshing following: ${s.queued || 0} queued, ${s.processed || 0} done${now}`);
        setTimeout(pollRefresh, 2000);
      } else {
        setProgress("Refresh finished.");
        reloadStats();
        setTimeout(() => setProgress(null), 4000);
      }
    });
  }

  function startScan(quick: boolean) {
    api.scan(quick).then((r: any) => {
      setProgress(r.error ? r.error : `${quick ? "Quick" : "Full"} scan started...`);
      if (!r.error) setTimeout(pollScan, 800);
    });
  }
  function startRefresh() {
    api.refreshAll().then((r: any) => {
      setProgress(r.error ? r.error : "Refresh started...");
      if (!r.error) setTimeout(pollRefresh, 800);
    });
  }

  function addByLink() {
    const link = mbLink.trim();
    if (!link) return;
    setMbBusy(true);
    setMbResult("Looking up...");
    api
      .addByLink(link, mbState)
      .then((r) => {
        if (r.error) {
          setMbResult(r.error);
        } else {
          setMbResult(`${r.created ? "Added " : "Now following "}${r.name}. Fetching releases...`);
          setMbLink("");
          reloadArtists();
          reloadStats();
        }
      })
      .catch(() => setMbResult("Failed to add artist."))
      .finally(() => setMbBusy(false));
  }

  const stat = (k: keyof Stats) => (stats ? stats[k] : "-");
  const selCount = selected.size;

  // Client-side pagination over the loaded artists.
  const perPage = pageSize === "all" ? artists.length || 1 : Number(pageSize);
  const totalPages = Math.max(1, Math.ceil(artists.length / perPage));
  const curPage = Math.min(page, totalPages);
  const pagedArtists =
    pageSize === "all" ? artists : artists.slice((curPage - 1) * perPage, curPage * perPage);
  const sortedNote = artists.length
    ? `Showing ${pagedArtists.length} of ${artists.length} artists`
    : "";

  return (
    <Box>
      <Heading size="xl" mb="2">Artists</Heading>

      <Text color="fg.muted" mb="3">
        {stat("visible")} artists <Sep /> {stat("following")} following <Sep />{" "}
        {stat("upcoming_month")} upcoming this month <Sep />{" "}
        <RouterLink to="/ignored">{stat("ignored")} ignored</RouterLink>
      </Text>

      <Wrap gap="2" mb="3">
        <Input
          flex="1 1 14rem"
          type="search"
          placeholder="Filter artists..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <NativeSelect.Root width="auto">
          <NativeSelect.Field value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All</option>
            <option value="following">Following</option>
            <option value="subscribed">Follow only</option>
            <option value="notify">Notify</option>
            <option value="none">Not followed</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
        <NativeSelect.Root width="auto">
          <NativeSelect.Field value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="name">Sort: Name</option>
            <option value="tracks">Sort: Track count</option>
            <option value="recent">Sort: Last checked</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
        <Button variant="outline" onClick={() => startScan(false)}>Full scan</Button>
        <Button variant="outline" onClick={() => startScan(true)}>Quick scan</Button>
        <Button variant="outline" onClick={startRefresh}>Refresh following</Button>
      </Wrap>

      <Wrap gap="2" mb="3" align="center">
        <Input
          flex="1 1 18rem"
          placeholder="Monitor an artist by MusicBrainz link or ID..."
          value={mbLink}
          onChange={(e) => setMbLink(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addByLink(); } }}
        />
        <NativeSelect.Root width="auto">
          <NativeSelect.Field value={mbState} onChange={(e) => setMbState(e.target.value as Subscription)}>
            <option value="subscribed">Follow</option>
            <option value="notify">Follow + Notify</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
        <Button onClick={addByLink} disabled={mbBusy}>Add</Button>
        <Text color="fg.muted">{mbResult}</Text>
      </Wrap>

      {progress && (
        <Box borderWidth="1px" rounded="md" px="3" py="2" mb="3" fontSize="sm">
          {progress}
        </Box>
      )}

      <ActionBar.Root open={selCount > 0} onOpenChange={(e) => { if (!e.open) setSelected(new Set()); }}>
        <Portal>
          <ActionBar.Positioner>
            <ActionBar.Content>
              <ActionBar.SelectionTrigger>{selCount} selected</ActionBar.SelectionTrigger>
              <ActionBar.Separator />
              <Button size="sm" variant="outline" onClick={() => bulkSubscription("subscribed")}>Follow</Button>
              <Button size="sm" variant="outline" onClick={() => bulkSubscription("notify")}>Follow + Notify</Button>
              <Button size="sm" variant="outline" onClick={() => bulkSubscription("none")}>Unfollow</Button>
              <Button size="sm" variant="outline" onClick={bulkIgnore}>Ignore</Button>
              <ActionBar.CloseTrigger asChild>
                <CloseButton size="sm" aria-label="Clear selection" />
              </ActionBar.CloseTrigger>
            </ActionBar.Content>
          </ActionBar.Positioner>
        </Portal>
      </ActionBar.Root>

      <Table.Root size="sm" interactive>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader width="3rem" textAlign="center">
              <BoxCheck checked={allChecked} onChange={toggleSelectAll} label="Select all" />
            </Table.ColumnHeader>
            <Table.ColumnHeader>Artist</Table.ColumnHeader>
            <Table.ColumnHeader textAlign="end" width="5rem">Tracks</Table.ColumnHeader>
            <Table.ColumnHeader width="3rem" textAlign="center" title="Show on Following page">Follow</Table.ColumnHeader>
            <Table.ColumnHeader width="3rem" textAlign="center" title="Follow + webhook on new release">Notify</Table.ColumnHeader>
            <Table.ColumnHeader width="4.5rem" textAlign="center">Ignore</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {loading ? (
            <Table.Row><Table.Cell colSpan={6} color="fg.muted">Loading...</Table.Cell></Table.Row>
          ) : artists.length === 0 ? (
            <Table.Row><Table.Cell colSpan={6} color="fg.muted">No artists. Run a library scan from the toolbar.</Table.Cell></Table.Row>
          ) : (
            pagedArtists.map((a) => {
              const subChecked = a.subscription === "subscribed" || a.subscription === "notify";
              const notifyChecked = a.subscription === "notify";
              return (
                <Table.Row key={a.id}>
                  <Table.Cell textAlign="center">
                    <BoxCheck checked={selected.has(a.id)} onChange={(v) => toggleSelect(a.id, v)} label={`Select ${a.name}`} />
                  </Table.Cell>
                  <Table.Cell>
                    <RouterLink to={`/artist/${a.id}`}>{a.name}</RouterLink>
                  </Table.Cell>
                  <Table.Cell textAlign="end" color="fg.muted">{a.track_count || 0}</Table.Cell>
                  <Table.Cell textAlign="center">
                    <BoxCheck checked={subChecked} onChange={(v) => toggleSub(a, v)} label={`Follow ${a.name}`} />
                  </Table.Cell>
                  <Table.Cell textAlign="center">
                    <BoxCheck checked={notifyChecked} onChange={(v) => toggleNotify(a, v)} label={`Notify for ${a.name}`} />
                  </Table.Cell>
                  <Table.Cell textAlign="center">
                    <Button size="xs" variant="ghost" color="fg.muted" onClick={() => ignoreOne(a.id)} title="Hide this artist">
                      Ignore
                    </Button>
                  </Table.Cell>
                </Table.Row>
              );
            })
          )}
        </Table.Body>
      </Table.Root>

      <HStack mt="3" gap="3" wrap="wrap">
        <Text color="fg.muted">{sortedNote}</Text>
        <Select.Root
          collection={PAGE_SIZES}
          value={[pageSize]}
          onValueChange={(e) => { setPageSize(e.value[0]); setPage(1); }}
          size="sm"
          width="9rem"
        >
          <Select.HiddenSelect />
          <Select.Control>
            <Select.Trigger>
              <Select.ValueText />
            </Select.Trigger>
            <Select.IndicatorGroup>
              <Select.Indicator />
            </Select.IndicatorGroup>
          </Select.Control>
          <Portal>
            <Select.Positioner>
              <Select.Content>
                {PAGE_SIZES.items.map((item) => (
                  <Select.Item item={item} key={item.value}>
                    {item.label}
                    <Select.ItemIndicator />
                  </Select.Item>
                ))}
              </Select.Content>
            </Select.Positioner>
          </Portal>
        </Select.Root>
        <Spacer />
        {pageSize !== "all" && totalPages > 1 && (
          <Pagination.Root count={artists.length} pageSize={perPage} page={curPage} onPageChange={(e) => setPage(e.page)}>
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
      </HStack>
    </Box>
  );
}

function Sep() {
  return <Text as="span" color="fg.muted" mx="1.5">/</Text>;
}
