import { Badge, Box, Button, Flex, Heading, IconButton, Image, Link as CLink, Spacer, Stack, Text } from "@chakra-ui/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link as RouterLink } from "react-router-dom";
import { LuBell, LuBellRing } from "react-icons/lu";
import { api, art } from "../api";
import { useNav } from "../nav";

export default function Following() {
  const nav = useNav();
  const qc = useQueryClient();
  const { data: artists } = useQuery({
    queryKey: ["subscriptions"],
    queryFn: () => api.subscriptions().then((d) => d.artists),
  });

  function unfollow(id: number) {
    api.setSubscription(id, "none").then(() => qc.invalidateQueries({ queryKey: ["subscriptions"] }));
  }
  function setNotify(id: number, on: boolean) {
    api.setSubscription(id, on ? "notify" : "subscribed").then(() =>
      qc.invalidateQueries({ queryKey: ["subscriptions"] }),
    );
  }

  return (
    <Box>
      <Heading size="xl" mb="2">Following</Heading>
      {!nav.hide_page_descriptions && (
        <Text color="fg.muted" mb="3">
          Artists you follow. "Notify" artists also trigger your webhook on a new release.
        </Text>
      )}

      {!artists ? (
        <Text color="fg.muted">Loading...</Text>
      ) : artists.length === 0 ? (
        <Text color="fg.muted">Not following anyone yet. Follow artists from the Artists page.</Text>
      ) : (
        <Stack gap="2">
          {artists.map((a) => (
            <Flex key={a.id} borderWidth="1px" rounded="md" p="2.5" gap="3" align="center">
              {a.image_url && (
                <Image src={art(a.image_url)} alt="" boxSize="56px" objectFit="cover" rounded="md" loading="lazy" />
              )}
              <Box flex="1" minW="0">
                <CLink as={RouterLink} {...{ to: `/artist/${a.id}` }}>{a.name}</CLink>
                <Text color="fg.muted">{a.track_count || 0} tracks</Text>
              </Box>
              <Badge variant="outline" colorPalette="green">{a.subscription === "notify" ? "notify" : "following"}</Badge>
              <IconButton
                aria-label={a.subscription === "notify" ? "Disable new-release notifications" : "Notify on new release"}
                title={a.subscription === "notify" ? "Disable new-release notifications" : "Notify on new release"}
                size="sm"
                variant={a.subscription === "notify" ? "solid" : "outline"}
                colorPalette="green"
                onClick={() => setNotify(a.id, a.subscription !== "notify")}
              >
                {a.subscription === "notify" ? <LuBellRing /> : <LuBell />}
              </IconButton>
              <Button size="sm" variant="outline" onClick={() => unfollow(a.id)}>Unfollow</Button>
            </Flex>
          ))}
          <Spacer />
        </Stack>
      )}
    </Box>
  );
}
