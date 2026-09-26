import { useColorMode } from "./ui/color-mode";

// Icons live in app/static/icons (from the selfh.st collection, fetched once
// and served from here). Each has three files: the plain mark, a light-coloured
// variant for dark backgrounds, and a dark-coloured one for light backgrounds.
const BASE = "/static/icons";

/**
 * A service's own mark: Navidrome, Deezer, Apple Music, Last.fm and the rest.
 *
 * Which file is used follows the colour mode, since a black wordmark on the
 * AMOLED theme is a black square. `name` comes from the backend, where each
 * plugin says what stands for it, so a service with no icon simply renders
 * nothing rather than a broken image.
 */
export function ServiceIcon({
  name,
  size = 16,
  className,
  title,
}: {
  name?: string | null;
  size?: number;
  className?: string;
  title?: string;
}) {
  const { colorMode } = useColorMode();
  if (!name) return null;
  const src = `${BASE}/${name}-${colorMode === "dark" ? "light" : "dark"}.svg`;
  return (
    <img
      src={src}
      alt=""
      aria-hidden={title ? undefined : true}
      title={title}
      width={size}
      height={size}
      className={className ? `flex-none ${className}` : "flex-none"}
      style={{ width: size, height: size }}
      loading="lazy"
      // A name with no file behind it leaves the row
      // alone rather than showing a broken image.
      onError={(e) => { e.currentTarget.style.display = "none"; }}
    />
  );
}
