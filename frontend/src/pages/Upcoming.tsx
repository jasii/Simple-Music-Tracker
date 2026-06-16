import { Badge, Box, Checkbox, Flex, HStack, Heading, Image, Link as CLink, Spacer, Text, Wrap } from "@chakra-ui/react";
import { useEffect, useMemo, useState } from "react";
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

function fmtDate(iso: string): string {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

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
        <CLink as={RouterLink} {...{ to: `/artist/${r.artist_id}` }}>{r.artist_name}</CLink>
        <Text color="fg.muted" fontSize="sm">{r.normalized_date ? fmtDate(r.normalized_date) : "date TBA"}</Text>
        <ReleaseIcons artist={r.artist_name} album={r.title} mbid={r.mbid} />
        {r.primary_type && (
          <Badge variant="outline" mt="1.5">{r.primary_type}</Badge>
        )}
      </Box>
    </Flex>
  );
}

function CalEvent({ r }: { r: UpcomingRelease }) {
  const label = `${r.artist_name} – ${r.title}`;
  return (
    <CLink
      as={RouterLink}
      {...{ to: `/artist/${r.artist_id}` }}
      display="block"
      bg="bg.muted"
      borderLeftWidth="2px"
      borderLeftColor="green.solid"
      rounded="sm"
      px="1"
      py="0.5"
      mb="0.5"
      fontSize="xs"
      title={label}
      whiteSpace="nowrap"
      overflow="hidden"
      textOverflow="ellipsis"
      color="fg"
    >
      {label}
    </CLink>
  );
}

export default function Upcoming() {
  const nav = useNav();
  const [items, setItems] = useState<UpcomingRelease[] | null>(null);
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

  useEffect(() => {
    api
      .getJSON<{ releases: UpcomingRelease[] }>("/api/upcoming/releases")
      .then((d) => setItems(d.releases || []))
      .catch(() => setItems([]));
  }, []);

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
              <Checkbox.Root
                key={t.value}
                size="sm"
                checked={!hiddenTypes.has(t.value)}
                onCheckedChange={(e) => toggleType(t.value, !!e.checked)}
              >
                <Checkbox.HiddenInput />
                <Checkbox.Control />
                <Checkbox.Label>{t.label}</Checkbox.Label>
              </Checkbox.Root>
            ))}
          </Wrap>
          <ViewToggle view={view} onChange={changeView} />
        </HStack>
      </Flex>
      {!nav.hide_page_descriptions && (
        <Text color="fg.muted" mt="1" mb="3">New and upcoming albums from artists you follow.</Text>
      )}

      {items === null ? (
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
