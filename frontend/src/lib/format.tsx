import { HStack, Image, Link } from "@chakra-ui/react";

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
  const iconProps = {
    h: "24px",
    w: "24px",
    filter: "grayscale(1)",
    opacity: 0.45,
    transition: "filter 0.15s, opacity 0.15s",
    _hover: { filter: "grayscale(0)", opacity: 1 },
  } as const;
  return (
    <HStack gap="3" mt="1">
      <Link href={`https://www.last.fm/music/${a}/${al}`} target="_blank" rel="noopener noreferrer">
        <Image src="/static/last-fm.svg" alt="Last.fm" {...iconProps} />
      </Link>
      <Link href={mbHref} target="_blank" rel="noopener noreferrer">
        <Image src="/static/musicbrainz.svg" alt="MusicBrainz" {...iconProps} />
      </Link>
      <Link href={ytHref} target="_blank" rel="noopener noreferrer">
        <Image src="/static/youtube-music.svg" alt="YouTube Music" {...iconProps} />
      </Link>
    </HStack>
  );
}

export function msToDuration(ms?: number | null): string {
  if (!ms || ms <= 0) return "";
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}
