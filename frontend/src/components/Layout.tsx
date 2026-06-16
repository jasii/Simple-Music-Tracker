import { Box, Container, Flex, HStack, Link as CLink, Spacer, Text } from "@chakra-ui/react";
import { NavLink, Link as RouterLink, Outlet } from "react-router-dom";
import { ColorModeButton } from "./ui/color-mode";
import { useNav } from "../nav";

export default function Layout() {
  const nav = useNav();
  return (
    <Flex direction="column" minH="100dvh">
      <Box
        as="header"
        position="sticky"
        top="0"
        zIndex="docked"
        bg="bg"
        borderBottomWidth="1px"
        px="4"
        py="2.5"
      >
        <Flex align="center" gap="3" wrap="wrap" maxW="60rem" mx="auto">
          <CLink as={RouterLink} {...{ to: nav.home_path }} fontWeight="bold" color="fg">
            Simple Music Tracker
          </CLink>
          <Spacer />
          <HStack gap="4" wrap="wrap">
            {nav.items
              .filter((item) => !item.hidden)
              .map((item) => (
                <CLink
                  key={item.key}
                  as={NavLink}
                  {...{ to: item.path }}
                  color="fg"
                  _hover={{ textDecoration: "underline" }}
                  css={{ "&.active": { fontWeight: "bold", textDecoration: "underline" } }}
                >
                  {item.label}
                </CLink>
              ))}
            <ColorModeButton />
          </HStack>
        </Flex>
      </Box>

      <Container as="main" maxW="60rem" flex="1" py="4">
        <Outlet />
      </Container>

      <Box as="footer" maxW="60rem" mx="auto" w="full" mt="8" mb="12" px="4" pt="4" borderTopWidth="1px">
        <Text color="fg.muted" fontSize="sm">
          Simple Music Tracker
          <Text as="span" color="fg.muted" mx="2">/</Text>
          <CLink href="/api/upcoming?window=week" color="fg.muted">API</CLink>
        </Text>
      </Box>
    </Flex>
  );
}
