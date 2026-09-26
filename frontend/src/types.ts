// Shapes mirror the Flask /api/* JSON responses (app/main.py). DB rows are
// passed through verbatim, so optional columns may be null.

export type Subscription = "none" | "subscribed" | "notify";
export type ReleaseType = "album" | "ep" | "single";

export interface Artist {
  id: number;
  name: string;
  sort_name: string;
  mbid: string | null;
  lastfm_url: string | null;
  image_url: string | null;
  // 1 = the image was chosen by hand in the artwork picker, so no refresh
  // replaces it (only the artist endpoint sends this).
  image_locked?: number;
  // Only the artist endpoint sends the bio; list endpoints leave it out (bios
  // were half the payload of a 2400-artist list).
  bio?: string | null;
  // Comma-separated genre tags stored on the artist (seeded when the artist is
  // created from a Discover suggestion); null when nothing was ever recorded.
  genres: string | null;
  subscription: Subscription;
  monitor_types: string; // comma list of album,ep,single
  ignored: number; // 0 | 1
  track_count: number;
  // Cached MusicBrainz discography counts by type; null until the artist's
  // discography has been loaded once.
  disc_albums?: number | null;
  disc_eps?: number | null;
  disc_singles?: number | null;
  // How many of each type the user owns (the owned subset of disc_*); null
  // until synced. total - owned = "missing".
  owned_albums?: number | null;
  owned_eps?: number | null;
  owned_singles?: number | null;
  last_checked: string | null;
  created_at: string;
}

export interface Release {
  id: number;
  artist_id: number;
  mbid: string | null;
  title: string;
  release_date: string | null;
  primary_type: string | null;
  image_url: string | null;
  notified?: number;
  created_at?: string;
}

// Upcoming releases join the artist in and add computed fields.
export interface UpcomingRelease extends Release {
  artist_name: string;
  subscription: Subscription;
  normalized_date: string;
  days_until: number;
}

export interface ArtistDetail extends Artist {
  bio: string | null;
  releases: Release[];
}

export interface Stats {
  artists: number;
  visible: number;
  ignored: number;
  subscribed: number;
  notify: number;
  following: number;
  tracked_releases: number;
  upcoming_week: number;
  upcoming_month: number;
}

export interface ArtistsResponse {
  total: number;
  count: number;
  artists: Artist[];
}

export interface DiscographyItem {
  title: string;
  release_date: string | null;
  primary_type: string;
  mbid?: string | null;
  image_url?: string | null;
  owned?: boolean;
  owned_sources?: { key: string; label: string }[];
  // Best quality among the copies you have ("FLAC 24bit", "MP3 320", ...).
  owned_format?: string | null;
  // Every quality you hold it in, best first: a record can be on one server
  // as FLAC and on another as 320.
  owned_formats?: string[];
  // Owned only because every song on it is already in the library elsewhere.
  owned_covered?: boolean;
  [k: string]: unknown;
}

export interface DiscographyResponse {
  mbid: string | null;
  error?: string;
  groups: Record<ReleaseType, DiscographyItem[]>;
  counts?: Record<ReleaseType, number>;
}

export interface DiscoverSourceTag {
  key: string | null;
  label: string | null;
}

export interface DiscoverItem {
  artist: string | null;
  album: string | null;
  image: string | null;
  context: string | null;
  // Artist names named inside `context`, so the feed can link each of them.
  context_artists?: string[] | null;
  mbid: string | null;
  release_date: string | null;
  normalized_date: string | null;
  genres?: string[];
  source: string;
  source_label: string;
  sources: DiscoverSourceTag[];
  in_library?: boolean;
  // The library already has this exact release (matched on release-group mbid,
  // else on artist + title).
  owned?: boolean;
  following?: boolean;
  // True when the artist is set to Notify (not merely followed).
  notify?: boolean;
  artist_id?: number | null;
  artist_url?: string | null;
  album_url?: string | null;
}

