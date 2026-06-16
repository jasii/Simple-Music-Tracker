import {
  Box,
  Button,
  Flex,
  Grid,
  HStack,
  Heading,
  Image,
  Input,
  Link as CLink,
  Stack,
  Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link as RouterLink, useParams } from "react-router-dom";
import { api, art } from "../api";
import type { Artist, ArtistDetail as ArtistDetailT, DiscographyResponse, Release, Subscription } from "../types";
import { formatDate } from "../lib/format";

const CATS: [keyof DiscographyResponse["groups"], string][] = [
  ["album", "Albums"],
  ["ep", "EPs"],
  ["single", "Singles"],
];

export default function ArtistDetail() {
  const { id: idParam } = useParams();
  const id = Number(idParam);
  const [artist, setArtist] = useState<ArtistDetailT | null>(null);
  const [disco, setDisco] = useState<DiscographyResponse | null>(null);
  const [discoMsg, setDiscoMsg] = useState("Loading from MusicBrainz...");
  const [discoSource, setDiscoSource] = useState("");
  const [hiddenCats, setHiddenCats] = useState<Set<string>>(new Set());

  const [sub, setSub] = useState<Subscription>("none");
  const [mtypes, setMtypes] = useState<Set<string>>(new Set());
  const [mtypeResult, setMtypeResult] = useState("");
  const [refreshLabel, setRefreshLabel] = useState("Refresh now");

  const loadDiscography = useCallback(() => {
    setDiscoMsg("Loading from MusicBrainz...");
    api
      .discography(id)
      .then((data) => {
        if (!data.mbid) {
          setDisco(null);
          setDiscoMsg("No MusicBrainz match for this artist yet. Use Tools above to match a MusicBrainz URL.");
          return;
        }
        setDiscoSource(data.error ? "(MusicBrainz error)" : "(from MusicBrainz)");
        setDisco(data);
      })
      .catch(() => {
        setDisco(null);
        setDiscoMsg("Failed to load discography.");
      });
  }, [id]);

  useEffect(() => {
    api.artist(id).then((a) => {
      setArtist(a);
      setSub(a.subscription);
      setMtypes(new Set((a.monitor_types || "album,ep").split(",").filter(Boolean)));
    });
    api.settings().then((s) => {
      const auto = (s.discography_autohide || "").split(",").filter(Boolean);
      setHiddenCats(new Set(auto));
    });
    loadDiscography();
  }, [id, loadDiscography]);

  function changeSub(state: Subscription) {
    setSub(state);
    api.setSubscription(id, state);
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
        setMtypeResult(`Saved (${kept.join(", ")})`);
        setTimeout(() => setMtypeResult(""), 2500);
      })
      .catch(() => setMtypeResult("Failed."));
  }

  function refresh() {
    setRefreshLabel("Refreshing...");
    api.refreshArtist(id).then(() => setTimeout(() => setRefreshLabel("Refresh now"), 3000));
  }

  function toggleCat(key: string) {
    setHiddenCats((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
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
            {([["none", "Not following"], ["subscribed", "Follow"], ["notify", "Follow + Notify"]] as [Subscription, string][]).map(
              ([value, label]) => (
                <label key={value} style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                  <input type="radio" name="sub" checked={sub === value} onChange={() => changeSub(value)} /> {label}
                </label>
              ),
            )}
            <Button size="sm" variant="outline" onClick={refresh}>{refreshLabel}</Button>
          </HStack>
          <HStack gap="4" wrap="wrap">
            <Text color="fg.muted">Monitor:</Text>
            {[["album", "Albums"], ["ep", "EPs"], ["single", "Singles"]].map(([value, label]) => (
              <label key={value} style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                <input type="checkbox" checked={mtypes.has(value)} onChange={(e) => toggleMtype(value, e.target.checked)} /> {label}
              </label>
            ))}
            <Text color="fg.muted">{mtypeResult}</Text>
          </HStack>
        </Box>
      </Flex>

      {artist.bio && (
        <Box my="4" color="fg.muted" dangerouslySetInnerHTML={{ __html: artist.bio }} />
      )}

      <MergeTools artistId={id} onMerged={() => window.location.reload()} mbid={artist.mbid} onMatched={loadDiscography} />

      <Heading size="lg" mt="6" mb="2">
        Discography <Text as="span" color="fg.muted">{discoSource}</Text>
      </Heading>
      {!disco ? (
        <Text color="fg.muted">{discoMsg}</Text>
      ) : (
        <Stack gap="4">
          {CATS.map(([key, label]) => {
            const items = disco.groups[key] || [];
            const hidden = hiddenCats.has(key);
            return (
              <Box as="section" key={key}>
                <Flex align="center" gap="2" borderBottomWidth="1px" pb="1">
                  <Heading as="h3" size="md">{label} <Text as="span" color="fg.muted">({items.length})</Text></Heading>
                  <Button size="xs" variant="outline" ml="auto" onClick={() => toggleCat(key)}>{hidden ? "Show" : "Hide"}</Button>
                </Flex>
                {!hidden &&
                  (items.length ? (
                    <Stack gap="1" mt="2">
                      {items.map((r, i) => (
                        <DiscoRow key={i} r={r as unknown as Release} artistName={artist.name} artistId={id} />
                      ))}
                    </Stack>
                  ) : (
                    <Text color="fg.muted" mt="2">None.</Text>
                  ))}
              </Box>
            );
          })}
        </Stack>
      )}
    </Box>
  );
}

function DiscoRow({ r, artistName, artistId }: { r: Release; artistName: string; artistId: number }) {
  const qs = new URLSearchParams({ artist: artistName, title: r.title, from: "artist", artist_id: String(artistId) });
  if (r.mbid) qs.set("mbid", r.mbid);
  return (
    <Flex gap="3" py="2" align="center">
      <Box className={!r.image_url ? "vinyl-art" : undefined} boxSize="150px" flex="none" rounded="md" bg="bg.muted" overflow="hidden">
        {r.image_url && <Image src={art(r.image_url)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
      </Box>
      <Box flex="1" minW="0">
        <Text fontWeight="semibold"><CLink as={RouterLink} {...{ to: "/album?" + qs.toString() }}>{r.title}</CLink></Text>
        <Text color="fg.muted" fontSize="sm">{r.release_date ? formatDate(r.release_date) : "date TBA"}</Text>
      </Box>
    </Flex>
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
            <HStack gap="4" my="3" wrap="wrap">
              <Text fontWeight="semibold">Keep name:</Text>
              <label style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                <input type="radio" name="merge-name" checked={keepName === "target"} onChange={() => setKeepName("target")} /> {compare.target.name}
              </label>
              {compare.source.name.toLowerCase() !== compare.target.name.toLowerCase() && (
                <label style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                  <input type="radio" name="merge-name" checked={keepName === "source"} onChange={() => setKeepName("source")} /> {compare.source.name}
                </label>
              )}
            </HStack>
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
