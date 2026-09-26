// Stroke gradients for the icons that carry meaning by colour: the hot-track
// flame and the unique-song sparkle. They live in the layout so any page can
// reference them by url(#id) -- an SVG gradient only has to exist somewhere in
// the document, not beside the icon using it.
export function GradientDefs() {
  return (
    <svg width="0" height="0" className="absolute" aria-hidden>
      <defs>
        <linearGradient id="smtHotFlame" x1="0" y1="1" x2="0" y2="0">
          <stop offset="0%" stopColor="#f97316" />
          <stop offset="100%" stopColor="#facc15" />
        </linearGradient>
        <linearGradient id="smtUniqueSparkle" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0%" stopColor="#a855f7" />
          <stop offset="100%" stopColor="#3b82f6" />
        </linearGradient>
      </defs>
    </svg>
  );
}