export interface DiscoverSourceStatus {
  key: string;
  label: string;
  configured: boolean;
  count: number;
  error: string | null;
  fetched_at?: number | null;
  stale?: boolean;
  refreshing?: boolean;
}

export interface DiscoverResponse {
  sources: DiscoverSourceStatus[];
  count: number;
  items: DiscoverItem[];
  refreshing: boolean;
}

// One Discover ignore rule (album null = whole artist hidden).
export interface DiscoverIgnore {
  id: number;
  artist: string;
  album: string | null;
  created_at: string;
}

// One similar-artist suggestion. artist_id is set when the artist is already in
// the local library (link internally instead); owned means the library actually
// has tracks by them.
export interface SimilarArtist {
  name: string;
  url?: string | null;
  artist_id?: number | null;
  owned?: boolean;
}

// Progress of the bulk similar-artist scan over the owned library.
export interface SimilarScanState {
  running: boolean;
  done: number;
  total: number;
  recorded: number;
  skipped_cached: number;
  current: string;
  message: string;
  started?: boolean;
}

// Progress of the bulk genre lookup over the suggestion ranking.
export interface SimilarEnrichState {
  running: boolean;
  done: number;
  total: number;
  fetched: number;
  skipped_cached: number;
  current: string;
  message: string;
  started?: boolean;
}

// One row of the "similar to many of yours, but not owned" ranking.
// Version, environment and scheduler state for the Help & Info pane.
export interface ScheduledTask {
  key: string;
  label: string;
  active: boolean;
  interval_seconds: number;
  last_run: number | null;
  next_run: number | null;
}

// One library scan: what it is and where it sits in the queue.
// How much of the followed library's artwork is already on local disk.
export interface ArtworkStatus {
  followed: number;
  with_image: number;
  on_disk: number;
  missing_image: number;
  running: boolean;
  done: number;
  total: number;
  warmed: number;
  filled: number;
  message: string;
}

export interface ScanJob {
  key: string;
  label: string;
  quick: boolean;
  queued_at: number;
  started_at?: number;
  elapsed?: number;
  cancelling?: boolean;
}

export interface ScanQueue {
  running: boolean;
  current: ScanJob | null;
  queue: ScanJob[];
  results: Record<string, { status: string; message: string; finished_at: number }>;
}

// A row that might be the same artist as the others in its group.
export interface DuplicateMember {
  id: number;
  name: string;
  mbid: string | null;
  subscription: Subscription;
  ignored: boolean;
  track_count: number;
  owned_albums: number;
  last_checked: string | null;
}

export interface DuplicateGroup {
  key: string;
  signature: string;
  reason: string;
  target: DuplicateMember;
  members: DuplicateMember[];
  dismissed: boolean;
}

export interface DuplicatesResponse {
  groups: DuplicateGroup[];
  dismissed: number;
}

// One configured library source and what it contributes to the totals.
export interface LibrarySourceInfo {
  key: string;
  label: string;
  scan_interval_hours: number;
  artists: number;
  albums: number;
  tracks: number;
  last_scanned: number | null;
}

export interface SystemInfo {
  version: string;
  libraries: LibrarySourceInfo[];
  python: string;
  sqlite: string;
  platform: string;
  database_path: string;
  database_bytes: number;
  artwork_path: string;
  music_directory: string;
  timezone: string;
  started_at: number;
  counts: { artists: number; following: number; releases: number; owned_albums: number };
  tasks: ScheduledTask[];
  links: Record<string, string>;
}

// The quality profile: which qualities are acceptable, best first.
export interface QualityProfile {
  qualities: string[];        // every quality the app knows, best first
  quality_order: string[];    // accepted qualities, user order
  clients: { key: string; label: string; configured: boolean }[];
  client_order: string[];
}

export interface AutograbPending {
  artist_id: number;
  artist: string;
  title: string;
  release_date: string | null;
  type: string | null;
}

