// Shared agenda (week-by-week) and calendar (month grid) rendering, used by the
// Upcoming and Discover pages. Ported from app/static/relview.js. Items each
// carry a `normalized_date` (YYYY-MM-DD); callers supply how to render a row.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { LuCalendarDays, LuChevronLeft, LuChevronRight, LuListTree } from "react-icons/lu";
import { Button } from "./ui/button";
import { Calendar as DayCalendar, CalendarDayButton } from "./ui/calendar";
import { Card, CardContent } from "./ui/card";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

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
function fmtDay(isoStr: string): string {
  return parseISO(isoStr).toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function Agenda<T extends DatedItem>({
  items,
  renderItem,
  emptyMsg,
}: {
  items: T[];
  renderItem: (item: T, key: number) => ReactNode;
  emptyMsg?: ReactNode;
}) {
  const dated = items.filter((r) => r.normalized_date);
  const undated = items.filter((r) => !r.normalized_date);
  if (!dated.length && !undated.length) {
    return <p className="text-muted-foreground">{emptyMsg || "Nothing to show."}</p>;
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
    <div className="flex flex-col gap-6">
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
          <section key={ws}>
            <h3 className="mb-2 border-b pb-1 text-lg">
              {rel && <span className="mr-2 font-bold">{rel}</span>}
              <span className="text-muted-foreground">{fmtRange(start, end)}</span>
            </h3>
            {(() => {
              // Group the week's releases by day, with a day subheading.
              const dayBuckets = new Map<string, T[]>();
              buckets.get(ws)!.forEach((r) => {
                const k = r.normalized_date!;
                if (!dayBuckets.has(k)) dayBuckets.set(k, []);
                dayBuckets.get(k)!.push(r);
              });
              return Array.from(dayBuckets.keys())
                .sort()
                .map((dayIso) => (
                  <div key={dayIso} className="mt-3">
                    <p className="mb-1 text-sm font-semibold text-muted-foreground">
                      {fmtDay(dayIso)}
                    </p>
                    {dayBuckets.get(dayIso)!.map((r) => renderItem(r, keyN++))}
                  </div>
                ));
            })()}
          </section>
        );
      })}
      {undated.length > 0 && (
        <section>
          <h3 className="mb-2 border-b pb-1 text-lg">
            <span className="text-muted-foreground">Date TBA</span>
          </h3>
          {undated.map((r) => renderItem(r, keyN++))}
        </section>
      )}
    </div>
  );
}

