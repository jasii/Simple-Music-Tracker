import { Badge, Box, CheckboxCard, Flex, HStack, Heading, Image, Link as CLink, Spacer, Text, Wrap } from "@chakra-ui/react";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { api, art } from "../api";
import type { UpcomingRelease } from "../types";
import { useNav } from "../nav";
import { Agenda, Calendar, ViewToggle } from "../components/RelView";
import { ReleaseIcons } from "../lib/format";

const TYPES = [
  { value: "Album", label: "Albums" },
  { value: "EP", label: "EPs" },
  { value: "Single", label: "Singles" },
];

function albumHref(r: UpcomingRelease): string {
  const qs = new URLSearchParams({ artist: r.artist_name, title: r.title, from: "upcoming" });
  if (r.mbid) qs.set("mbid", r.mbid);
  return "/album?" + qs.toString();
}

function AgendaRow({ r }: { r: UpcomingRelease }) {
  return (
    <Flex gap="3" py="2.5" align="center">
      <Box className={!r.image_url ? "vinyl-art" : undefined} boxSize="150px" flex="none" rounded="md" bg="bg.muted" overflow="hidden">
        {r.image_url && <Image src={art(r.image_url)} alt="" w="full" h="full" objectFit="cover" loading="lazy" />}
      </Box>
      <Box flex="1" minW="0">
        <Text fontWeight="semibold">
          <CLink as={RouterLink} {...{ to: albumHref(r) }}>{r.title}</CLink>
        </Text>
        <Box>
          <CLink as={RouterLink} {...{ to: `/artist/${r.artist_id}` }}>{r.artist_name}</CLink>
        </Box>
        {r.primary_type && (
          <Badge variant="outline" mt="1.5">{r.primary_type}</Badge>
        )}
      </Box>
      <Box alignSelf="flex-start" flex="none">
        <ReleaseIcons artist={r.artist_name} album={r.title} mbid={r.mbid} />
      </Box>
    </Flex>
  );
}

function CalEvent({ r }: { r: UpcomingRelease }) {
  return (
    <Badge
      as={RouterLink}
      {...{ to: `/artist/${r.artist_id}` }}
      size="sm"
      variant="surface"
      colorPalette="green"
      fontSize="2xs"
      px="1"
      py="0"
      title={`${r.artist_name} – ${r.title}`}
    >
      {r.artist_name}
    </Badge>
  );
}

export default function Upcoming() {
  const nav = useNav();
  const { data: items } = useQuery({
    queryKey: ["upcoming"],
    queryFn: () =>
      api.getJSON<{ releases: UpcomingRelease[] }>("/api/upcoming/releases").then((d) => d.releases || []),
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
    <Box>
      <Flex align="center" justify="space-between" gap="3" wrap="wrap">
        <Heading size="xl">Upcoming Releases</Heading>
        <HStack gap="3" wrap="wrap" fontSize="sm">
          <Wrap gap="2">
            {TYPES.map((t) => (
              <CheckboxCard.Root
                key={t.value}
                size="sm"
                w="auto"
                variant="surface"
                colorPalette="green"
                checked={!hiddenTypes.has(t.value)}
                onCheckedChange={(e) => toggleType(t.value, !!e.checked)}
              >
                <CheckboxCard.HiddenInput />
                <CheckboxCard.Control py="1" px="2" minH="0" alignItems="center" cursor="pointer">
                  <CheckboxCard.Label>{t.label}</CheckboxCard.Label>
                </CheckboxCard.Control>
              </CheckboxCard.Root>
            ))}
          </Wrap>
          <ViewToggle view={view} onChange={changeView} />
        </HStack>
      </Flex>
      {!nav.hide_page_descriptions && (
        <Text color="fg.muted" mt="1" mb="3">New and upcoming albums from artists you follow.</Text>
      )}

      {!items ? (
        <Text color="fg.muted">Loading...</Text>
      ) : view === "calendar" ? (
        <Calendar items={visible} renderEvent={(r, k) => <CalEvent key={k} r={r} />} />
      ) : (
        <Agenda
          items={visible}
          renderItem={(r, k) => <AgendaRow key={k} r={r} />}
          emptyMsg="No upcoming releases from artists you follow."
        />
      )}
      <Spacer />
    </Box>
  );
}