export interface AutograbStatus {
  enabled: boolean;
  pending: AutograbPending[];
  count: number;
}

export interface AutograbRun {
  checked: number;
  sent: number;
  skipped: number;   // already owned, or already in the client
  failed: number;
  results: (AutograbPending & { sent?: boolean; error?: string; client?: string })[];
}

// One of the user's most-played Last.fm artists, matched to the library.
export interface LastfmArtist {
  name: string;
  playcount: number;
  url: string | null;
  image_url: string | null;
  artist_id: number | null;
  owned: boolean;
  subscription: Subscription;
}

export interface LastfmTopArtists {
  user: string;
  period: string;
  artists: LastfmArtist[];
  configured: boolean;
}

// A release the library doesn't own, or owns only in part.
export interface LibraryGap {
  artist_id: number;
  artist: string;
  title: string;
  type: string;            // album | ep | single
  release_date: string | null;
  year: string | null;
  mbid: string | null;
  image_url: string | null;
  // MusicBrainz secondary types (Compilation, Live, ...); empty for a proper
  // new release.
  secondary?: string[];
  // Incomplete only: the quality you have it in.
  owned_format?: string;
  // Incomplete only: tracks the fullest library copy has, of how many.
  have_tracks?: number;
  total_tracks?: number;
}

export interface LibraryGapsResponse {
  rows: LibraryGap[];
  total: number;
  missing_total: number;
  incomplete_total?: number;
  computed_at: number;
  // A download client is configured, so releases can be grabbed.
  grab_ready: boolean;
  // True while the gaps table is being rebuilt in the background.
  rebuilding: boolean;
}

export interface GrabResult {
  sent?: boolean;
  client?: string;
  message?: string;
  error?: string;
  // Soulseek grabs are followed as a download job (see DownloadJob).
  job_id?: number;
  // grab-missing: the tracks it went looking for.
  tracks?: string[];
}

// One file of a followed Soulseek download.
export interface DownloadFile {
  name: string;
  username: string;
  // pending | queued | downloading | done | failed | gave_up
  state: string;
  percent: number;
  attempts: number;
  error?: string | null;
}

// A Soulseek download followed until every file lands or is given up on.
export interface DownloadJob {
  id: number;
  artist_id: number | null;
  artist: string;
  title: string;
  client: string;
  kind: "album" | "tracks";
  format: string | null;
  // searching | downloading | complete | partial | failed | cancelled
  status: string;
  message: string | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
  total: number;
  done: number;
  failed: number;
  active: number;
  alternates: number;
  files: DownloadFile[];
}

// Who sounds like one artist, and which sources said so.
export interface SimilarArtistsResponse {
  artists: SimilarArtist[];
  source: string | null;
}

export interface SimilarRanking {
  name: string;
  count: number;
  url: string | null;
  site: string | null;
  artist_id: number | null;
  // Subscription of the matching library artist ('none' when they're in the
  // database but not followed, e.g. added by opening their page from here).
  subscription: Subscription;
  // Genre tags known for this artist; empty until they've been looked up (see
  // the bulk genre lookup on the Similar Artists tab).
  genres: string[];
  sources: string[];
}

// The ranking plus how much of it can be filtered by genre already.
export interface SimilarRankingsResponse {
  artists: SimilarRanking[];
  genres_known: number;
  genres_total: number;
}

// Lazy-loaded details for one row of that ranking (best-effort; all nullable).
export interface SimilarArtistInfo {
  image_url: string | null;
  genres: string[];
  bio: string | null;
  lastfm_url: string | null;
}

// One settings input a plugin needs (key maps to a /api/settings setting).
export interface PluginConfigField {
  key: string;
  label: string;
  // Absent for a plain text field, which is what most plugins declare.
  type?: "text" | "textarea" | "password" | "checkbox" | "select";
  // Only for "select": the choices, in order.
  options?: { value: string; label: string }[];
  // Masked with a reveal button: cookies, keys and webhook URLs that happen
  // to be long enough to want a textarea but shouldn't be legible by default.
  secret?: boolean;
  placeholder?: string;
  help?: string;
}

