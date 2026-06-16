import {
  Box,
  Button,
  Flex,
  HStack,
  Heading,
  Input,
  NativeSelect,
  Spacer,
  Table,
  Text,
  Wrap,
} from "@chakra-ui/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import type { Artist, Stats, Subscription } from "../types";
import { toaster } from "../components/ui/toaster";

export default function Artists() {
  const [artists, setArtists] = useState<Artist[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState("name");
  const [progress, setProgress] = useState<string | null>(null);

  // Add-by-link state.
  const [mbLink, setMbLink] = useState("");
  const [mbState, setMbState] = useState<Subscription>("subscribed");
  const [mbResult, setMbResult] = useState("");
  const [mbBusy, setMbBusy] = useState(false);

  const searchTimer = useRef<ReturnType<typeof setTimeout>>();

  const loadStats = useCallback(() => {
    api.stats().then(setStats).catch(() => {});
  }, []);

  const load = useCallback(
    async (opts: { silent?: boolean } = {}) => {
      const params: Record<string, string> = { sort };
      if (search.trim()) params.q = search.trim();
      if (filter) params.subscription = filter;
      if (!opts.silent) setLoading(true);
      const scrollY = window.scrollY;
      try {
        const data = await api.artists(params);
        setArtists(data.artists);
        if (opts.silent) window.scrollTo(0, scrollY);
      } catch {
        if (!opts.silent) toaster.create({ title: "Failed to load artists.", type: "error" });
      } finally {
        setLoading(false);
      }
    },
    [search, filter, sort],
  );

  // Debounced reload on search/filter/sort changes.
  useEffect(() => {
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => load(), 200);
    return () => clearTimeout(searchTimer.current);
  }, [load]);

  useEffect(() => {
    loadStats();
    // Resume a running scan/refresh on mount.
    api.scanStatus().then((s) => { if (s.running) pollScan(); }).catch(() => {});
    api.refreshStatus().then((s) => { if (s.running) pollRefresh(); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function setSubscription(id: number, state: Subscription) {
    await api.setSubscription(id, state);
    setArtists((prev) => prev.map((a) => (a.id === id ? { ...a, subscription: state } : a)));
    loadStats();
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
      setArtists((prev) => prev.filter((x) => x.id !== id));
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      loadStats();
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
      load().then(loadStats);
    });
  }
  function bulkIgnore() {
    api.bulkIgnore(Array.from(selected), true).then(() => {
      setSelected(new Set());
      load().then(loadStats);
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
        if (scanTick.current % 3 === 0) { load({ silent: true }); loadStats(); }
        setTimeout(pollScan, 1000);
      } else {
        setProgress("Scan finished: " + (s.message || ""));
        scanTick.current = 0;
        load().then(loadStats);
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
        loadStats();
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
          load().then(loadStats);
        }
      })
      .catch(() => setMbResult("Failed to add artist."))
      .finally(() => setMbBusy(false));
  }

  const stat = (k: keyof Stats) => (stats ? stats[k] : "-");
  const selCount = selected.size;
  const sortedNote = useMemo(() => (artists.length ? `Showing ${artists.length} artists` : ""), [artists]);

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

      {selCount > 0 && (
        <Flex
          borderWidth="1px"
          borderColor="green.solid"
          rounded="md"
          p="2"
          mb="3"
          gap="2"
          align="center"
          wrap="wrap"
        >
          <Text>{selCount} selected</Text>
          <Button size="sm" variant="outline" onClick={() => bulkSubscription("subscribed")}>Follow</Button>
          <Button size="sm" variant="outline" onClick={() => bulkSubscription("notify")}>Follow + Notify</Button>
          <Button size="sm" variant="outline" onClick={() => bulkSubscription("none")}>Unfollow</Button>
          <Button size="sm" variant="outline" onClick={bulkIgnore}>Ignore</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Clear selection</Button>
        </Flex>
      )}

      <Table.Root size="sm" interactive>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader width="3rem" textAlign="center">
              <input
                type="checkbox"
                aria-label="Select all"
                checked={allChecked}
                onChange={(e) => toggleSelectAll(e.target.checked)}
              />
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
            artists.map((a) => {
              const subChecked = a.subscription === "subscribed" || a.subscription === "notify";
              const notifyChecked = a.subscription === "notify";
              return (
                <Table.Row key={a.id}>
                  <Table.Cell textAlign="center">
                    <input
                      type="checkbox"
                      aria-label={`Select ${a.name}`}
                      checked={selected.has(a.id)}
                      onChange={(e) => toggleSelect(a.id, e.target.checked)}
                    />
                  </Table.Cell>
                  <Table.Cell>
                    <RouterLink to={`/artist/${a.id}`}>{a.name}</RouterLink>
                  </Table.Cell>
                  <Table.Cell textAlign="end" color="fg.muted">{a.track_count || 0}</Table.Cell>
                  <Table.Cell textAlign="center">
                    <input type="checkbox" aria-label="Follow" checked={subChecked} onChange={(e) => toggleSub(a, e.target.checked)} />
                  </Table.Cell>
                  <Table.Cell textAlign="center">
                    <input type="checkbox" aria-label="Notify" checked={notifyChecked} onChange={(e) => toggleNotify(a, e.target.checked)} />
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

      <HStack mt="2">
        <Text color="fg.muted">{sortedNote}</Text>
        <Spacer />
      </HStack>
    </Box>
  );
}

function Sep() {
  return <Text as="span" color="fg.muted" mx="1.5">/</Text>;
}
