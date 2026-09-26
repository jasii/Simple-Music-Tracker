import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  LuBell,
  LuChevronDown,
  LuChevronUp,
  LuCircleHelp,
  LuEye,
  LuEyeOff,
  LuCompass,
  LuDownload,
  LuLibrary,
  LuPlug,
  LuRotateCw,
  LuSettings,
  LuTags,
  LuWrench,
} from "react-icons/lu";
import { api } from "../api";
import type { NavConfig, PluginConfigField, PluginInfo, Settings as SettingsT } from "../types";
import { Progress } from "../components/ui/progress";
import { Button } from "../components/ui/button";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "../components/ui/accordion";
import { Card, CardContent } from "../components/ui/card";
import { ServiceIcon } from "../components/ServiceIcon";
import { timeAgo } from "../lib/format";
import { Checkbox } from "../components/ui/checkbox";
import { Input } from "../components/ui/input";
import { Label as UiLabel } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Textarea } from "../components/ui/textarea";
import { Separator } from "../components/ui/separator";
import { Skeleton } from "../components/ui/skeleton";
import { Alert, AlertDescription } from "../components/ui/alert";
import { Badge } from "../components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

// The settings panes, in sidebar order. Each is one screenful of related
// settings rather than the single long column this page used to be.
const SECTIONS = [
  { key: "help", label: "Help & Info", icon: LuCircleHelp },
  { key: "general", label: "General", icon: LuSettings },
  { key: "library", label: "Library", icon: LuLibrary },
  // Metadata describes what the library holds, so it reads next; Discovery is
  // about finding more of it.
  { key: "metadata", label: "Metadata", icon: LuTags },
  { key: "discovery", label: "Discovery", icon: LuCompass },
  { key: "downloads", label: "Downloads & quality", icon: LuDownload },
  { key: "notifications", label: "Notifications", icon: LuBell },
  { key: "apis", label: "Keys & accounts", icon: LuPlug },
  { key: "maintenance", label: "Maintenance", icon: LuWrench },
];
const SECTION_KEYS = SECTIONS.map((s) => s.key);

const MTYPES = [
  ["album", "Albums"],
  ["ep", "EPs"],
  ["single", "Singles"],
];

function Hint({ children }: { children: React.ReactNode }) {
  return <p className="mt-1 mb-3 text-sm text-muted-foreground">{children}</p>;
}
function Label({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return <UiLabel htmlFor={htmlFor} className="mt-3 mb-1.5 font-semibold">{children}</UiLabel>;
}
function Legend({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-2 text-lg font-semibold">{children}</h2>;
}
// A value that shouldn't be sitting in plain sight -- an API key, a session
// cookie, an email address. Hidden until asked for, because the usual reason
// this page is open is to change something else entirely.
function Secret({
  id,
  value,
  onChange,
  placeholder,
  multiline,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  multiline?: boolean;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div className="flex items-start gap-2">
      {shown && multiline ? (
        <Textarea
          id={id}
          rows={1}
          wrap="off"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 min-h-0 flex-1 resize-none overflow-hidden field-sizing-fixed font-mono text-xs whitespace-nowrap"
        />
      ) : (
        <Input
          id={id}
          // A masked textarea isn't a thing, so a hidden multi-line secret
          // shows as one masked line; revealing it restores the textarea.
          type={shown ? "text" : "password"}
          autoComplete="new-password"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={multiline && shown ? "font-mono text-xs" : undefined}
        />
      )}
      <Button
        type="button"
        size="icon-sm"
        variant="outline"
        aria-label={shown ? "Hide value" : "Show value"}
        aria-pressed={shown}
        onClick={() => setShown((v) => !v)}
      >
        {shown ? <LuEyeOff /> : <LuEye />}
      </Button>
    </div>
  );
}

function Section({ children }: { children: React.ReactNode }) {
  return (
    <Card className="mb-4">
      <CardContent>{children}</CardContent>
    </Card>
  );
}
// A group of settings inside a card: ruled off from what came before, named,
// and given a line of its own explaining what the group is for.
function SubHeading({ children, note, first }: {
  children: React.ReactNode;
  note?: React.ReactNode;
  first?: boolean;
}) {
  return (
    <>
      {!first && <Separator className="mt-5 mb-3" />}
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="font-semibold">{children}</h3>
        {note && <span className="text-xs text-muted-foreground">{note}</span>}
      </div>
    </>
  );
}
// Something you set up once and then leave alone: named, folded, out of the
// way of the settings that get changed.
function FoldedCard({ title, children }: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="mb-4">
      <CardContent>
        <Accordion type="single" collapsible>
          <AccordionItem value={title} className="border-b-0">
            <AccordionTrigger className="py-0 hover:no-underline">
              <span className="text-lg font-semibold">{title}</span>
            </AccordionTrigger>
            <AccordionContent className="pb-0">
              <Separator className="mt-3 mb-1" />
              {children}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  );
}

interface NavRow {
  key: string;
  label: string;
  show: boolean;
}

function fmtBytes(n?: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v : v.toFixed(1)) + " " + units[i];
}