// Plugin metadata from /api/plugins (see app/plugins). Values live in settings.
export interface PluginInfo {
  kind: string;
  key: string;
  label: string;
  description: string;
  // Which service mark to show, as a file in app/static/icons; null for
  // anything that isn't a service (a folder on disk, your own covers).
  icon?: string | null;
  enabled: boolean;
  enabled_setting: string | null;
  configured: boolean;
  config_fields: PluginConfigField[];
  has_test: boolean;
  // Present for refreshable (discovery) plugins.
  refreshable?: boolean;
  last_scraped?: number | null; // epoch seconds, or null if never scraped
  item_count?: number;
  refreshing?: boolean;
  error?: string | null;
  // Metadata plugins: which fields this source can answer
  // (artist_image | artist_bio | artist_genres | album_art).
  provides?: string[];
  // Present for scannable (library) plugins.
  scannable?: boolean;
  supports_quick?: boolean;
  scanning?: boolean;
  // Libraries: hours between automatic scans, 0 when they only run on request.
  scan_interval_hours?: number;
  // Queue state from the scan coordinator: "idle" | "queued" | "running".
  state?: string;
  position?: number | null;
  cancelling?: boolean;
  last_scanned?: number | null; // epoch seconds, or null if never scanned
  artist_total?: number;
  album_total?: number;
  track_total?: number;
  progress?: { running?: boolean; message?: string; [k: string]: unknown };
}

export interface AlbumTrack {
  name: string;
  url?: string | null;
  duration?: number | null; // seconds
  preview_url?: string | null;
  // Last.fm plays for this track, and whether it's one of the album's standouts.
  playcount?: number | null;
  hot?: boolean;
  // Set on an EP/single's tracks: the library hasn't got this song.
  unique?: boolean;
  // This song appears on no album by the artist -- it only exists here.
  album_exclusive?: boolean;
  [k: string]: unknown;
}

export interface AlbumDetailResponse {
  artist: string;
  title: string;
  mbid?: string | null;
  image?: string | null;
  tracks: AlbumTrack[];
  artist_id: number | null;
  following: boolean;
  // The library already has this release.
  owned?: boolean;
  owned_format?: string | null;
  // Every quality you hold it in, best first.
  owned_formats?: string[];
  // Tracks the fullest library copy holds (0 = unknown).
  owned_tracks?: number;
  // Tracks on the shortest official edition: what a complete copy has.
  complete_tracks?: number | null;
  // The newest Soulseek download of this release, if any.
  download?: DownloadJob | null;
  owned_covered?: boolean;
  // MusicBrainz release types: "Album" | "EP" | "Single", plus any secondary
  // types (Compilation, Live, Remix, ...).
  primary_type?: string | null;
  secondary_types?: string[];
  // Whether the unique-track marks below are filled in yet.
  unique_ready?: boolean;
  // Whether the artist's album songs are known, which the second mark needs.
  album_songs_ready?: boolean;
  unique_running?: boolean;
  error?: string;
  [k: string]: unknown;
}

// One of an artist's best-known tracks. `stream` plays it: the full song from
// whichever library holds it (then `full` is true and `library` names it), else
// a 30-second sample from the public catalogues.
export interface ArtistTopTrack {
  name: string;
  url: string | null;
  playcount: number;
  mbid: string | null;
  preview: string | null;
  stream: string | null;
  library: string | null;
  // The library's own page for this song, when it has one (Navidrome's album
  // view, Plex's item page).
  library_url?: string | null;
  // Which service mark to show beside the credit.
  library_icon?: string | null;
  album: string | null;
  full: boolean;
  duration: number | null;
}

