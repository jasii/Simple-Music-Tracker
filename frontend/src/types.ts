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
  bio: string | null;
  subscription: Subscription;
  monitor_types: string; // comma list of album,ep,single
  ignored: number; // 0 | 1
  track_count: number;
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
  mbid: string | null;
  release_date: string | null;
  normalized_date: string | null;
  genres?: string[];
  source: string;
  source_label: string;
  sources: DiscoverSourceTag[];
  in_library?: boolean;
  following?: boolean;
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

export interface AlbumTrack {
  name: string;
  url?: string | null;
  duration?: number | null; // seconds
  preview_url?: string | null;
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
  error?: string;
  [k: string]: unknown;
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
