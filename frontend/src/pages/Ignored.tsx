import { Box, Button, Flex, Heading, Input, Link as CLink, Stack, Text, Wrap } from "@chakra-ui/react";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import type { Artist } from "../types";

export default function Ignored() {
  const qc = useQueryClient();
  const { data: all } = useQuery({
    queryKey: ["ignored"],
    queryFn: () => api.ignored().then((d) => d.artists),
  });
  const [search, setSearch] = useState("");

  const shown = useMemo(() => {
    if (!all) return [];
    const q = search.trim().toLowerCase();
    return q ? all.filter((a) => a.name.toLowerCase().includes(q)) : all;
  }, [all, search]);

  function unignore(id: number) {
    api.setIgnore(id, false).then(() =>
      qc.setQueryData<Artist[]>(["ignored"], (prev) => (prev ? prev.filter((a) => a.id !== id) : prev)),
    );
  }
  function unignoreAllShown() {
    const ids = shown.map((a) => a.id);
    if (!ids.length) return;
    api.bulkIgnore(ids, false).then(() => qc.invalidateQueries({ queryKey: ["ignored"] }));
  }

  return (
    <Box>
      <Heading size="xl" mb="2">Ignored Artists</Heading>
      <Text color="fg.muted" mb="3">
        Artists hidden from your library list (e.g. they no longer release music). They are kept
        here so you can bring them back any time.
      </Text>

      <Wrap gap="2" mb="3">
        <Input flex="1 1 14rem" type="search" placeholder="Filter ignored..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <Button variant="outline" onClick={unignoreAllShown}>Unignore all shown</Button>
      </Wrap>

      {!all ? (
        <Text color="fg.muted">Loading...</Text>
      ) : shown.length === 0 ? (
        <Text color="fg.muted">
          {all.length
            ? "No matches."
            : 'No ignored artists. Use "Ignore" on the Artists page to hide ones you don\'t want cluttering your library.'}
        </Text>
      ) : (
        <Stack gap="2">
          {shown.map((a) => (
            <Flex key={a.id} borderWidth="1px" rounded="md" p="2.5" gap="3" align="center">
              <Box flex="1" minW="0">
                <CLink as={RouterLink} {...{ to: `/artist/${a.id}` }}>{a.name}</CLink>
                <Text color="fg.muted">{a.track_count || 0} tracks</Text>
              </Box>
              <Button size="sm" variant="outline" onClick={() => unignore(a.id)}>Unignore</Button>
            </Flex>
          ))}
        </Stack>
      )}
    </Box>
  );
}