// How many songs each of an artist's EPs/singles keeps off their albums,
// keyed by release-group mbid (or "t:<normalised title>" without one).
export interface PassProgress {
  done?: number;
  total?: number;
}

export interface ArtistExclusives {
  ready: boolean;
  running: boolean;
  progress?: PassProgress;
  counts: Record<string, { unique: number; total: number }>;
  albums_read?: number;
  partial?: boolean;
  message?: string;
  error?: string;
}

export interface ArtistTopTracksResponse {
  artist: string;
  tracks: ArtistTopTrack[];
  error?: string;
}

// One album your library holds, and what it currently counts as.
export interface LibraryAlbumLink {
  album_key: string;
  sources: string[];
  format: string | null;
  matched_releases: string[];
  links: { rg_mbid: string; linked: boolean }[];
}

export interface ReleaseLink {
  mbid: string | null;
  title: string;
  type: string | null;
  release_date: string | null;
  owned: boolean;
  matched_albums: string[];
  format: string | null;
  linked: boolean;
}

export interface AlbumLinksResponse {
  artist: string;
  library: LibraryAlbumLink[];
  releases: ReleaseLink[];
  error?: string;
}

export interface NavItem {
  key: string;
  endpoint: string;
  label: string;
  path: string;
  hidden: boolean;
}

export interface NavConfig {
  items: NavItem[];
  home: string;
  home_path: string;
  default_theme: string;
  hide_page_descriptions: boolean;
  default_webhook_template?: string;
}

export type Settings = Record<string, string>;

export interface ScanState {
  running: boolean;
  [k: string]: unknown;
}

export interface RefreshState {
  running?: boolean;
  [k: string]: unknown;
}

// One image the app can use as an artist's artwork, and where it came from.
export interface ArtworkOption {
  url: string;
  // current | lastfm | deezer | cover
  source: string;
  label: string;
}

export interface ArtworkOptionsResponse {
  current: string | null;
  // True when the image was chosen by hand, so no refresh will replace it.
  locked: boolean;
  options: ArtworkOption[];
}

// One metadata field and the sources that answer it, best source first.
export interface MetadataField {
  key: string;
  label: string;
  description: string;
  // Which block of the settings page this list belongs in.
  group: string;
  order: string[];
}

export interface MetadataFieldGroup {
  key: string;
  label: string;
  description: string;
}

export interface MetadataOrderResponse {
  fields: MetadataField[];
  groups: MetadataFieldGroup[];
  plugins: PluginInfo[];
}

// One kind of missing metadata, as the Settings scanner reports it.
export interface MetadataGapKind {
  key: string;
  label: string;
  missing: number;
  // Photos only: a link is stored but no image ever came back from it.
  broken?: number;
  examples: string[];
}

export interface MetadataFillState {
  running: boolean;
  cancelling: boolean;
  done: number;
  total: number;
  images: number;
  bios: number;
  genres: number;
  message: string;
}

export interface MetadataGapsResponse {
  followed: number;
  locked_images: number;
  kinds: MetadataGapKind[];
  state: MetadataFillState;
}

// What the metadata sources add to a release beyond its tracklist. Each field
// comes from the first source in its own order that had one; `sources` says
// which source that was, per field.
export interface AlbumExtras {
  image_url: string | null;
  description: string | null;
  tags: string[];
  sources: Record<string, string>;
}

// One artist the artwork review tool is offering to fix.
export interface ArtworkReviewArtist {
  id: number;
  name: string;
  // The URL that isn't working, or null when nothing was ever stored.
  image_url: string | null;
  followed: boolean;
  track_count: number;
  dismissed: boolean;
}

export interface ArtworkReviewResponse {
  // False until an artwork pass has run: before that the list is the whole
  // library and means nothing.
  ready: boolean;
  scanned_at: number;
  total: number;
  dismissed: number;
  artists: ArtworkReviewArtist[];
}
