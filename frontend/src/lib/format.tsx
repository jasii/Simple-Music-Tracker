import { ServiceIcon } from "../components/ServiceIcon";

export function formatDate(iso?: string | null): string {
  if (!iso) return "date TBA";
  const d = new Date(iso + "T00:00:00");
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// "just now", "20 minutes ago", "yesterday", "3 days ago", then a plain date.
// Takes epoch seconds, the shape the API reports times in.
export function timeAgo(epochSeconds?: number | null): string {
  if (!epochSeconds) return "never";
  const seconds = Math.floor(Date.now() / 1000 - epochSeconds);
  if (seconds < 0) return "just now";
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: "short", day: "numeric",
  });
}

export function relativeDays(days: number): string {
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 0) return Math.abs(days) + " days ago";
  return "in " + days + " days";
}

// External lookup icons (Last.fm / MusicBrainz / YouTube Music) for a release.
// MusicBrainz links the release-group directly when its mbid is known.
export function ReleaseIcons({
  artist,
  album,
  mbid,
}: {
  artist: string;
  album: string;
  mbid?: string | null;
}) {
  const a = encodeURIComponent(artist || "");
  const al = encodeURIComponent(album || "");
  const mbHref = mbid
    ? "https://musicbrainz.org/release-group/" + encodeURIComponent(mbid)
    : "https://musicbrainz.org/search?type=release_group&method=indexed&query=" +
      encodeURIComponent((artist || "") + " " + (album || ""));
  const ytHref =
    "https://music.youtube.com/search?q=" +
    encodeURIComponent((artist || "") + " " + (album || ""));
  // Quiet until hovered: these are a footnote on the page, not its subject.
  const iconCls = "grayscale opacity-45 transition hover:grayscale-0 hover:opacity-100";
  return (
    <div className="mt-1 flex gap-3">
      <a href={`https://www.last.fm/music/${a}/${al}`} target="_blank" rel="noopener noreferrer"
         title="Last.fm">
        <ServiceIcon name="last-fm" size={22} className={iconCls} />
      </a>
      <a href={mbHref} target="_blank" rel="noopener noreferrer" title="MusicBrainz">
        <ServiceIcon name="musicbrainz" size={22} className={iconCls} />
      </a>
      <a href={ytHref} target="_blank" rel="noopener noreferrer" title="YouTube Music">
        <ServiceIcon name="youtube" size={22} className={iconCls} />
      </a>
    </div>
  );
}

export function msToDuration(ms?: number | null): string {
  if (!ms || ms <= 0) return "";
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Quality labels shortened to the usual shorthand: "FLAC 24bit"
// becomes FLAC24, "MP3 320" becomes 320. Anything unrecognised passes through.
const SHORT_QUALITY: Record<string, string> = {
  "FLAC 24bit": "FLAC24",
  "MP3 320": "320",
  "MP3 V0": "V0",
  "MP3 V2": "V2",
};

export function shortQuality(label?: string | null): string {
  if (!label) return "";
  return SHORT_QUALITY[label] ?? label;
}
