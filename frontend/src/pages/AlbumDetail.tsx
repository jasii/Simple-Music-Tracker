import { Badge, Box, Button, Flex, HStack, Heading, IconButton, Image, Link as CLink, Stack, Text } from "@chakra-ui/react";
import { useEffect, useState } from "react";
import { Link as RouterLink, useSearchParams } from "react-router-dom";
import { LuX } from "react-icons/lu";
import { api, art } from "../api";
import type { AlbumDetailResponse, AlbumTrack } from "../types";
import { ReleaseIcons } from "../lib/format";

function fmtDuration(sec?: number | null): string {
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = String(sec % 60).padStart(2, "0");
  return `${m}:${s}`;
}

export default function AlbumDetail() {
  const [params] = useSearchParams();
  const artist = (params.get("artist") || "").trim();
  const title = (params.get("title") || "").trim();
  const mbid = (params.get("mbid") || "").trim();
  const origin = params.get("from");
  const artistIdParam = params.get("artist_id");

  const [data, setData] = useState<AlbumDetailResponse | null>(null);
  const [following, setFollowing] = useState(false);
  const [msg, setMsg] = useState("Loading...");

  useEffect(() => {
    api
      .album(artist, title, mbid || undefined)
      .then((d) => {
        setData(d);
        setFollowing(!!d.following);
        if (!d.tracks || !d.tracks.length) setMsg("Tracklist not available yet.");
      })
      .catch(() => setMsg("Failed to load tracklist."));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artist, title, mbid]);

  function toggleFollow(state: "subscribed" | "none") {
    api.trackByName(artist, state).then((r: any) => {
      if (!r.error) setFollowing(state !== "none");
    });
  }

  // Back link mirrors the old album_page logic.
  let back = { to: "/upcoming", label: "Upcoming" };
  if (origin === "discover") back = { to: "/discover", label: "Discover" };
  else if (origin === "artist" && artistIdParam && /^\d+$/.test(artistIdParam))
    back = { to: `/artist/${artistIdParam}`, label: artist };

  const tracks: AlbumTrack[] = data?.tracks || [];

  return (
    <Box>
      <Text mb="3"><CLink as={RouterLink} {...{ to: back.to }}>← {back.label}</CLink></Text>

      <Flex gap="4" align="flex-start" wrap="wrap">
        <Box className={!data?.image ? "vinyl-art" : undefined} boxSize="175px" flex="none" rounded="md" bg="bg.muted" overflow="hidden">
          {data?.image && <Image src={art(data.image)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
        </Box>
        <Box>
          <Heading size="xl">{title}</Heading>
          <HStack gap="2" color="fg.muted">
            {data?.artist_id ? (
              <CLink as={RouterLink} {...{ to: `/artist/${data.artist_id}` }}>{artist}</CLink>
            ) : (
              <Text as="span">{artist}</Text>
            )}
            {following ? (
              <Badge colorPalette="green" variant="outline">
                following
                <IconButton aria-label="Unfollow" title="Unfollow" size="2xs" variant="ghost" ml="1" onClick={() => toggleFollow("none")}>
                  <LuX />
                </IconButton>
              </Badge>
            ) : (
              <Button size="xs" variant="outline" onClick={() => toggleFollow("subscribed")}>Follow</Button>
            )}
          </HStack>
          <ReleaseIcons artist={artist} album={title} mbid={mbid || undefined} />
        </Box>
      </Flex>

      <Heading size="lg" mt="6" mb="2">Tracks</Heading>
      {tracks.length === 0 ? (
        <Text color="fg.muted">{msg}</Text>
      ) : (
        <Stack gap="0">
          {tracks.map((t, i) => (
            <Flex key={i} align="center" gap="3" py="1.5" borderBottomWidth="1px">
              <Text color="fg.muted" w="1.5rem" textAlign="end" flex="none">{i + 1}</Text>
              <Box flex="1" minW="0">
                {t.url ? (
                  <CLink href={t.url} target="_blank" rel="noopener">{t.name}</CLink>
                ) : (
                  t.name
                )}
              </Box>
              {t.preview_url && (
                <audio controls preload="none" src={t.preview_url} style={{ height: "2rem", maxWidth: 240, flex: "none" }} />
              )}
              {t.duration ? <Text color="fg.muted" flex="none">{fmtDuration(t.duration)}</Text> : null}
            </Flex>
          ))}
        </Stack>
      )}
    </Box>
  );
}