// Relative "time since" for a unix-epoch-seconds timestamp.
function fmtAgo(epoch?: number | null): string {
  if (!epoch) return "never";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - epoch));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function Settings() {
  const [form, setForm] = useState<SettingsT>({});
  const qc = useQueryClient();
  const [navTpl, setNavTpl] = useState<NavConfig | null>(null);
  const [navRows, setNavRows] = useState<NavRow[]>([]);
  const [saveResult, setSaveResult] = useState("");
  const [keyResult, setKeyResult] = useState("");
  const [pluginTest, setPluginTest] = useState<Record<string, string>>({});
  const [webhookResult, setWebhookResult] = useState("");
  const [scanResults, setScanResults] = useState<Record<string, string>>({});
  const [cacheStats, setCacheStats] = useState("Measuring stored data...");
  const [staleBytes, setStaleBytes] = useState(0);
  const [purgeResult, setPurgeResult] = useState("");
  const [compactResult, setCompactResult] = useState("");
  const [userResult, setUserResult] = useState("");
  const [calendarUrl, setCalendarUrl] = useState("");
  const [calendarResult, setCalendarResult] = useState("");
  const [backupSections, setBackupSections] = useState(new Set(["settings", "artists", "artwork"]));
  const [importSections, setImportSections] = useState(new Set(["settings", "artists", "artwork"]));
  const [importResult, setImportResult] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const dragKey = useRef<string | null>(null);
  // The open pane is reflected in the URL (?tab=discovery) so other pages can
  // deep-link straight to it. "plugins" is kept as an alias for the old link.
  const [searchParams, setSearchParams] = useSearchParams();
  const [section, setSection] = useState(() => {
    const t = searchParams.get("tab");
    if (t === "plugins") return "discovery";
    return t && SECTION_KEYS.includes(t) ? t : "help";
  });
  function changeSection(next: string) {
    setSection(next);
    setSearchParams(next === "help" ? {} : { tab: next }, { replace: true });
  }

  // Initial load of everything this page shows. There's no reload button: with
  // changes saving themselves, the form already matches the server, so re-
  // reading it would look like nothing happened.
  function loadAll() {
    api.settings().then(setForm);
    api.getJSON<NavConfig>("/api/nav").then((n) => {
      setNavTpl(n);
      setNavRows(n.items.map((it) => ({ key: it.key, label: it.label, show: !it.hidden })));
    });
    loadCacheStats();
    api.calendarUrl().then((r) => setCalendarUrl(r.url)).catch(() => {});
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function loadCacheStats() {
    api
      .cacheStats()
      .then((s: any) => {
        setStaleBytes(s.stale_bytes || 0);
        setCacheStats(
          `${fmtBytes(s.total_bytes)} stored · ${fmtBytes(s.stale_bytes)} of it belongs to artists you no longer follow (${s.stale_json_entries} records, ${s.stale_art_files} images).`,
        );
      })
      .catch(() => setCacheStats("Could not measure stored data."));
  }

  const get = (k: string) => form[k] ?? "";
  const set = (k: string, v: string) => {
    setForm((p) => ({ ...p, [k]: v }));
    queueSave({ [k]: v });
  };
  // Checkbox checked if the value isn't the literal "false" (default-on settings).
  const onUnlessFalse = (k: string) => get(k) !== "false";
  const onIfTrue = (k: string) => get(k) === "true";

  function toggleCsv(k: string, value: string, checked: boolean) {
    const cur = new Set((get(k) || "").split(",").filter(Boolean));
    if (checked) cur.add(value); else cur.delete(value);
    set(k, Array.from(cur).join(","));
  }
  const csvHas = (k: string, value: string) => (get(k) || "").split(",").includes(value);

  // Nav order: first row is home and locked-on; settings is locked-on too.
  const navOrder = useMemo(() => navRows.map((r) => r.key).join(","), [navRows]);
  const navHidden = useMemo(
    () => navRows.filter((r, i) => i !== 0 && r.key !== "settings" && !r.show).map((r) => r.key).join(","),
    [navRows],
  );

  // Settings save themselves: every change is queued and written a moment
  // later, so there is no Save button to forget to press. Only the keys that
  // actually changed are sent, which also means two panes can't overwrite
  // each other's fields.
  const pending = useRef<Record<string, string>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();
  const savedTimer = useRef<ReturnType<typeof setTimeout>>();

  function flushNow(): Promise<void> {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const values = pending.current;
    pending.current = {};
    if (!Object.keys(values).length) return Promise.resolve();
    setSaveResult("Saving...");
    return api
      .saveSettings(values)
      .then(() => {
        // The Subsonic password is write-only: the server swaps it for a
        // token, so drop it from the form once it's gone through.
        if (values.subsonic_password) setForm((f) => ({ ...f, subsonic_password: "" }));
        setSaveResult("Saved");
        if (savedTimer.current) clearTimeout(savedTimer.current);
        savedTimer.current = setTimeout(() => setSaveResult(""), 2000);
      })
      .catch(() => {
        // Put the values back so the next change retries them together.
        pending.current = { ...values, ...pending.current };
        setSaveResult("Save failed - will retry");
      });
  }

  function queueSave(values: Record<string, string>) {
    pending.current = { ...pending.current, ...values };
    setSaveResult("Saving...");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    // Long enough that typing a password or a URL is one write, short enough
    // that a toggle feels immediate.
    saveTimer.current = setTimeout(() => { void flushNow(); }, 700);
  }

  // Anything still unwritten goes out when the page is left.
  useEffect(() => () => { void flushNow(); }, []);

  // Nav order and hidden pages live in the same settings table, but they're
  // edited by dragging rather than typing, so they queue on change.
  const navLoaded = useRef(false);
  useEffect(() => {
    if (!navRows.length) return;
    if (!navLoaded.current) { navLoaded.current = true; return; }
    queueSave({ nav_order: navOrder, nav_hidden: navHidden });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [navOrder, navHidden]);

  function testWebhook() {
    setWebhookResult("Sending...");
    flushNow()
      .then(() => api.testWebhook())
      .then((r) => setWebhookResult(r.ok ? `OK (${r.message})` : `Failed: ${r.message}`))
      .catch(() => setWebhookResult("Failed."));
  }
  function testApiKey() {
    setKeyResult("Checking...");
    flushNow()
      .then(() => api.healthLastfmKey())
      .then((r) => setKeyResult((r.ok ? "OK - " : "Failed - ") + (r.message || "")))
      .catch(() => setKeyResult("Check failed."));
  }

  function testLastfmUser() {
    setUserResult("Checking...");
    flushNow()
      .then(() => api.healthLastfmUser())
      .then((r) => setUserResult((r.ok ? "OK - " : "Failed - ") + (r.message || "")))
      .catch(() => setUserResult("Check failed."));
  }

  // Run a plugin's health check (e.g. validate the Last.fm cookie). Saves the
  // current form first so the test uses the values just typed.
  function testPlugin(kind: string, key: string) {
    setPluginTest((p) => ({ ...p, [key]: "Checking..." }));
    flushNow()
      .then(() => api.pluginTest(kind, key))
      .then((r) => setPluginTest((p) => ({ ...p, [key]: (r.ok ? "OK - " : "Failed - ") + (r.message || "") })))
      .catch(() => setPluginTest((p) => ({ ...p, [key]: "Check failed." })));
  }

  // Kept for the toggles that used to save themselves; set() does that now.
  const saveOne = set;

  // Kick a scan for one library plugin. Persists config first (so the scan uses
  // values just typed), then the LibraryTab query polls progress until done.
  function scanLibrary(key: string, quick: boolean) {
    setScanResults((p) => ({ ...p, [key]: "Starting..." }));
    flushNow()
      .then(() => api.libraryScan(key, quick))
      .then((r) => {
        const message = r.error
          ? r.error
          : r.state === "queued"
            ? `Queued behind ${r.position} scan${r.position === 1 ? "" : "s"}.`
            : r.state === "duplicate"
              ? "Already running or queued."
              : "Scan started.";
        setScanResults((p) => ({ ...p, [key]: message }));
        qc.invalidateQueries({ queryKey: ["scanQueue"] });
        qc.invalidateQueries({ queryKey: ["plugins", "library"] });
      })
      .catch(() => setScanResults((p) => ({ ...p, [key]: "Scan failed to start." })));
  }

  function purge() {
    if (!window.confirm("Delete stored data for artists you no longer follow? Everything for artists you follow or have ignored is kept.")) return;
    setPurgeResult("Purging...");
    api.cachePurge().then((r: any) => {
      setPurgeResult(`Freed ${fmtBytes(r.freed_bytes)} (${r.art_files_removed} images, ${r.json_entries_removed} records).`);
      loadCacheStats();
    }).catch(() => setPurgeResult("Purge failed."));
  }

  function copyCalendar() {
    navigator.clipboard
      .writeText(calendarUrl)
      .then(() => setCalendarResult("Copied."))
      .catch(() => setCalendarResult("Copy failed - select the text instead."));
    setTimeout(() => setCalendarResult(""), 3000);
  }
  function resetCalendar() {
    if (!window.confirm("Issue a new calendar link? Existing subscriptions will stop updating.")) return;
    api.calendarReset().then((r) => {
      setCalendarUrl(r.url);
      setCalendarResult("New link issued.");
      setTimeout(() => setCalendarResult(""), 4000);
    });
  }

  function compactDb() {
    setCompactResult("Compacting...");
    api
      .dbCompact()
      .then((r) =>
        setCompactResult(
          r.freed_bytes
            ? `Freed ${fmtBytes(r.freed_bytes)}; database is now ${fmtBytes(r.size_bytes)}.`
            : `Nothing to reclaim; database is ${fmtBytes(r.size_bytes)}.`,
        ),
      )
      .catch(() => setCompactResult("Compacting failed."));
  }

  function downloadBackup() {
    const sections = Array.from(backupSections);
    if (!sections.length) return;
    window.location.href = "/api/backup?sections=" + encodeURIComponent(sections.join(","));
  }

  function doImport() {
    const file = fileRef.current?.files?.[0];
    if (!file) { setImportResult("Choose a backup file first."); return; }
    if (!window.confirm("Importing replaces ALL current settings and data with this backup. Continue?")) return;
    const sections = Array.from(importSections);
    if (!sections.length) { setImportResult("Pick at least one section."); return; }
    setImportResult("Importing...");
    const fd = new FormData();
    fd.append("file", file);
    fd.append("sections", sections.join(","));
    fetch("/api/import", { method: "POST", body: fd })
      .then((res) => res.json())
      .then((r) => {
        if (r.error) { setImportResult(r.error); return; }
        const c = r.imported || {};
        const parts: string[] = [];
        if ("settings" in c) parts.push(c.settings + " settings");
        if ("artists" in c) parts.push(c.artists + " artists");
        if ("releases" in c) parts.push(c.releases + " releases");
        if ("artwork" in c) parts.push(c.artwork + " artwork files");
        setImportResult(`Imported ${parts.join(", ") || "nothing"}. Reloading...`);
        setTimeout(() => window.location.reload(), 1000);
      })
      .catch(() => setImportResult("Import failed."));
  }

  // --- nav drag reorder ---
  function onDragOver(e: React.DragEvent, overKey: string) {
    e.preventDefault();
    const from = dragKey.current;
    if (!from || from === overKey) return;
    setNavRows((prev) => {
      const rows = [...prev];
      const fromIdx = rows.findIndex((r) => r.key === from);
      const toIdx = rows.findIndex((r) => r.key === overKey);
      if (fromIdx < 0 || toIdx < 0) return prev;
      const [moved] = rows.splice(fromIdx, 1);
      rows.splice(toIdx, 0, moved);
      return rows;
    });
  }
  function toggleShow(key: string, checked: boolean) {
    setNavRows((prev) => prev.map((r) => (r.key === key ? { ...r, show: checked } : r)));
  }

  function toggleSet(setter: typeof setBackupSections, value: string, checked: boolean) {
    setter((prev) => {
      const next = new Set(prev);
      if (checked) next.add(value); else next.delete(value);
      return next;
    });
  }

  const beforeRelease = get("webhook_trigger") === "before_release";
  return (
    <div>
      {/* Header: what page this is, and whether the last change made it to
          the server. */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="flex items-center gap-2 text-2xl font-bold">
          <LuSettings className="size-6 text-muted-foreground" />
          Settings
        </h1>
        <div className="flex flex-wrap items-center gap-2">
          {/* Changes save themselves; this is the only thing that reports it. */}
          <span className="text-sm text-muted-foreground">
            {saveResult || "Changes save automatically"}
          </span>
        </div>
      </div>

      {/* Sidebar + pane, the shape a settings page this long wants: the
          sections stay visible instead of scrolling past as one column. */}
      <div className="flex flex-col gap-4 md:flex-row md:items-start">
        <Card className="md:w-56 md:shrink-0">
          <CardContent className="p-2">
            <nav className="flex flex-row flex-wrap gap-1 md:flex-col">
              {SECTIONS.map((s) => (
                <Button
                  key={s.key}
                  variant={section === s.key ? "secondary" : "ghost"}
                  size="sm"
                  className="justify-start gap-2 md:w-full"
                  aria-current={section === s.key ? "page" : undefined}
                  onClick={() => changeSection(s.key)}
                >
                  <s.icon className="size-4 text-muted-foreground" />
                  {s.label}
                </Button>
              ))}
            </nav>
          </CardContent>
        </Card>

        <div className="min-w-0 flex-1">

        {section === "help" && (
          <>
  <HelpInfo />
          </>
        )}

        {section === "general" && (
          <>
        <Section>
          <Legend>Appearance</Legend>
          <Label htmlFor="default_theme">Default theme</Label>
          <Select value={get("default_theme") || "dark"} onValueChange={(v) => set("default_theme", v)}>
            <SelectTrigger id="default_theme" className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="dark">Dark (AMOLED)</SelectItem>
              <SelectItem value="light">Light</SelectItem>
            </SelectContent>
          </Select>
          <Hint>The toggle in the header overrides this per device.</Hint>
          <SubHeading note="How much explaining the pages do.">Page text</SubHeading>
          <Check checked={onIfTrue("hide_page_descriptions")} onChange={(c) => set("hide_page_descriptions", c ? "true" : "false")}>
            Hide page descriptions
          </Check>
          <Hint>Hides the short intro text under the title on the Discover, Upcoming and Following pages.</Hint>
        </Section>

        <Section>
          <Legend>Navigation &amp; home page</Legend>
          <Hint>Drag the tabs to reorder. The tab at the top is your home page.</Hint>
          <div className="flex flex-col gap-2">
            {navRows.map((row, i) => {
              const isHome = i === 0;
              const locked = isHome || row.key === "settings";
              return (
                <div
                  key={row.key}
                  className={"flex cursor-grab items-center gap-3 rounded-md border px-2 py-2 " + (isHome ? "border-green-500" : "")}
                  draggable
                  onDragStart={() => { dragKey.current = row.key; }}
                  onDragEnd={() => { dragKey.current = null; }}
                  onDragOver={(e) => onDragOver(e, row.key)}
                >
                  <span className="select-none text-muted-foreground">⋮⋮</span>
                  <span className="flex-1 font-semibold">{row.label}</span>
                  {isHome && <span className="text-xs uppercase text-green-500">home</span>}
                  {row.key !== "settings" && (
                    <label className="flex cursor-pointer items-center gap-2 text-sm">
                      <Checkbox checked={isHome ? true : row.show} disabled={locked} onCheckedChange={(c) => toggleShow(row.key, c === true)} />
                      <span>Show</span>
                    </label>
                  )}
                </div>
              );
            })}
          </div>
        </Section>

          </>
        )}

        {section === "library" && (
          <>
        <Section>
          <Legend>Artist monitoring</Legend>
          <SubHeading first note="How often the app looks for new releases.">
            Refreshing
          </SubHeading>
          <Label htmlFor="check_interval_hours">Refresh interval (hours)</Label>
          <Input id="check_interval_hours" type="number" min={0.25} step={0.25} value={get("check_interval_hours")} onChange={(e) => set("check_interval_hours", e.target.value)} />
          <Hint>How often followed artists are checked for new releases in the background (default 12).</Hint>

          <Label htmlFor="artist_refresh_timeout">Per-artist refresh timeout (seconds)</Label>
          <Input id="artist_refresh_timeout" type="number" min={30} step={30} value={get("artist_refresh_timeout")} onChange={(e) => set("artist_refresh_timeout", e.target.value)} />
          <Hint>Give up on a single artist's refresh after this long, so one slow artist can't stall the queue (default 180).</Hint>

          <SubHeading note="Applied to artists you follow from now on.">
            What to watch
          </SubHeading>
          <Label>Release types to monitor for newly followed artists</Label>
          <div className="mt-1 flex flex-wrap gap-4">
            {MTYPES.map(([v, l]) => (
              <Check key={v} checked={csvHas("default_monitor_types", v)} onChange={(c) => toggleCsv("default_monitor_types", v, c)}>{l}</Check>
            ))}
          </div>
          <Hint>Change per-artist on each artist's page. New follows start with this.</Hint>

          <Label>Auto-hide these categories on artist pages</Label>
          <div className="mt-1 flex flex-wrap gap-4">
            {MTYPES.map(([v, l]) => (
              <Check key={v} checked={csvHas("discography_autohide", v)} onChange={(c) => toggleCsv("discography_autohide", v, c)}>{l}</Check>
            ))}
          </div>
          <Hint>Collapsed by default when you open an artist; you can still expand them per page.</Hint>

          <SubHeading note="What counts as already having a record.">
            EPs and singles
          </SubHeading>
          <div className="mt-3">
            <Check
              checked={get("own_covered_releases") === "true"}
              onChange={(c) => set("own_covered_releases", c ? "true" : "false")}
            >
              Count an EP or single as owned when you already have every song
            </Check>
            <Hint>
              A single is usually one album track with a new cover. With this on, a
              release whose every song the library already has -- wherever it keeps
              them -- stops counting as missing. Uses the same tracklists as the tag
              below.
            </Hint>
          </div>

          <div className="mt-3">
            <Check
              checked={get("show_unique_tags") !== "false"}
              onChange={(c) => set("show_unique_tags", c ? "true" : "false")}
            >
              Tag EPs and singles with their unique song count
            </Check>
            <Hint>
              Marks how many songs a release carries that your library hasn't got, so
              a single you effectively already own is obvious. Reads one tracklist per
              release in the background the first time you open an artist; off means
              those lookups never happen.
            </Hint>
          </div>
        </Section>


        <Section>
          <Legend>Get Hyped playlist</Legend>
          <HypePlaylist get={get} set={set} />
        </Section>

  <LibraryTab
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
            scanResults={scanResults}
            onScan={scanLibrary}
          />
          </>
        )}

        {section === "discovery" && (
          <>
        <Section>
          <Legend>Discover feed</Legend>
          <Label htmlFor="discover_refresh_hours">Refresh interval (hours)</Label>
          <Input id="discover_refresh_hours" type="number" min={1} step={1} value={get("discover_refresh_hours")} onChange={(e) => set("discover_refresh_hours", e.target.value)} />
          <Hint>How often the Discover scrape refreshes in the background (default 24).</Hint>

          <Label htmlFor="discover_enrich_workers">Enrichment workers</Label>
          <Input id="discover_enrich_workers" type="number" min={1} max={16} step={1} value={get("discover_enrich_workers")} onChange={(e) => set("discover_enrich_workers", e.target.value)} />
          <Hint>Parallel threads fetching art/genres per release (default 8, max 16). Higher = faster Metacritic refresh.</Hint>

          <Label htmlFor="discover_history_months">History (months)</Label>
          <Input id="discover_history_months" type="number" min={0} step={1} value={get("discover_history_months")} onChange={(e) => set("discover_history_months", e.target.value)} />
          <Hint>Keep previously-found releases on the Discover page for this many months after their release date (default 3). 0 = only current and upcoming.</Hint>
        </Section>


        <PluginsTab
            show={["discovery"]}
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
          />

        {/* Only in play when a source gets blocked, and configured once. */}
        <FoldedCard title="Challenge solvers">
          <PluginsTab
            show={["solver"]}
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
          />
        </FoldedCard>
          </>
        )}

        {section === "metadata" && (
          <>
        <Section>
          <Legend>Where information comes from</Legend>
          <Hint>
            MusicBrainz decides what a release is; these sources fill in
            everything around it. Each field below is answered by the first
            source in its list that has an answer, so put the one you trust at
            the top. A new service only needs a plugin file under
            app/plugins/metadata to appear here.
          </Hint>
          <MetadataOrder />
        </Section>

        <Section>
          <Legend>Find missing metadata</Legend>
          <MetadataGaps />
        </Section>

        {/* The sources themselves: keys, toggles and tests. Folded away,
            because ordering them is the daily job and setting them up is
            something you do once. */}
        <FoldedCard title="Set up the sources">
          <PluginsTab
            show={["metadata"]}
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
          />
        </FoldedCard>
          </>
        )}

        {section === "downloads" && (
          <>
        <QualityTab get={get} set={set} />

        <FoldedCard title="Set up the download clients">
          <PluginsTab
            show={["downloader"]}
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
          />
        </FoldedCard>
          </>
        )}

        {section === "notifications" && (
          <>
        <Section>
          <Legend>When to announce a release</Legend>
          <Hint>
            Applies to every channel below: an artist set to <strong>Notify</strong>
            triggers one announcement per release, either the moment it's found or
            as its release date approaches.
          </Hint>
          <Label htmlFor="webhook_trigger">When to trigger</Label>
          <Select value={get("webhook_trigger") || "discovery"} onValueChange={(v) => set("webhook_trigger", v)}>
            <SelectTrigger id="webhook_trigger" className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="discovery">As soon as a release is discovered</SelectItem>
              <SelectItem value="before_release">A set time before the release date</SelectItem>
            </SelectContent>
          </Select>

          {beforeRelease && (
            <div className="mt-2">
              <Label htmlFor="webhook_lead_value">Lead time before release</Label>
              <div className="flex items-center gap-2">
                <Input id="webhook_lead_value" className="w-24" type="number" min={0} step={1} value={get("webhook_lead_value")} onChange={(e) => set("webhook_lead_value", e.target.value)} />
                <Select value={get("webhook_lead_unit") || "days"} onValueChange={(v) => set("webhook_lead_unit", v)}>
                  <SelectTrigger id="webhook_lead_unit" className="w-auto"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {["hours", "days", "weeks"].map((u) => <SelectItem key={u} value={u}>{u}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <Hint>e.g. 7 days before release. Use 0 to fire on the release date.</Hint>
            </div>
          )}
        </Section>

        <PluginsTab
            show={["notifier"]}
            get={get}
            set={set}
            testResult={pluginTest}
            onTest={testPlugin}
          />

        {/* The generic escape hatch: anything without a plugin of its own --
            Home Assistant, n8n, a script -- takes a webhook. */}
        <FoldedCard title="Custom webhook">
          <Hint>
            For anything that isn't one of the services above: your own endpoint,
            with your own headers and JSON body. Fires on the same schedule.
          </Hint>
          <Label htmlFor="webhook_url">Webhook URL</Label>
          <Input id="webhook_url" placeholder="https://..." value={get("webhook_url")} onChange={(e) => set("webhook_url", e.target.value)} />

          <Label htmlFor="webhook_method">Method</Label>
          <Select value={get("webhook_method") || "POST"} onValueChange={(v) => set("webhook_method", v)}>
            <SelectTrigger id="webhook_method" className="w-auto"><SelectValue /></SelectTrigger>
            <SelectContent>
              {["POST", "PUT", "GET"].map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}
            </SelectContent>
          </Select>

          <Label htmlFor="webhook_headers">Headers (JSON object or "Key: Value" per line)</Label>
          <Textarea id="webhook_headers" className="font-mono" rows={3} value={get("webhook_headers")} onChange={(e) => set("webhook_headers", e.target.value)} />

          <Label htmlFor="webhook_template">Body template (JSON, supports {"{artist} {title} {release_date} {type} {image_url}"})</Label>
          <Textarea id="webhook_template" className="font-mono" rows={8} placeholder={navTpl?.default_webhook_template} value={get("webhook_template")} onChange={(e) => set("webhook_template", e.target.value)} />
          <Hint>Leave blank to use the built-in default payload.</Hint>

          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={testWebhook}>Send test webhook</Button>
            <span className="text-muted-foreground">{webhookResult}</span>
          </div>
        </FoldedCard>

          </>
        )}

        {section === "apis" && (
          <>
        <Section>
          <Legend>Keys &amp; accounts</Legend>
          <Hint>
            Credentials the rest of the app borrows. What each source is used
            for, and in what order, is set under Metadata.
          </Hint>
          <SubHeading note="Bios, images, tags, similar artists, your scrobbles.">
            Last.fm
          </SubHeading>
          <Label htmlFor="lastfm_api_key">Last.fm API key</Label>
          <Secret id="lastfm_api_key" value={get("lastfm_api_key")} onChange={(v) => set("lastfm_api_key", v)} />
          <Hint>
            Used for artist bios and images. Get one at last.fm/api.{" "}
            <Button size="xs" variant="outline" onClick={testApiKey}>Test key</Button>{" "}
            <span className="text-muted-foreground">{keyResult}</span>
          </Hint>

          <Label htmlFor="lastfm_username">Last.fm username</Label>
          <Secret id="lastfm_username" value={get("lastfm_username")} onChange={(v) => set("lastfm_username", v)} />
          <Hint>
            Optional. Adds a Discover tab of the artists you actually play, with the
            ones missing from your library called out.{" "}
            <Button size="xs" variant="outline" onClick={testLastfmUser}>Test user</Button>{" "}
            <span className="text-muted-foreground">{userResult}</span>
          </Hint>

          <SubHeading note="The release data everything else hangs off.">
            MusicBrainz
          </SubHeading>
          <Label htmlFor="musicbrainz_contact">MusicBrainz contact (email or URL)</Label>
          <Secret id="musicbrainz_contact" value={get("musicbrainz_contact")} onChange={(v) => set("musicbrainz_contact", v)} />
          <Hint>Sent in the MusicBrainz User-Agent, as their API etiquette requests.</Hint>

          <Label htmlFor="musicbrainz_rate_limit_ms">MusicBrainz rate limit (ms between requests)</Label>
          <Input id="musicbrainz_rate_limit_ms" type="number" min={1000} step={100} value={get("musicbrainz_rate_limit_ms")} onChange={(e) => set("musicbrainz_rate_limit_ms", e.target.value)} />
          <Hint>Minimum gap between MusicBrainz calls. Kept at 1000ms or more.</Hint>
        </Section>

          </>
        )}

        {section === "maintenance" && (
          <>
        <Section>
          <Legend>Stored data</Legend>
          <SubHeading first note="What the app keeps on disk, and for how long.">
            What to keep
          </SubHeading>
          <Check checked={onUnlessFalse("cache_images")} onChange={(c) => saveOne("cache_images", c ? "true" : "false")}>Keep images on disk</Check>
          <Hint>
            Album art and artist pictures are saved under the data folder and stay
            there: instant to load, available offline, and unaffected by a restart.
            Turning this off makes every page fetch them from the internet again.
          </Hint>
          <Check checked={onUnlessFalse("purge_cache_on_unfollow")} onChange={(c) => saveOne("purge_cache_on_unfollow", c ? "true" : "false")}>
            Delete an artist's data when you unfollow them
          </Check>
          <Hint>
            Removes their discography, album details, tracked releases and pictures as
            soon as you unfollow. Artists you ignore keep everything, so their page
            still opens instantly.
          </Hint>
          <SubHeading note="0 means forever, which is usually what you want.">
            How long scraped answers last
          </SubHeading>
          <Label htmlFor="store_keep_days">Keep scraped data for (days)</Label>
          <Input id="store_keep_days" type="number" min={0} step={1}
                 value={get("store_keep_days")}
                 onChange={(e) => set("store_keep_days", e.target.value)} />
          <Hint>
            How long anything read from MusicBrainz, Last.fm, iTunes or Deezer
            stays usable: discographies, tracklists, covers, bios, top tracks,
            similar artists, previews. <strong>0 means forever</strong>, which is the
            point of storing it - the more you browse, the less the app goes online
            and the faster pages open. New releases are still found either way: the
            refresh cycle re-reads a followed artist on its own interval.
          </Hint>

          <Label htmlFor="store_miss_days">Retry a dead end after (days)</Label>
          <Input id="store_miss_days" type="number" min={0} step={1}
                 value={get("store_miss_days")}
                 onChange={(e) => set("store_miss_days", e.target.value)} />
          <Hint>
            "Nothing found" is stored too, so the same fruitless lookup isn't repeated
            all week - but for less time than a hit, since a release nobody had
            indexed yet may turn up. 0 remembers a dead end forever.
          </Hint>

          <Label htmlFor="store_marker_days">Re-check library track matches after (days)</Label>
          <Input id="store_marker_days" type="number" min={0} step={1}
                 value={get("store_marker_days")}
                 onChange={(e) => set("store_marker_days", e.target.value)} />
          <Hint>
            How long "this server has that track" is trusted, which drives the play
            buttons and the unique tags. Shorter notices deleted music sooner;
            pressing play always looks again for real, so a stale yes costs nothing.
          </Hint>

          <Label htmlFor="store_scrobble_hours">Re-read your scrobbles after (hours)</Label>
          <Input id="store_scrobble_hours" type="number" min={0} step={1}
                 value={get("store_scrobble_hours")}
                 onChange={(e) => set("store_scrobble_hours", e.target.value)} />
          <Hint>Your own Last.fm listening on the Discover page - the one thing here that changes hourly.</Hint>

          <SubHeading note="Reclaiming space, once you've decided what to drop.">
            Clearing up
          </SubHeading>
          <p className="mt-2 mb-2 text-muted-foreground">{cacheStats}</p>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={purge} disabled={!staleBytes}>Delete unfollowed artists' data</Button>
            <span className="text-muted-foreground">{purgeResult}</span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <Button variant="outline" onClick={compactDb}>Compact database</Button>
            <span className="text-muted-foreground">{compactResult}</span>
          </div>
          <Hint>Rewrites the database file to give back space left by anything deleted.</Hint>
        </Section>

        {/* Its own card: a background pass with its own progress, not a
            setting about stored data. */}
        <Section>
          <ArtworkSection />
        </Section>

        <Section>
          <Legend>Release calendar</Legend>
          <Hint>
            Subscribe to this URL in any calendar app to see upcoming releases for
            the artists you follow. Anyone with the link can read it, so reset it if
            it leaks.
          </Hint>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Input readOnly value={calendarUrl} onFocus={(e) => e.currentTarget.select()}
                   className="min-w-[18rem] flex-1 font-mono text-xs" />
            <Button variant="outline" onClick={copyCalendar}>Copy</Button>
            <Button variant="outline" onClick={resetCalendar}>Reset link</Button>
            <span className="text-muted-foreground">{calendarResult}</span>
          </div>
        </Section>

        <Section>
          <Legend>Backup &amp; restore</Legend>
          <Hint>A backup is a ZIP file. Choose what to include (default: everything).</Hint>
          <div className="flex flex-wrap gap-4">
            {[["settings", "Settings"], ["artists", "Artist information"], ["artwork", "Artwork"]].map(([v, l]) => (
              <Check key={v} checked={backupSections.has(v)} onChange={(c) => toggleSet(setBackupSections, v, c)}>{l}</Check>
            ))}
          </div>
          <div className="my-3"><Button variant="outline" onClick={downloadBackup}>Download backup</Button></div>

          <Label htmlFor="backup-file">Restore from a backup file</Label>
          <div className="mt-1"><input ref={fileRef} id="backup-file" type="file" accept=".zip,application/zip,application/json,.json" /></div>
          <div className="mt-2 flex flex-wrap gap-4">
            {[["settings", "Settings"], ["artists", "Artist information"], ["artwork", "Artwork"]].map(([v, l]) => (
              <Check key={v} checked={importSections.has(v)} onChange={(c) => toggleSet(setImportSections, v, c)}>{l}</Check>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button variant="outline" onClick={doImport}>Import &amp; replace</Button>
            <span className="text-muted-foreground">{importResult}</span>
          </div>
          <Hint>Only the ticked sections that exist in the file are restored. Older JSON backups still work.</Hint>
        </Section>
          </>
        )}

        </div>
      </div>
    </div>
  );
}


function PluginsTab({
  show,
  get,
  set,
  testResult,
  onTest,
}: {
  // Which plugin kinds this instance renders -- the settings panes each show
  // one part of the registry rather than the whole thing at once.
  show: string[];
  get: (k: string) => string;
  set: (k: string, v: string) => void;
  testResult: Record<string, string>;
  onTest: (kind: string, key: string) => void;
}) {
  const wants = (kind: string) => show.includes(kind);
  // Plugin metadata (labels, config fields, last-scraped, refreshing) via
  // TanStack Query. While any source is mid-scrape, poll so "last scraped" and
  // the spinner update on their own, then stop.
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["plugins", "discovery"],
    queryFn: () => api.plugins("discovery"),
    staleTime: 60_000,
    refetchInterval: (query) =>
      query.state.data?.plugins?.some((p) => p.refreshing) ? 3000 : false,
  });
  const plugins = data?.plugins ?? [];
  const { data: downloaderData } = useQuery({
    queryKey: ["plugins", "downloader"],
    queryFn: () => api.plugins("downloader"),
  });
  const downloaderPlugins = downloaderData?.plugins ?? [];

  const { data: solverData } = useQuery({
    queryKey: ["plugins", "solver"],
    queryFn: () => api.plugins("solver"),
  });
  const solverPlugins = solverData?.plugins ?? [];

  const { data: notifierData } = useQuery({
    queryKey: ["plugins", "notifier"],
    queryFn: () => api.plugins("notifier"),
  });
  const notifierPlugins = notifierData?.plugins ?? [];

  const { data: metadataData } = useQuery({
    queryKey: ["plugins", "metadata"],
    queryFn: () => api.plugins("metadata"),
  });
  const metadataPlugins = metadataData?.plugins ?? [];

  function refresh(kind: string, key: string) {
    api.pluginRefresh(kind, key).finally(() => refetch());
  }
  // Whether the plugin is on right now reflects the live form value, not the
  // (possibly cached) `enabled` from the query, so toggling shows config at once.
  const isOn = (p: PluginInfo) => !p.enabled_setting || get(p.enabled_setting) !== "false";

  return (
    <div>
      {wants("discovery") && (
      <>
      <Hint>
        Discovery plugins are the new-release sources on the Discover page. Turn one
        off to stop scraping it. Configure each below; changes save themselves.
      </Hint>

      {isLoading ? (
        <p className="text-muted-foreground">Loading plugins...</p>
      ) : isError ? (
        <p className="text-muted-foreground">Could not load plugins.</p>
      ) : plugins.length === 0 ? (
        <p className="text-muted-foreground">No plugins available.</p>
      ) : (
        plugins.map((p) => {
          const on = isOn(p);
          return (
            <Section key={p.key}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Legend>
                    <span className="flex items-center gap-2">
                      <ServiceIcon name={p.icon} size={18} />
                      {p.label}
                    </span>
                  </Legend>
                  {p.description && (
                    <p className="mb-1 text-sm text-muted-foreground">{p.description}</p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <div className="flex items-center gap-3">
                    {p.has_test && (
                      <Button size="xs" variant="outline" onClick={() => onTest(p.kind, p.key)}>
                        Test
                      </Button>
                    )}
                    {p.refreshable && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                        aria-label={`Refresh ${p.label}`}
                        size="icon-xs"
                        variant="outline"
                        disabled={!!p.refreshing}
                        onClick={() => refresh(p.kind, p.key)}
                      >
                        <LuRotateCw className={p.refreshing ? "animate-spin" : undefined} />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Refresh {p.label}</TooltipContent>
                      </Tooltip>
                    )}
                    {p.enabled_setting && (
                      <Check checked={on} onChange={(c) => set(p.enabled_setting!, c ? "true" : "false")}>
                        Enabled
                      </Check>
                    )}
                  </div>
                  {p.has_test && testResult[p.key] && (
                    <p className="text-right text-sm text-muted-foreground">{testResult[p.key]}</p>
                  )}
                  {p.refreshable && (
                    <p className="text-right text-xs text-muted-foreground">
                      {p.refreshing
                        ? "Scraping..."
                        : `Last scraped: ${fmtAgo(p.last_scraped)}`}
                      {p.error
                        ? ` · ${p.error}`
                        : p.item_count != null
                          ? ` · ${p.item_count} releases`
                          : ""}
                    </p>
                  )}
                </div>
              </div>

              {on && <PluginFields fields={p.config_fields} get={get} set={set} />}
            </Section>
          );
        })
      )}
      <SimilarScanSection />
      </>
      )}

      {wants("downloader") && (
      <>
      <h2 className="mb-1 text-lg font-semibold">Downloaders</h2>
      <Hint>
        What the Grab buttons on the Missing and album pages send a release to. The
        client searches for the release itself and downloads it.
      </Hint>
      {downloaderPlugins.map((p) => (
        <PluginCard key={p.key} p={p} on={isOn(p)} get={get} set={set}
                    testResult={testResult} onTest={onTest} />
      ))}

      </>
      )}

      {wants("solver") && (
      <>
      <h2 className="mb-1 text-lg font-semibold">Challenge solvers</h2>
      <Hint>
        Shared by whichever source gets blocked - today that's Album of the Year,
        which sits behind Cloudflare and refuses plain server-side fetches. A solver
        runs a real browser elsewhere, fetches the page and hands back the clearance
        cookie, so those sources keep working without you pasting a cookie every
        time one expires. Set it up once; the sources that need it just use it.
      </Hint>
      {solverPlugins.map((p) => (
        <PluginCard key={p.key} p={p} on={isOn(p)} get={get} set={set}
                    testResult={testResult} onTest={onTest} />
      ))}

      </>
      )}

      {wants("metadata") && (
      <>
      <Hint>
        Every source the lists above can draw on. Switching one off takes it out
        of every list; the tags under each name are the fields it can answer.
      </Hint>
      {metadataPlugins.map((p) => (
        <PluginCard key={p.key} p={p} on={isOn(p)} get={get} set={set}
                    testResult={testResult} onTest={onTest} />
      ))}
      </>
      )}

      {wants("notifier") && (
      <>
      <h2 className="mb-1 text-lg font-semibold">Notification services</h2>
      <Hint>
        Where releases and grabs get announced. Each service picks which events it
        wants, so one can carry everything and another only what you act on.
      </Hint>
      {notifierPlugins.map((p) => (
        <PluginCard key={p.key} p={p} on={isOn(p)} get={get} set={set}
                    testResult={testResult} onTest={onTest} />
      ))}
      </>
      )}


    </div>
  );
}

// "· next in 4h" for a library on a schedule, nothing for one that only scans
// when asked.
function scheduleNote(p: PluginInfo): string {
  const hours = p.scan_interval_hours ?? 0;
  if (!hours) return "";
  if (!p.last_scanned) return " · scheduled";
  const left = Math.round(p.last_scanned + hours * 3600 - Date.now() / 1000);
  if (left <= 0) return " · due now";
  if (left < 3600) return ` · next in ${Math.max(1, Math.round(left / 60))}m`;
  if (left < 86400) return ` · next in ${Math.round(left / 3600)}h`;
  return ` · next in ${Math.round(left / 86400)}d`;
}

// Artist pictures are served from disk; anything not yet fetched is downloaded
// while a page waits for it, so this reports the gap and fills it.
function ArtworkSection() {
  const { data, refetch } = useQuery({
    queryKey: ["artworkStatus"],
    queryFn: () => api.artworkStatus(),
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });
  const [busy, setBusy] = useState(false);
  function warm(force: boolean) {
    setBusy(true);
    api.artworkWarm(force).finally(() => { setBusy(false); refetch(); });
  }
  function stop() {
    api.artworkWarmStop().finally(() => refetch());
  }
  return (
    <div className="mt-4">
      <Legend>Artist artwork</Legend>
      <p className="mb-2 text-muted-foreground">
        {data
          ? data.running
            ? `Fetching artwork: ${data.done}/${data.total} artists, ${data.warmed} downloaded${data.filled ? `, ${data.filled} found for artists that had none` : ""}...`
            : `${data.followed} followed artists · ${data.on_disk} of ${data.with_image} pictures saved on disk${data.missing_image ? ` · ${data.missing_image} have none` : ""}.`
          : "Checking..."}
      </p>
      {data?.running && !!data.total && (
        <Progress
          className="mb-2"
          value={Math.round((data.done / Math.max(data.total, 1)) * 100)}
        />
      )}
      {!data?.running && !!data?.with_image && (
        // How much of the library's artwork is already on disk.
        <Progress
          className="mb-2"
          value={Math.round((data.on_disk / Math.max(data.with_image, 1)) * 100)}
        />
      )}
      {!data?.running && data?.message && (
        <p className="mb-2 text-sm text-muted-foreground">{data.message}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {data?.running ? (
          <Button variant="outline" onClick={stop}>Stop</Button>
        ) : (
          <>
            <Button variant="outline" disabled={busy} onClick={() => warm(false)}>
              Download missing artwork
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => warm(true)}>
              Re-fetch everything
            </Button>
          </>
        )}
      </div>
      <Hint>
        Pages show a thumbnail per artist, and anything not already saved is
        downloaded while the page waits for it. This fetches them all ahead of time
        and keeps them, and finds a sleeve for artists Last.fm has no photo of.
      </Hint>
    </div>
  );
}

// Version, paths and what the scheduler is about to do -- the things worth
// having in front of you when something looks wrong.
function HelpInfo() {
  const { data, isError } = useQuery({
    queryKey: ["system"],
    queryFn: () => api.system(),
    refetchInterval: 30_000,
  });

  if (isError) return <Section><p className="text-muted-foreground">Could not read system info.</p></Section>;
  if (!data) return <Section><p className="text-muted-foreground">Loading...</p></Section>;

  const rows: [string, React.ReactNode][] = [
    ["Version", data.version],
    ["Database", `${data.database_path} (${fmtBytes(data.database_bytes)})`],
    ["Artwork cache", data.artwork_path],
    ["Music directory", data.music_directory || "not set"],
    ["Platform", data.platform],
    ["Python", data.python],
    ["SQLite", data.sqlite],
    ["Timezone", data.timezone],
    ["Running since", fmtAgo(data.started_at)],
  ];

  return (
    <>
      <Section>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <Legend>Simple Music Tracker</Legend>
          <Badge variant="secondary">v{data.version}</Badge>
        </div>
        <div className="mb-3 flex flex-wrap gap-4 text-sm">
          <span><strong>{data.counts.artists.toLocaleString()}</strong> artists</span>
          <span><strong>{data.counts.following.toLocaleString()}</strong> followed</span>
          <span>
            <strong>{data.counts.owned_albums.toLocaleString()}</strong> owned albums
            {data.libraries.length > 0 && (
              <> across <strong>{data.libraries.length}</strong>{" "}
                {data.libraries.length === 1 ? "library" : "libraries"}</>
            )}
          </span>
          <span><strong>{data.counts.releases.toLocaleString()}</strong> tracked releases</span>
        </div>
        {data.libraries.length > 0 && (
          <ul className="mb-3 space-y-1 text-sm text-muted-foreground">
            {data.libraries.map((lib) => (
              <li key={lib.key}>
                {lib.label}: {lib.albums.toLocaleString()} albums ·{" "}
                {lib.artists.toLocaleString()} artists
                {lib.tracks ? ` · ${lib.tracks.toLocaleString()} tracks` : ""}
                {lib.last_scanned ? ` · scanned ${fmtAgo(lib.last_scanned)}` : " · never scanned"}
                {lib.scan_interval_hours
                  ? ` · every ${lib.scan_interval_hours >= 24
                      ? `${Math.round(lib.scan_interval_hours / 24)}d`
                      : `${lib.scan_interval_hours}h`}`
                  : " · manual"}
              </li>
            ))}
          </ul>
        )}
        <Separator className="my-3" />
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[12rem_1fr]">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-muted-foreground">{label}</dt>
              <dd className="break-all font-mono text-xs sm:text-sm">{value}</dd>
            </div>
          ))}
        </dl>
        <Separator className="my-3" />
        <div className="flex flex-wrap gap-3 text-sm">
          {Object.entries(data.links).map(([name, url]) => (
            <a key={name} href={url} target="_blank" rel="noopener noreferrer"
               className="capitalize hover:underline">
              {name}
            </a>
          ))}
        </div>
      </Section>

      <Section>
        <Legend>Scheduled tasks</Legend>
        <Hint>
          Background jobs and when they next run. Intervals come from the settings in
          the other panes; a job switched off there shows as inactive here.
        </Hint>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Task</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Interval</TableHead>
                <TableHead>Last run</TableHead>
                <TableHead>Next run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.tasks.map((t) => (
                <TableRow key={t.key}>
                  <TableCell>{t.label}</TableCell>
                  <TableCell>
                    <Badge variant={t.active ? "secondary" : "outline"}
                           className={t.active ? "" : "text-muted-foreground"}>
                      {t.active ? "Active" : "Inactive"}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{fmtEvery(t.interval_seconds)}</TableCell>
                  <TableCell className="text-muted-foreground">{fmtAgo(t.last_run)}</TableCell>
                  <TableCell className="text-muted-foreground">
                    {t.active ? fmtIn(t.next_run) : "-"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Section>
    </>
  );
}

// "every 12 hours" / "every 30 minutes", from a count of seconds.
function fmtEvery(seconds: number): string {
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return `every ${h} hour${h === 1 ? "" : "s"}`;
  }
  const m = Math.round(seconds / 60);
  return `every ${m} minute${m === 1 ? "" : "s"}`;
}

// "in 4m" / "in 2h 5m", from a unix timestamp in the future.
function fmtIn(epoch?: number | null): string {
  if (!epoch) return "-";
  const s = Math.max(0, Math.round(epoch - Date.now() / 1000));
  if (s < 60) return "in <1m";
  const m = Math.floor(s / 60) % 60;
  const h = Math.floor(s / 3600);
  return h ? `in ${h}h ${m}m` : `in ${m}m`;
}

// An ordered, opt-in list: the chosen entries in priority order with move and
// remove controls, and whatever is left offered underneath. Used for the
// quality ladder and the download-client order -- both are "these, in this
// order, and nothing else".
function OrderedList({
  chosen,
  all,
  labels,
  onChange,
  emptyText,
}: {
  chosen: string[];
  all: string[];
  labels?: Record<string, string>;
  onChange: (next: string[]) => void;
  emptyText: string;
}) {
  const label = (key: string) => labels?.[key] ?? key;
  const rest = all.filter((item) => !chosen.includes(item));
  function move(index: number, by: number) {
    const next = [...chosen];
    const target = index + by;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  }
  return (
    <div className="mt-2">
      {chosen.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ol className="space-y-1">
          {chosen.map((item, i) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-6 text-right text-sm text-muted-foreground">{i + 1}.</span>
              <span className="flex-1">{label(item)}</span>
              <Button size="icon-xs" variant="ghost" aria-label={`Move ${label(item)} up`}
                      disabled={i === 0} onClick={() => move(i, -1)}>
                <LuChevronUp />
              </Button>
              <Button size="icon-xs" variant="ghost" aria-label={`Move ${label(item)} down`}
                      disabled={i === chosen.length - 1} onClick={() => move(i, 1)}>
                <LuChevronDown />
              </Button>
              <Button size="xs" variant="ghost" className="text-muted-foreground"
                      onClick={() => onChange(chosen.filter((x) => x !== item))}>
                Remove
              </Button>
            </li>
          ))}
        </ol>
      )}
      {rest.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">Add:</span>
          {rest.map((item) => (
            <Button key={item} size="xs" variant="outline" onClick={() => onChange([...chosen, item])}>
              {label(item)}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

// Quality profile + automatic grabbing. The ladder decides what a grab is
// allowed to take and in what order.
function QualityTab({
  get,
  set,
}: {
  get: (k: string) => string;
  set: (k: string, v: string) => void;
}) {
  const { data: profile } = useQuery({
    queryKey: ["quality"],
    queryFn: () => api.quality(),
    staleTime: 60_000,
  });
  const { data: autograb, refetch: refetchAutograb } = useQuery({
    queryKey: ["autograbStatus"],
    queryFn: () => api.autograbStatus(),
    staleTime: 30_000,
  });
  const [runResult, setRunResult] = useState("");
  const [running, setRunning] = useState(false);

  const csv = (key: string, fallback: string[]) => {
    const raw = get(key);
    if (raw === undefined || raw === "") return fallback;
    return raw.split(",").map((v) => v.trim()).filter(Boolean);
  };
  const qualities = csv("quality_order", profile?.quality_order ?? []);
  const clientLabels = Object.fromEntries((profile?.clients ?? []).map((c) => [c.key, c.label]));
  // A stored order can still name a client that no longer exists.
  const clientOrder = csv("downloader_priority", profile?.client_order ?? [])
    .filter((key) => key in clientLabels);

  function runNow() {
    setRunning(true);
    setRunResult("Looking for releases to grab...");
    api
      .autograbRun()
      .then((r) => {
        const skipped = r.skipped ? `, ${r.skipped} already had` : "";
        setRunResult(`Checked ${r.checked}, sent ${r.sent}${skipped}, failed ${r.failed}.`);
        refetchAutograb();
      })
      .catch(() => setRunResult("Run failed."))
      .finally(() => setRunning(false));
  }

  return (
    <div>
      <Section>
        <Legend>What a grab may take</Legend>
        <SubHeading first note="Best first. Anything left off is never downloaded.">
          Quality
        </SubHeading>
        <Hint>Leaving MP3 V2 off means a V2 rip is simply skipped.</Hint>
        <OrderedList
          chosen={qualities}
          all={profile?.qualities ?? []}
          onChange={(next) => set("quality_order", next.join(","))}
          emptyText="Nothing accepted yet - add at least one quality."
        />

        <SubHeading note="First client that finds the release wins.">
          Download client
        </SubHeading>
        <OrderedList
          chosen={clientOrder}
          all={(profile?.clients ?? []).map((c) => c.key)}
          labels={clientLabels}
          onChange={(next) => set("downloader_priority", next.join(","))}
          emptyText="No explicit order - clients are tried as they're registered."
        />
      </Section>

      <Section>
        <Legend>Duplicate protection</Legend>
        <Hint>
          Checked before anything is sent: what the library already owns, and what
          the download client is already holding. Artist and album names are
          compared loosely, so "Bonnie 'Prince' Billy" and "Bonnie “Prince” Billy"
          count as the same artist.
        </Hint>
        <Check checked={get("skip_in_client") !== "false"}
               onChange={(c) => set("skip_in_client", c ? "true" : "false")}>
          Skip releases the download client already has
        </Check>
        <Hint>Catches the gap between "still downloading" and "scanned into the library".</Hint>
      </Section>

      <Section>
        <Legend>Automatic grabbing</Legend>
        <Check checked={get("autograb_enabled") === "true"}
               onChange={(c) => set("autograb_enabled", c ? "true" : "false")}>
          Grab new releases automatically
        </Check>
        <Hint>
          Runs on a schedule and right after a refresh finds something, using the
          quality ladder above. Releases you already have are left alone.
        </Hint>

        <Label htmlFor="autograb_scope">Which artists</Label>
        <Select value={get("autograb_scope") || "notify"} onValueChange={(v) => set("autograb_scope", v)}>
          <SelectTrigger id="autograb_scope" className="w-auto"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="notify">Only artists set to Notify</SelectItem>
            <SelectItem value="following">Every followed artist</SelectItem>
          </SelectContent>
        </Select>
        <Hint>Release types follow each artist's own monitor settings.</Hint>

        <Label htmlFor="autograb_max_age_days">Only releases from the last (days)</Label>
        <Input id="autograb_max_age_days" type="number" min={1} step={1}
               value={get("autograb_max_age_days")}
               onChange={(e) => set("autograb_max_age_days", e.target.value)} />
        <Hint>Keeps switching this on from trying to fetch a decade of back catalogue.</Hint>

        <Label htmlFor="autograb_batch">Most releases per run</Label>
        <Input id="autograb_batch" type="number" min={1} step={1}
               value={get("autograb_batch")}
               onChange={(e) => set("autograb_batch", e.target.value)} />

        <Label htmlFor="autograb_interval_minutes">Check every (minutes)</Label>
        <Input id="autograb_interval_minutes" type="number" min={5} step={5}
               value={get("autograb_interval_minutes")}
               onChange={(e) => set("autograb_interval_minutes", e.target.value)} />

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={running} onClick={runNow}>Run now</Button>
          <span className="text-muted-foreground">
            {runResult || (autograb ? `${autograb.count} release${autograb.count === 1 ? "" : "s"} waiting` : "")}
          </span>
        </div>
        {(autograb?.pending?.length ?? 0) > 0 && (
          <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
            {autograb!.pending.slice(0, 10).map((p) => (
              <li key={`${p.artist_id}-${p.title}`}>
                {p.artist} - {p.title}
                {p.release_date ? ` (${p.release_date})` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>


    </div>
  );
}

// One plugin card: name, description, Test button, Enabled toggle and (when on)
// its config fields. Shared by the downloader and solver sections --
// discovery has its own copy because it also shows scrape progress.
// Short names for the fields a metadata source can answer, for its chips.
const PROVIDES_LABELS: Record<string, string> = {
  artist_image: "photos",
  artist_bio: "biographies",
  artist_genres: "genre tags",
  album_art: "album covers",
};

function PluginCard({
  p,
  on,
  get,
  set,
  testResult,
  onTest,
}: {
  p: PluginInfo;
  on: boolean;
  get: (k: string) => string;
  set: (k: string, v: string) => void;
  testResult: Record<string, string>;
  onTest: (kind: string, key: string) => void;
}) {
  return (
    <Section>
      <div className="flex items-start justify-between gap-3">
        <div>
          <Legend>
            <span className="flex items-center gap-2">
              <ServiceIcon name={p.icon} size={18} />
              {p.label}
            </span>
          </Legend>
          {p.description && (
            <p className="mb-1 text-sm text-muted-foreground">{p.description}</p>
          )}
          {/* Metadata sources: which fields this one can answer. */}
          {(p.provides ?? []).length > 0 && (
            <p className="mb-1 flex flex-wrap gap-1">
              {(p.provides ?? []).map((cap) => (
                <Badge key={cap} variant="outline" className="px-1 py-0 text-[10px] leading-4">
                  {PROVIDES_LABELS[cap] ?? cap}
                </Badge>
              ))}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex items-center gap-3">
            {p.has_test && on && (
              <Button size="xs" variant="outline" onClick={() => onTest(p.kind, p.key)}>
                Test
              </Button>
            )}
            {p.enabled_setting && (
              <Check checked={on} onChange={(c) => set(p.enabled_setting!, c ? "true" : "false")}>
                Enabled
              </Check>
            )}
          </div>
          {p.has_test && testResult[p.key] && (
            <p className="text-right text-sm text-muted-foreground">{testResult[p.key]}</p>
          )}
        </div>
      </div>

      {on && <PluginFields fields={p.config_fields} get={get} set={set} />}
    </Section>
  );
}

// Config inputs for one plugin card. Every kind declares its fields the same
// way (app/plugins), so discovery, libraries and downloaders share this.
// Which option a stored value selects. A number that was written as "24.0"
// still means the "24" option, and an unset value takes the first one, so the
// dropdown never renders blank.
function selectValue(stored: string, options?: { value: string; label: string }[]) {
  const opts = options ?? [];
  if (opts.some((o) => o.value === stored)) return stored;
  const asNumber = Number(stored);
  if (stored && Number.isFinite(asNumber)) {
    const hit = opts.find((o) => Number(o.value) === asNumber);
    if (hit) return hit.value;
  }
  return opts[0]?.value ?? "";
}

// What the library is missing, and a button that goes and gets it. Browsing
// fills this in as a side effect, so the gaps are whatever was never opened --
// which on a big library is most of it.
function MetadataGaps() {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["metadataGaps"],
    queryFn: () => api.metadataGaps(),
  });
  // Only polls while a fill is running, then stops on its own.
  const { data: live } = useQuery({
    queryKey: ["metadataFill"],
    queryFn: () => api.metadataFillStatus(),
    refetchInterval: (query) => (query.state.data?.running ? 1500 : false),
    initialData: data?.state,
  });
  const running = !!live?.running;

  // A finished run leaves stale counts on screen; refresh them once.
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) qc.invalidateQueries({ queryKey: ["metadataGaps"] });
    wasRunning.current = running;
  }, [running, qc]);

  async function fill() {
    setBusy(true);
    try {
      await api.metadataFill();
      await qc.invalidateQueries({ queryKey: ["metadataFill"] });
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) {
    return <Skeleton className="mt-3 h-[9rem] w-full rounded-md" />;
  }

  const kinds = data?.kinds ?? [];
  const nothingToDo = kinds.every((k) => k.missing === 0 && (k.broken ?? 0) === 0);

  return (
    <>
      <Hint>
        Counted across the {data?.followed ?? 0} artists you follow. "Doesn't
        load" is a photo whose link is stored but serves nothing - an image host
        that goes down takes hundreds at once, and the row still looks filled in.
        Filling asks the sources above for whatever is blank or broken, downloads
        each answer before storing it, and leaves everything else alone,
        including the {data?.locked_images ?? 0} photos you chose by hand.
      </Hint>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Field</TableHead>
            <TableHead className="w-[5rem] text-right">Blank</TableHead>
            <TableHead className="w-[8rem] text-right">Doesn't load</TableHead>
            <TableHead>For example</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {kinds.map((kind) => {
            const broken = kind.broken ?? 0;
            return (
              <TableRow key={kind.key}>
                <TableCell>{kind.label}</TableCell>
                <TableCell className="text-right font-medium">{kind.missing}</TableCell>
                <TableCell className="text-right font-medium">
                  {kind.key === "images" ? broken : ""}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {kind.missing === 0 && broken === 0
                    ? "nothing missing"
                    : kind.examples.join(", ")}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>

      {running && (
        <div className="mt-3">
          <Progress value={live.total ? (live.done / live.total) * 100 : 0} />
          <p className="mt-1 text-sm text-muted-foreground">
            {live.done} of {live.total} artists · {live.images} photos,{" "}
            {live.bios} biographies, {live.genres} genre tags so far
            {live.cancelling ? " · stopping..." : ""}
          </p>
        </div>
      )}
      {!running && live?.message && (
        <p className="mt-2 text-sm text-muted-foreground">{live.message}</p>
      )}

      <Separator className="mt-4" />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy || running || nothingToDo} onClick={fill}>
          {running ? "Filling..." : "Fill what's missing"}
        </Button>
        {running && (
          <Button size="sm" variant="outline" onClick={() => api.metadataFillCancel()}>
            Stop
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={running}
          onClick={() => qc.invalidateQueries({ queryKey: ["metadataGaps"] })}
        >
          <LuRotateCw aria-hidden /> Check again
        </Button>
        <span className="text-sm text-muted-foreground">
          One lookup per artist, paced by each source - a few thousand artists
          takes minutes. Safe to leave running. Whatever it can't settle is
          listed artist by artist on the Artists page, where you can pick a
          picture by hand.
        </span>
      </div>
    </>
  );
}

// The playlist built from the release calendar: how far ahead it looks, and
// whether it rebuilds itself. Its own card because it spans two things --
// what's coming, and what your library already holds.
function HypePlaylist({
  get,
  set,
}: {
  get: (k: string) => string;
  set: (k: string, v: string) => void;
}) {
  const { data } = useQuery({
    queryKey: ["hypePlaylist"],
    queryFn: () => api.hypePlaylistState(),
    staleTime: 60_000,
  });
  const schedule = data?.schedule;
  const weekly = get("hype_playlist_weekly") === "true";

  if (!data) return <Skeleton className="mt-3 h-[8rem] w-full rounded-md" />;
  if (!data.available) {
    return (
      <Hint>
        Needs a library that can hold a playlist. Navidrome can; set it up under
        Where your music lives below.
      </Hint>
    );
  }

  return (
    <>
      <Hint>
        A playlist on {data.label} to get ready for what's coming: the songs you
        already have by the artists with a release due. Built from the Upcoming
        page, or on a schedule here. Rebuilding replaces it, so it never grows
        a second copy.
      </Hint>

      <Label htmlFor="hype_playlist_days">How far ahead to look</Label>
      <Select value={get("hype_playlist_days") || "7"}
              onValueChange={(v) => set("hype_playlist_days", v)}>
        <SelectTrigger id="hype_playlist_days" className="w-auto"><SelectValue /></SelectTrigger>
        <SelectContent>
          {(schedule?.windows ?? []).map((w) => (
            <SelectItem key={w.days} value={String(w.days)}>{w.label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Hint>
        A wider window means more artists, so a longer playlist: up to three
        songs each, sixty in total.
      </Hint>

      <SubHeading note="Or leave it off and build it by hand when you want it.">
        Rebuild it every week
      </SubHeading>
      <Check
        checked={weekly}
        onChange={(c) => set("hype_playlist_weekly", c ? "true" : "false")}
      >
        Rebuild on a schedule
      </Check>

      {weekly && (
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <Label htmlFor="hype_playlist_day">Day</Label>
            <Select value={get("hype_playlist_day") || "4"}
                    onValueChange={(v) => set("hype_playlist_day", v)}>
              <SelectTrigger id="hype_playlist_day" className="w-auto"><SelectValue /></SelectTrigger>
              <SelectContent>
                {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                  "Saturday", "Sunday"].map((name, i) => (
                  <SelectItem key={name} value={String(i)}>{name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="hype_playlist_time">Time</Label>
            <Input
              id="hype_playlist_time"
              type="time"
              className="w-auto"
              value={get("hype_playlist_time") || "08:00"}
              onChange={(e) => set("hype_playlist_time", e.target.value)}
            />
          </div>
        </div>
      )}
      <Hint>
        {weekly && schedule?.next_run
          ? `Next rebuild: ${new Date(schedule.next_run).toLocaleString()}.`
          : "Friday suits it: that's when most records come out."}
        {schedule?.last_run ? ` Last built ${timeAgo(schedule.last_run)}.` : ""}
      </Hint>
    </>
  );
}

// One ordered list per metadata field. Reordering is two buttons rather than
// drag-and-drop: these lists are three or four rows long and a keyboard user
// can work them.
function MetadataOrder() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["metadataOrder"],
    queryFn: () => api.metadataOrder(),
  });
  const [busy, setBusy] = useState("");

  const byKey = useMemo(() => {
    const out: Record<string, PluginInfo> = {};
    for (const p of data?.plugins ?? []) out[p.key] = p;
    return out;
  }, [data]);

  // A source you haven't set up can't answer anything, so it isn't listed --
  // but it keeps its place in the stored order, so setting it up later puts it
  // back where it was rather than at the bottom.
  const usable = (key: string) => byKey[key]?.configured !== false;
  const hiddenCount = useMemo(() => {
    const keys = new Set<string>();
    for (const field of data?.fields ?? []) {
      for (const key of field.order) if (!usable(key)) keys.add(key);
    }
    return keys.size;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, byKey]);

  async function move(capability: string, full: string[], from: number, to: number) {
    const shown = full.filter(usable);
    if (to < 0 || to >= shown.length) return;
    const reordered = shown.slice();
    const [row] = reordered.splice(from, 1);
    reordered.splice(to, 0, row!);
    // Put the reordered visible sources back into their own slots, leaving the
    // hidden ones exactly where they sit.
    let i = 0;
    const next = full.map((key) => (usable(key) ? reordered[i++]! : key));
    setBusy(capability);
    try {
      await api.setMetadataOrder(capability, next);
      await qc.invalidateQueries({ queryKey: ["metadataOrder"] });
    } finally {
      setBusy("");
    }
  }

  if (isLoading) {
    return (
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-[8.5rem] w-full rounded-md" />
        ))}
      </div>
    );
  }

  const fields = (data?.fields ?? []).map((field) => ({
    ...field,
    shown: field.order.filter(usable),
  }));
  // A field nothing can answer is not a choice, so the whole card goes.
  const answerable = fields.filter((field) => field.shown.length > 0);

  const groups = (data?.groups ?? []).map((group) => ({
    ...group,
    fields: answerable.filter((field) => field.group === group.key),
  })).filter((group) => group.fields.length > 0);

  return (
    <>
    {answerable.length === 0 && (
      <p className="mt-3 text-sm text-muted-foreground">
        Nothing is set up to look anything up yet. Switch on a source below.
      </p>
    )}
    {groups.map((group, index) => (
      <section key={group.key}>
        <Separator className={index === 0 ? "mt-4 mb-3" : "mt-6 mb-3"} />
        <div className="mb-3 flex flex-wrap items-baseline gap-x-2">
          <h3 className="font-semibold">{group.label}</h3>
          <span className="text-xs text-muted-foreground">{group.description}</span>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {group.fields.map((field) => (
        <div key={field.key} className="rounded-md border p-3">
          <div className="flex items-baseline justify-between gap-2">
            <h4 className="font-medium">{field.label}</h4>
            {field.shown.length > 1 && (
              <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
                asked in order
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-muted-foreground">{field.description}</p>
          <Separator className="my-2" />
          <ol className="flex flex-col gap-1">
            {field.shown.map((key, index) => {
              const plugin = byKey[key];
              return (
                <li
                  key={key}
                  className="flex items-center gap-2 rounded border bg-muted/30 px-2 py-1 text-sm"
                >
                  {field.shown.length > 1 && (
                    <span className="w-4 text-right text-xs text-muted-foreground">
                      {index + 1}
                    </span>
                  )}
                  <ServiceIcon name={plugin?.icon} size={14} />
                  <span>{plugin?.label ?? key}</span>
                  {field.shown.length > 1 && (
                    <span className="ml-auto flex gap-1">
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        title="Ask this source earlier"
                        disabled={index === 0 || busy === field.key}
                        onClick={() => move(field.key, field.order, index, index - 1)}
                      >
                        <LuChevronUp aria-hidden />
                      </Button>
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        title="Ask this source later"
                        disabled={index === field.shown.length - 1 || busy === field.key}
                        onClick={() => move(field.key, field.order, index, index + 1)}
                      >
                        <LuChevronDown aria-hidden />
                      </Button>
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
            </div>
          ))}
        </div>
      </section>
    ))}
    {hiddenCount > 0 && (
      <p className="mt-2 text-xs text-muted-foreground">
        {hiddenCount} {hiddenCount === 1 ? "source is" : "sources are"} hidden
        because {hiddenCount === 1 ? "it isn't" : "they aren't"} set up or
        switched on. They keep their place in the order and reappear as soon as
        they work.
      </p>
    )}
    </>
  );
}

function PluginFields({
  fields,
  get,
  set,
}: {
  fields: PluginConfigField[];
  get: (k: string) => string;
  set: (k: string, v: string) => void;
}) {
  return (
    <>
      {fields.map((f) =>
        f.type === "checkbox" ? (
          <div key={f.key} className="mt-3">
            <Check checked={get(f.key) === "true"} onChange={(c) => set(f.key, c ? "true" : "false")}>
              {f.label}
            </Check>
            {f.help && <Hint>{f.help}</Hint>}
          </div>
        ) : (
          <div key={f.key} className="mt-3">
            <Label htmlFor={f.key}>{f.label}</Label>
            {f.type === "select" ? (
              <Select value={selectValue(get(f.key), f.options)}
                      onValueChange={(v) => set(f.key, v)}>
                <SelectTrigger id={f.key} className="w-auto"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(f.options ?? []).map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : f.type === "password" || f.secret ? (
              // Keys, cookies and webhook URLs: masked, with a reveal button.
              <Secret
                id={f.key}
                value={get(f.key)}
                onChange={(v) => set(f.key, v)}
                placeholder={f.placeholder}
                multiline={f.type === "textarea"}
              />
            ) : f.type === "textarea" ? (
              <Textarea
                rows={1}
                id={f.key}
                wrap="off"
                autoComplete="off"
                placeholder={f.placeholder}
                value={get(f.key)}
                onChange={(e) => set(f.key, e.target.value)}
                className="h-9 min-h-0 resize-none overflow-hidden field-sizing-fixed font-mono text-xs whitespace-nowrap"
              />
            ) : (
              <Input
                id={f.key}
                type="text"
                autoComplete="off"
                placeholder={f.placeholder}
                value={get(f.key)}
                onChange={(e) => set(f.key, e.target.value)}
              />
            )}
            {f.help && <Hint>{f.help}</Hint>}
          </div>
        ),
      )}
    </>
  );
}

// Start/stop the bulk similar-artist scan (feeds Discover's Similar Artists
// tab). Lives with the Discover sources it feeds.
function SimilarScanSection() {
  const qc = useQueryClient();
  const { data: scan } = useQuery({
    queryKey: ["similarScan"],
    queryFn: () => api.similarScanStatus(),
    refetchInterval: (query) => (query.state.data?.running ? 2000 : false),
  });
  function toggle() {
    (scan?.running ? api.similarScanStop() : api.similarScanStart()).then(() =>
      qc.invalidateQueries({ queryKey: ["similarScan"] }),
    );
  }
  return (
    <Section>
      <Legend>Similar artists</Legend>
      <p className="mb-3 text-sm text-muted-foreground">
        Walk the owned library and record who each artist sounds like, from Last.fm.
        Feeds
        the Similar Artists tab on Discover, and fills in passively as artist pages
        are browsed. Rate-limited, so a big library takes a while.
      </p>
      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={toggle}>
          {scan?.running ? "Stop scan" : "Scan whole library"}
        </Button>
        <span className="text-sm text-muted-foreground">
          {scan?.running
            ? `Scanning ${scan.done}/${scan.total}${scan.current ? ` - ${scan.current}` : ""} · ${scan.recorded} suggestions`
            : scan?.message}
        </span>
      </div>
    </Section>
  );
}

function LibraryTab({
  get,
  set,
  testResult,
  onTest,
  scanResults,
  onScan,
}: {
  get: (k: string) => string;
  set: (k: string, v: string) => void;
  testResult: Record<string, string>;
  onTest: (kind: string, key: string) => void;
  scanResults: Record<string, string>;
  onScan: (key: string, quick: boolean) => void;
}) {
  const qc = useQueryClient();
  // Library metadata + live scan state via TanStack Query; poll while scanning.
  const { data, isLoading, isError } = useQuery({
    queryKey: ["plugins", "library"],
    queryFn: () => api.plugins("library"),
    staleTime: 30_000,
    refetchInterval: (query) =>
      query.state.data?.plugins?.some((p) => p.scanning || p.state === "queued")
        ? 2000
        : false,
  });
  // Only one scan runs at a time; this is the shared queue behind them all.
  const { data: queue } = useQuery({
    queryKey: ["scanQueue"],
    queryFn: () => api.scanQueue(),
    refetchInterval: (query) =>
      query.state.data?.running || query.state.data?.queue.length ? 2000 : false,
  });
  function cancelScan(key: string) {
    api.libraryScanCancel(key).finally(() => {
      qc.invalidateQueries({ queryKey: ["scanQueue"] });
      qc.invalidateQueries({ queryKey: ["plugins", "library"] });
    });
  }
  function cancelEverything() {
    api.scanQueueCancel().finally(() => {
      qc.invalidateQueries({ queryKey: ["scanQueue"] });
      qc.invalidateQueries({ queryKey: ["plugins", "library"] });
    });
  }
  const libraries = data?.plugins ?? [];
  const isOn = (p: PluginInfo) => !p.enabled_setting || get(p.enabled_setting) !== "false";
  const anyOn = libraries.some(isOn);

  return (
    <div>
      <h2 className="mb-1 text-lg font-semibold">Where your music lives</h2>
      <Hint>
        Libraries tell the app which albums you already own (drives track counts and
        the "owned" badges). Enable any combination - a local folder, a music server,
        or none at all.
      </Hint>
      {(queue?.running || (queue?.queue.length ?? 0) > 0) && (
        <Alert className="mb-3">
          <AlertDescription className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              {queue?.current
                ? `Scanning ${queue.current.label}${queue.current.cancelling ? " (stopping)" : ""} · ${queue.current.elapsed ?? 0}s`
                : "Scan starting"}
            </span>
            {(queue?.queue.length ?? 0) > 0 && (
              <span className="text-muted-foreground">
                Waiting: {queue!.queue.map((j) => j.label).join(", ")}
              </span>
            )}
            <Button size="xs" variant="outline" onClick={cancelEverything}>Cancel all</Button>
          </AlertDescription>
        </Alert>
      )}

      {!isLoading && !isError && !anyOn && (
        <p className="mb-3 text-muted-foreground">No library enabled - the app runs without one. Turn one on below to track owned music.</p>
      )}

      {isLoading ? (
        <p className="text-muted-foreground">Loading libraries...</p>
      ) : isError ? (
        <p className="text-muted-foreground">Could not load libraries.</p>
      ) : (
        libraries.map((p) => {
          const on = isOn(p);
          const prog = (p.progress || {}) as Record<string, number | string | undefined>;
          const liveCounts = [
            prog.files_seen != null ? `${prog.files_seen} files` : null,
            prog.albums != null ? `${prog.albums} albums` : null,
            (prog.artists_found ?? prog.artists) != null
              ? `${prog.artists_found ?? prog.artists} artists`
              : null,
          ].filter(Boolean).join(", ");
          const queued = p.state === "queued";
          const statusText = queued
            ? `Queued behind ${p.position} scan${p.position === 1 ? "" : "s"}...`
            : p.scanning
              ? `Scanning... ${liveCounts || prog.message || ""}`
              : p.error
                ? `Error: ${p.error}`
                : scanResults[p.key] ||
                  (p.last_scanned
                    ? `Last scanned: ${fmtAgo(p.last_scanned)} · ${p.artist_total ?? 0} artists · ${p.album_total ?? 0} albums${scheduleNote(p)}`
                    : `Never scanned.${scheduleNote(p)}`);
          return (
            <Section key={p.key}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <Legend>
                    <span className="flex items-center gap-2">
                      <ServiceIcon name={p.icon} size={18} />
                      {p.label}
                    </span>
                  </Legend>
                  {p.description && (
                    <p className="mb-1 text-sm text-muted-foreground">{p.description}</p>
                  )}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <div className="flex flex-wrap justify-end gap-2">
                    {p.has_test && (
                      <Button size="xs" variant="outline" onClick={() => onTest(p.kind, p.key)}>
                        Test
                      </Button>
                    )}
                    {on && (p.scanning || queued) ? (
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={!!p.cancelling}
                        onClick={() => cancelScan(p.key)}
                      >
                        {p.cancelling ? "Stopping..." : queued ? "Cancel queued" : "Cancel"}
                      </Button>
                    ) : (
                      on && (
                        <Button size="xs" variant="outline" onClick={() => onScan(p.key, false)}>
                          Scan now
                        </Button>
                      )
                    )}
                    {on && p.supports_quick && !p.scanning && !queued && (
                      <Button size="xs" variant="outline" onClick={() => onScan(p.key, true)}>
                        Quick scan
                      </Button>
                    )}
                    {p.enabled_setting && (
                      <Check checked={on} onChange={(c) => set(p.enabled_setting!, c ? "true" : "false")}>
                        Enabled
                      </Check>
                    )}
                  </div>
                  {p.has_test && testResult[p.key] && (
                    <p className="max-w-[22rem] truncate text-right text-sm text-muted-foreground" title={testResult[p.key]}>
                      {testResult[p.key]}
                    </p>
                  )}
                  {on && (
                    <p className="max-w-[22rem] truncate text-right text-xs text-muted-foreground" title={statusText}>
                      {statusText}
                    </p>
                  )}
                </div>
              </div>

              {on && (
                <>
                  {/* The same renderer the other plugin cards use, so a
                      dropdown is a dropdown and a token stays masked. */}
                  <PluginFields fields={p.config_fields} get={get} set={set} />

                  {p.key === "filesystem" && (
                    <div className="mt-3">
                      <Check checked={get("prefer_album_artist") !== "false"} onChange={(c) => set("prefer_album_artist", c ? "true" : "false")}>
                        Use the album-artist tag (fall back to track artist)
                      </Check>
                      <Hint>When scanning, prefer each file's album artist over the per-track artist.</Hint>
                    </div>
                  )}
                </>
              )}
            </Section>
          );
        })
      )}


    </div>
  );
}

function Check({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (c: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 font-semibold">
      <Checkbox checked={checked} onCheckedChange={(c) => onChange(c === true)} />
      <span>{children}</span>
    </label>
  );
}
