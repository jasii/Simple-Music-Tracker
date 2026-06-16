// Shared agenda (week-by-week) and calendar (month grid) rendering, used by the
// Upcoming and Discover pages. Ported from app/static/relview.js. Items each
// carry a `normalized_date` (YYYY-MM-DD); callers supply how to render a row.
import { Box, Button, Grid, GridItem, HStack, Heading, IconButton, Stack, Text } from "@chakra-ui/react";
import { useState, type ReactNode } from "react";
import { LuCalendarDays, LuListTree } from "react-icons/lu";

export interface DatedItem {
  normalized_date?: string | null;
}

function iso(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
function parseISO(s: string): Date {
  return new Date(s + "T00:00:00");
}
function weekStart(d: Date): Date {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  x.setDate(x.getDate() - x.getDay());
  return x;
}
function fmtRange(start: Date, end: Date): string {
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  return `${start.toLocaleDateString(undefined, opts)} – ${end.toLocaleDateString(undefined, opts)}`;
}

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function Agenda<T extends DatedItem>({
  items,
  renderItem,
  emptyMsg,
}: {
  items: T[];
  renderItem: (item: T, key: number) => ReactNode;
  emptyMsg?: string;
}) {
  const dated = items.filter((r) => r.normalized_date);
  const undated = items.filter((r) => !r.normalized_date);
  if (!dated.length && !undated.length) {
    return <Text color="fg.muted">{emptyMsg || "Nothing to show."}</Text>;
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const thisWeek = weekStart(today).getTime();

  const buckets = new Map<number, T[]>();
  dated.forEach((r) => {
    const ws = weekStart(parseISO(r.normalized_date!)).getTime();
    if (!buckets.has(ws)) buckets.set(ws, []);
    buckets.get(ws)!.push(r);
  });
  const weeks = Array.from(buckets.keys()).sort((a, b) => a - b);

  let keyN = 0;
  return (
    <Stack gap="6">
      {weeks.map((ws) => {
        const start = new Date(ws);
        const end = new Date(ws);
        end.setDate(end.getDate() + 6);
        const diff = Math.round((ws - thisWeek) / (7 * 86400000));
        let rel = "";
        if (diff === 0) rel = "This week";
        else if (diff === 1) rel = "Next week";
        else if (diff > 1) rel = `In ${diff} weeks`;
        else if (diff === -1) rel = "Last week";
        return (
          <Box as="section" key={ws}>
            <Heading as="h3" size="md" mb="2" pb="1" borderBottomWidth="1px">
              {rel && <Text as="span" fontWeight="bold" mr="2">{rel}</Text>}
              <Text as="span" color="fg.muted">{fmtRange(start, end)}</Text>
            </Heading>
            {buckets.get(ws)!.map((r) => renderItem(r, keyN++))}
          </Box>
        );
      })}
      {undated.length > 0 && (
        <Box as="section">
          <Heading as="h3" size="md" mb="2" pb="1" borderBottomWidth="1px">
            <Text as="span" color="fg.muted">Date TBA</Text>
          </Heading>
          {undated.map((r) => renderItem(r, keyN++))}
        </Box>
      )}
    </Stack>
  );
}

export function Calendar<T extends DatedItem>({
  items,
  renderEvent,
}: {
  items: T[];
  renderEvent: (item: T, key: number) => ReactNode;
}) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth());

  const first = new Date(year, month, 1);
  const title = first.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const gridStart = weekStart(first);
  const todayIso = iso(new Date());

  const byDay: Record<string, T[]> = {};
  items.forEach((r) => {
    if (r.normalized_date) (byDay[r.normalized_date] = byDay[r.normalized_date] || []).push(r);
  });

  function prev() {
    setMonth((m) => { if (m === 0) { setYear((y) => y - 1); return 11; } return m - 1; });
  }
  function next() {
    setMonth((m) => { if (m === 11) { setYear((y) => y + 1); return 0; } return m + 1; });
  }
  function today() {
    const n = new Date();
    setYear(n.getFullYear());
    setMonth(n.getMonth());
  }

  const cells = [];
  let keyN = 0;
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    const dIso = iso(d);
    const inMonth = d.getMonth() === month;
    const evs = byDay[dIso] || [];
    cells.push(
      <GridItem
        key={i}
        bg={inMonth ? "bg" : "bg.muted"}
        color={inMonth ? "fg" : "fg.muted"}
        minH={{ base: "3.5rem", md: "5.5rem" }}
        p="1"
        fontSize="sm"
        overflow="hidden"
        outline={dIso === todayIso ? "2px solid" : undefined}
        outlineColor="green.solid"
        outlineOffset="-2px"
      >
        <Text fontWeight="semibold" mb="0.5">{d.getDate()}</Text>
        {evs.map((r) => renderEvent(r, keyN++))}
      </GridItem>,
    );
  }

  return (
    <Box>
      <HStack mb="3" gap="2">
        <IconButton aria-label="Previous month" variant="outline" size="sm" onClick={prev}>←</IconButton>
        <Text fontWeight="bold" minW="10rem">{title}</Text>
        <IconButton aria-label="Next month" variant="outline" size="sm" onClick={next}>→</IconButton>
        <Button variant="outline" size="sm" onClick={today}>Today</Button>
      </HStack>
      <Grid templateColumns="repeat(7, 1fr)" gap="1px" bg="border" borderWidth="1px">
        {DOW.map((d) => (
          <GridItem key={d} bg="bg" textAlign="center" fontWeight="semibold" fontSize="sm" py="1" color="fg.muted">
            {d}
          </GridItem>
        ))}
        {cells}
      </Grid>
    </Box>
  );
}

export function ViewToggle({
  view,
  onChange,
}: {
  view: "agenda" | "calendar";
  onChange: (v: "agenda" | "calendar") => void;
}) {
  return (
    <HStack gap="1">
      <IconButton
        aria-label="Agenda"
        title="Agenda"
        size="sm"
        variant={view === "agenda" ? "subtle" : "ghost"}
        onClick={() => onChange("agenda")}
      >
        <LuListTree />
      </IconButton>
      <IconButton
        aria-label="Calendar"
        title="Calendar"
        size="sm"
        variant={view === "calendar" ? "subtle" : "ghost"}
        onClick={() => onChange("calendar")}
      >
        <LuCalendarDays />
      </IconButton>
    </HStack>
  );
}
