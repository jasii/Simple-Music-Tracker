import {
  Box,
  Button,
  HStack,
  Heading,
  Input,
  NativeSelect,
  Stack,
  Text,
  Textarea,
  Wrap,
} from "@chakra-ui/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { NavConfig, Settings as SettingsT } from "../types";

const MTYPES = [
  ["album", "Albums"],
  ["ep", "EPs"],
  ["single", "Singles"],
];

function Hint({ children }: { children: React.ReactNode }) {
  return <Text color="fg.muted" fontSize="sm" mt="1" mb="3">{children}</Text>;
}
function Label({ children }: { children: React.ReactNode }) {
  return <Text fontWeight="semibold" mt="3">{children}</Text>;
}
function Legend({ children }: { children: React.ReactNode }) {
  return <Heading size="md" mb="2">{children}</Heading>;
}
function Section({ children }: { children: React.ReactNode }) {
  return <Box as="fieldset" borderWidth="1px" rounded="md" p="4" mb="4">{children}</Box>;
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

export default function Settings() {
  const [form, setForm] = useState<SettingsT>({});
  const [navTpl, setNavTpl] = useState<NavConfig | null>(null);
  const [navRows, setNavRows] = useState<NavRow[]>([]);
  const [saveResult, setSaveResult] = useState("");
  const [keyResult, setKeyResult] = useState("");
  const [cookieResult, setCookieResult] = useState("");
  const [webhookResult, setWebhookResult] = useState("");
  const [cacheStats, setCacheStats] = useState("Checking cache size...");
  const [staleBytes, setStaleBytes] = useState(0);
  const [purgeResult, setPurgeResult] = useState("");
  const [backupSections, setBackupSections] = useState(new Set(["settings", "artists", "artwork"]));
  const [importSections, setImportSections] = useState(new Set(["settings", "artists", "artwork"]));
  const [importResult, setImportResult] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const dragKey = useRef<string | null>(null);

  useEffect(() => {
    api.settings().then(setForm);
    api.getJSON<NavConfig>("/api/nav").then((n) => {
      setNavTpl(n);
      setNavRows(n.items.map((it) => ({ key: it.key, label: it.label, show: !it.hidden })));
    });
    loadCacheStats();
  }, []);

  function loadCacheStats() {
    api
      .cacheStats()
      .then((s: any) => {
        setStaleBytes(s.stale_bytes || 0);
        setCacheStats(
          `Cache: ${fmtBytes(s.total_bytes)} total, ${fmtBytes(s.stale_bytes)} stale (${s.stale_json_entries} cache entries, ${s.stale_art_files} artwork files).`,
        );
      })
      .catch(() => setCacheStats("Could not read cache size."));
  }

  const get = (k: string) => form[k] ?? "";
  const set = (k: string, v: string) => setForm((p) => ({ ...p, [k]: v }));
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

  function payload(): Record<string, string> {
    return {
      music_directory: get("music_directory"),
      prefer_album_artist: onUnlessFalse("prefer_album_artist") ? "true" : "false",
      lastfm_api_key: get("lastfm_api_key"),
      lastfm_cookie: get("lastfm_cookie"),
      discover_refresh_hours: get("discover_refresh_hours"),
      musicbrainz_contact: get("musicbrainz_contact"),
      check_interval_hours: get("check_interval_hours"),
      musicbrainz_rate_limit_ms: get("musicbrainz_rate_limit_ms"),
      discover_lastfm_enabled: onUnlessFalse("discover_lastfm_enabled") ? "true" : "false",
      discover_metacritic_enabled: onUnlessFalse("discover_metacritic_enabled") ? "true" : "false",
      default_monitor_types: get("default_monitor_types"),
      discography_autohide: get("discography_autohide"),
      webhook_url: get("webhook_url"),
      webhook_trigger: get("webhook_trigger"),
      webhook_lead_value: get("webhook_lead_value"),
      webhook_lead_unit: get("webhook_lead_unit"),
      webhook_method: get("webhook_method"),
      webhook_headers: get("webhook_headers"),
      webhook_template: get("webhook_template"),
      default_theme: get("default_theme"),
      hide_page_descriptions: onIfTrue("hide_page_descriptions") ? "true" : "false",
      nav_order: navOrder,
      nav_hidden: navHidden,
    };
  }

  function save() {
    setSaveResult("Saving...");
    api.saveSettings(payload()).then(() => {
      setSaveResult("Saved.");
      setTimeout(() => setSaveResult(""), 2500);
    }).catch(() => setSaveResult("Save failed."));
  }

  function testWebhook() {
    setWebhookResult("Sending...");
    api.saveSettings(payload())
      .then(() => api.testWebhook())
      .then((r) => setWebhookResult(r.ok ? `OK (${r.message})` : `Failed: ${r.message}`))
      .catch(() => setWebhookResult("Failed."));
  }
  function healthCheck(kind: "key" | "cookie") {
    const setR = kind === "key" ? setKeyResult : setCookieResult;
    setR("Checking...");
    api.saveSettings(payload())
      .then(() => (kind === "key" ? api.healthLastfmKey() : api.healthLastfmCookie()))
      .then((r) => setR((r.ok ? "OK - " : "Failed - ") + (r.message || "")))
      .catch(() => setR("Check failed."));
  }

  // Cache toggles save immediately (they live outside the main form).
  function saveOne(k: string, v: string) {
    set(k, v);
    api.saveSettings({ [k]: v });
  }

  function purge() {
    if (!window.confirm("Delete cached data for artists you no longer follow? Your settings and followed artists are untouched.")) return;
    setPurgeResult("Purging...");
    api.cachePurge().then((r: any) => {
      setPurgeResult(`Freed ${fmtBytes(r.freed_bytes)} (${r.art_files_removed} artwork files, ${r.json_entries_removed} cache entries).`);
      loadCacheStats();
    }).catch(() => setPurgeResult("Purge failed."));
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
    <Box>
      <Heading size="xl" mb="3">Settings</Heading>

      <Section>
        <Legend>Library</Legend>
        <Label>Music directory (inside the container)</Label>
        <Input value={get("music_directory")} onChange={(e) => set("music_directory", e.target.value)} />
        <Hint>Mount your library here, e.g. <code>-v /path/to/music:/music</code>.</Hint>
        <Check checked={onUnlessFalse("prefer_album_artist")} onChange={(c) => set("prefer_album_artist", c ? "true" : "false")}>
          Use the album-artist tag (fall back to track artist)
        </Check>
        <Hint>When scanning, prefer each file's album artist over the per-track artist.</Hint>
      </Section>

      <Section>
        <Legend>APIs</Legend>
        <Label>Last.fm API key</Label>
        <Input autoComplete="off" value={get("lastfm_api_key")} onChange={(e) => set("lastfm_api_key", e.target.value)} />
        <Hint>
          Used for artist bios and images. Get one at last.fm/api.{" "}
          <Button size="xs" variant="outline" onClick={() => healthCheck("key")}>Test key</Button>{" "}
          <Text as="span" color="fg.muted">{keyResult}</Text>
        </Hint>

        <Label>Last.fm session cookie (for Discover)</Label>
        <Textarea rows={2} autoComplete="off" placeholder="sessionid=...; csrftoken=..." value={get("lastfm_cookie")} onChange={(e) => set("lastfm_cookie", e.target.value)} />
        <Hint>
          The Discover page scrapes your personalised Last.fm "coming soon" releases, which require a login.
          Paste your last.fm cookies here (at minimum <code>sessionid=VALUE</code>). Kept only in your local database.{" "}
          <Button size="xs" variant="outline" onClick={() => healthCheck("cookie")}>Test cookie</Button>{" "}
          <Text as="span" color="fg.muted">{cookieResult}</Text>
        </Hint>

        <Label>Discover refresh interval (hours)</Label>
        <Input type="number" min={1} step={1} value={get("discover_refresh_hours")} onChange={(e) => set("discover_refresh_hours", e.target.value)} />
        <Hint>How often the Discover scrape refreshes in the background (default 24).</Hint>

        <Label>MusicBrainz contact (email or URL)</Label>
        <Input value={get("musicbrainz_contact")} onChange={(e) => set("musicbrainz_contact", e.target.value)} />
        <Hint>Sent in the MusicBrainz User-Agent, as their API etiquette requests.</Hint>

        <Label>Check interval (hours)</Label>
        <Input type="number" min={0.25} step={0.25} value={get("check_interval_hours")} onChange={(e) => set("check_interval_hours", e.target.value)} />

        <Label>MusicBrainz rate limit (ms between requests)</Label>
        <Input type="number" min={1000} step={100} value={get("musicbrainz_rate_limit_ms")} onChange={(e) => set("musicbrainz_rate_limit_ms", e.target.value)} />
        <Hint>Minimum gap between MusicBrainz calls. Kept at 1000ms or more.</Hint>
      </Section>

      <Section>
        <Legend>Discover sources</Legend>
        <Label>Show these sources on the Discover page</Label>
        <Wrap gap="4" mt="1">
          <Check checked={onUnlessFalse("discover_lastfm_enabled")} onChange={(c) => set("discover_lastfm_enabled", c ? "true" : "false")}>Last.fm</Check>
          <Check checked={onUnlessFalse("discover_metacritic_enabled")} onChange={(c) => set("discover_metacritic_enabled", c ? "true" : "false")}>Metacritic</Check>
        </Wrap>
        <Hint>Turn a source off to stop scraping it. Last.fm also needs a session cookie above.</Hint>
      </Section>

      <Section>
        <Legend>Monitoring defaults</Legend>
        <Label>Release types to monitor for newly followed artists</Label>
        <Wrap gap="4" mt="1">
          {MTYPES.map(([v, l]) => (
            <Check key={v} checked={csvHas("default_monitor_types", v)} onChange={(c) => toggleCsv("default_monitor_types", v, c)}>{l}</Check>
          ))}
        </Wrap>
        <Hint>Change per-artist on each artist's page. New follows start with this.</Hint>
      </Section>

      <Section>
        <Legend>Discography display</Legend>
        <Label>Auto-hide these categories on artist pages</Label>
        <Wrap gap="4" mt="1">
          {MTYPES.map(([v, l]) => (
            <Check key={v} checked={csvHas("discography_autohide", v)} onChange={(c) => toggleCsv("discography_autohide", v, c)}>{l}</Check>
          ))}
        </Wrap>
        <Hint>Collapsed by default when you open an artist; you can still expand them per page.</Hint>
      </Section>

      <Section>
        <Legend>Webhook (for "Notify" subscriptions)</Legend>
        <Label>Webhook URL</Label>
        <Input placeholder="https://..." value={get("webhook_url")} onChange={(e) => set("webhook_url", e.target.value)} />

        <Label>When to trigger</Label>
        <NativeSelect.Root>
          <NativeSelect.Field value={get("webhook_trigger") || "discovery"} onChange={(e) => set("webhook_trigger", e.target.value)}>
            <option value="discovery">As soon as a release is discovered</option>
            <option value="before_release">A set time before the release date</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>

        {beforeRelease && (
          <Box mt="2">
            <Label>Lead time before release</Label>
            <HStack gap="2">
              <Input type="number" min={0} step={1} w="6rem" value={get("webhook_lead_value")} onChange={(e) => set("webhook_lead_value", e.target.value)} />
              <NativeSelect.Root width="auto">
                <NativeSelect.Field value={get("webhook_lead_unit") || "days"} onChange={(e) => set("webhook_lead_unit", e.target.value)}>
                  {["hours", "days", "weeks"].map((u) => <option key={u} value={u}>{u}</option>)}
                </NativeSelect.Field>
                <NativeSelect.Indicator />
              </NativeSelect.Root>
            </HStack>
            <Hint>e.g. 7 days before release. Use 0 to fire on the release date.</Hint>
          </Box>
        )}

        <Label>Method</Label>
        <NativeSelect.Root>
          <NativeSelect.Field value={get("webhook_method") || "POST"} onChange={(e) => set("webhook_method", e.target.value)}>
            {["POST", "PUT", "GET"].map((m) => <option key={m} value={m}>{m}</option>)}
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>

        <Label>Headers (JSON object or "Key: Value" per line)</Label>
        <Textarea rows={3} value={get("webhook_headers")} onChange={(e) => set("webhook_headers", e.target.value)} fontFamily="mono" />

        <Label>Body template (JSON, supports {"{artist} {title} {release_date} {type} {image_url}"})</Label>
        <Textarea rows={8} fontFamily="mono" placeholder={navTpl?.default_webhook_template} value={get("webhook_template")} onChange={(e) => set("webhook_template", e.target.value)} />
        <Hint>Leave blank to use the built-in default payload.</Hint>

        <HStack gap="2">
          <Button variant="outline" onClick={testWebhook}>Send test webhook</Button>
          <Text color="fg.muted">{webhookResult}</Text>
        </HStack>
      </Section>

      <Section>
        <Legend>Navigation &amp; home page</Legend>
        <Hint>Drag the tabs to reorder. The tab at the top is your home page.</Hint>
        <Stack gap="2">
          {navRows.map((row, i) => {
            const isHome = i === 0;
            const locked = isHome || row.key === "settings";
            return (
              <HStack
                key={row.key}
                borderWidth="1px"
                borderColor={isHome ? "green.solid" : "border"}
                rounded="md"
                px="2"
                py="2"
                gap="3"
                draggable
                onDragStart={() => { dragKey.current = row.key; }}
                onDragEnd={() => { dragKey.current = null; }}
                onDragOver={(e) => onDragOver(e, row.key)}
                cursor="grab"
              >
                <Text color="fg.muted" userSelect="none">⋮⋮</Text>
                <Text fontWeight="semibold" flex="1">{row.label}</Text>
                {isHome && <Text fontSize="xs" color="green.solid" textTransform="uppercase">home</Text>}
                {row.key !== "settings" && (
                  <label style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
                    <input type="checkbox" checked={isHome ? true : row.show} disabled={locked} onChange={(e) => toggleShow(row.key, e.target.checked)} /> Show
                  </label>
                )}
              </HStack>
            );
          })}
        </Stack>
      </Section>

      <Section>
        <Legend>Appearance</Legend>
        <Label>Default theme</Label>
        <NativeSelect.Root>
          <NativeSelect.Field value={get("default_theme") || "dark"} onChange={(e) => set("default_theme", e.target.value)}>
            <option value="dark">Dark (AMOLED)</option>
            <option value="light">Light</option>
          </NativeSelect.Field>
          <NativeSelect.Indicator />
        </NativeSelect.Root>
        <Hint>The toggle in the header overrides this per device.</Hint>
        <Check checked={onIfTrue("hide_page_descriptions")} onChange={(c) => set("hide_page_descriptions", c ? "true" : "false")}>
          Hide page descriptions
        </Check>
        <Hint>Hides the short intro text under the title on the Discover, Upcoming and Following pages.</Hint>
      </Section>

      <HStack gap="3" mb="6">
        <Button onClick={save}>Save settings</Button>
        <Text color="fg.muted">{saveResult}</Text>
      </HStack>

      <Section>
        <Legend>Cache maintenance</Legend>
        <Check checked={onUnlessFalse("cache_images")} onChange={(c) => saveOne("cache_images", c ? "true" : "false")}>Cache images on disk</Check>
        <Hint>Saves album art and artist images under the data folder so they load fast and offline.</Hint>
        <Check checked={onUnlessFalse("purge_cache_on_unfollow")} onChange={(c) => saveOne("purge_cache_on_unfollow", c ? "true" : "false")}>
          Delete cached data when unfollowing
        </Check>
        <Hint>When you unfollow an artist, immediately remove their cached discography, album details, tracked releases and artwork.</Hint>
        <Text color="fg.muted" mb="2">{cacheStats}</Text>
        <HStack gap="2">
          <Button variant="outline" onClick={purge} disabled={!staleBytes}>Purge stale data</Button>
          <Text color="fg.muted">{purgeResult}</Text>
        </HStack>
      </Section>

      <Section>
        <Legend>Backup &amp; restore</Legend>
        <Hint>A backup is a ZIP file. Choose what to include (default: everything).</Hint>
        <Wrap gap="4">
          {[["settings", "Settings"], ["artists", "Artist information"], ["artwork", "Artwork"]].map(([v, l]) => (
            <Check key={v} checked={backupSections.has(v)} onChange={(c) => toggleSet(setBackupSections, v, c)}>{l}</Check>
          ))}
        </Wrap>
        <Box my="3"><Button variant="outline" onClick={downloadBackup}>Download backup</Button></Box>

        <Label>Restore from a backup file</Label>
        <Box mt="1"><input ref={fileRef} type="file" accept=".zip,application/zip,application/json,.json" /></Box>
        <Wrap gap="4" mt="2">
          {[["settings", "Settings"], ["artists", "Artist information"], ["artwork", "Artwork"]].map(([v, l]) => (
            <Check key={v} checked={importSections.has(v)} onChange={(c) => toggleSet(setImportSections, v, c)}>{l}</Check>
          ))}
        </Wrap>
        <HStack gap="2" mt="3">
          <Button variant="outline" onClick={doImport}>Import &amp; replace</Button>
          <Text color="fg.muted">{importResult}</Text>
        </HStack>
        <Hint>Only the ticked sections that exist in the file are restored. Older JSON backups still work.</Hint>
      </Section>
    </Box>
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
    <label style={{ display: "inline-flex", alignItems: "center", gap: "0.4rem", fontWeight: 600 }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {children}
    </label>
  );
}