export function Calendar<T extends DatedItem>({
  items,
  renderEvent,
  itemDots,
}: {
  items: T[];
  // Render one item in the selected-day list under the calendar.
  renderEvent: (item: T, key: number) => ReactNode;
  // Tailwind bg color class(es) for this item's day dot(s) (e.g. source colors).
  itemDots?: (item: T) => string[];
}) {
  const now = new Date();
  const [month, setMonth] = useState(() => new Date());
  const [selected, setSelected] = useState<Date | undefined>(undefined);
  const didInit = useRef(false);

  // Bucket releases by YYYY-MM-DD, then collapse each day to its distinct dots.
  const byDay: Record<string, T[]> = {};
  items.forEach((r) => {
    if (r.normalized_date) (byDay[r.normalized_date] = byDay[r.normalized_date] || []).push(r);
  });
  const dotsByDay: Record<string, string[]> = {};
  for (const [d, list] of Object.entries(byDay)) {
    const colors = new Set<string>();
    list.forEach((it) => (itemDots ? itemDots(it) : ["bg-foreground"]).forEach((c) => colors.add(c)));
    dotsByDay[d] = Array.from(colors);
  }

  const selectedItems = selected ? byDay[iso(selected)] || [] : [];

  // Days that actually have releases, sorted. Powers disabling empty days and
  // the prev/next jumps that skip straight to the next/previous day with data.
  const dataDays = Object.keys(byDay).sort();
  const refIso = selected ? iso(selected) : iso(now);
  const prevDay = [...dataDays].reverse().find((d) => d < refIso);
  const nextDay = dataDays.find((d) => d > refIso);
  function jumpTo(isoStr: string) {
    const d = parseISO(isoStr);
    setSelected(d);
    setMonth(d);
  }
  function goToday() {
    const t = new Date();
    setSelected(t);
    setMonth(t);
  }

  // On first load (once releases arrive), open on the day with data closest to
  // today and select it, so the list below isn't empty on arrival.
  useEffect(() => {
    if (didInit.current || !dataDays.length) return;
    const todayMs = parseISO(iso(now)).getTime();
    let best = dataDays[0];
    let bestDiff = Infinity;
    for (const d of dataDays) {
      const diff = Math.abs(parseISO(d).getTime() - todayMs);
      if (diff < bestDiff) { bestDiff = diff; best = d; }
    }
    const dt = parseISO(best);
    setSelected(dt);
    setMonth(dt);
    didInit.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items]);

  return (
    <div className="flex flex-col items-center gap-4">
      <Card className="w-fit p-0">
        <CardContent className="p-0">
          <DayCalendar
            mode="single"
            selected={selected}
            onSelect={setSelected}
            month={month}
            onMonthChange={setMonth}
            showOutsideDays
            fixedWeeks
            // Only days with releases are clickable.
            disabled={(date) => !byDay[iso(date)]}
            captionLayout="dropdown"
            startMonth={new Date(now.getFullYear() - 1, 0)}
            endMonth={new Date(now.getFullYear() + 2, 11)}
            className="[--cell-size:--spacing(10)] md:[--cell-size:--spacing(12)]"
            components={{
              // Keep the regular shadcn day button, but stack a colored dot per
              // source under the number when that day has releases.
              DayButton: ({ children, modifiers, day, ...props }) => {
                const dots = dotsByDay[iso(day.date)] || [];
                return (
                  <CalendarDayButton day={day} modifiers={modifiers} {...props}>
                    {children}
                    {dots.length > 0 && (
                      <span className="flex gap-0.5">
                        {dots.map((c, i) => (
                          <span key={i} className={"size-1.5 rounded-full " + c} />
                        ))}
                      </span>
                    )}
                  </CalendarDayButton>
                );
              },
            }}
          />
        </CardContent>
      </Card>

      {/* Jump straight to the previous / next day that has releases. */}
      <div className="flex items-center justify-center gap-2">
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Previous release day"
          disabled={!prevDay}
          onClick={() => prevDay && jumpTo(prevDay)}
        >
          <LuChevronLeft />
        </Button>
        <Button variant="outline" size="sm" onClick={goToday}>Today</Button>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="Next release day"
          disabled={!nextDay}
          onClick={() => nextDay && jumpTo(nextDay)}
        >
          <LuChevronRight />
        </Button>
      </div>

      {selected && (
        <div className="w-full max-w-sm">
          <p className="mb-2 text-sm font-medium">
            {selected.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
          </p>
          {selectedItems.length === 0 ? (
            <p className="text-sm text-muted-foreground">No releases this day.</p>
          ) : (
            <div className="flex flex-col gap-1.5">{selectedItems.map((r, i) => renderEvent(r, i))}</div>
          )}
        </div>
      )}
    </div>
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
    <div className="flex gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="Agenda"
            size="icon-sm"
            variant={view === "agenda" ? "secondary" : "ghost"}
            onClick={() => onChange("agenda")}
          >
            <LuListTree />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Agenda</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            aria-label="Calendar"
            size="icon-sm"
            variant={view === "calendar" ? "secondary" : "ghost"}
            onClick={() => onChange("calendar")}
          >
            <LuCalendarDays />
          </Button>
        </TooltipTrigger>
        <TooltipContent>Calendar</TooltipContent>
      </Tooltip>
    </div>
  );
}
