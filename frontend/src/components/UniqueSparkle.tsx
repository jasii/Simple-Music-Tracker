import * as React from "react";
// Straight from lucide, not react-icons: that package's bundled lucide set is
// old enough that its "Sparkles" is the same single star as its "Sparkle", so
// the two marks below were indistinguishable.
import { Sparkle, Sparkles } from "lucide-react";
import { cn } from "../lib/utils";

// Two marks, both stroked with the purple-to-blue gradient defined once in
// GradientDefs: one sparkle for a song the library hasn't got, the fuller
// sparkles for one that appears on no album by the artist. A track can carry
// either, or both -- a B-side you don't own is both new to you and new
// full stop.
const SPARKLE_GRADIENT = "url(#smtUniqueSparkle)";

// Both forward their props and their ref, so wrapping one in a tooltip trigger
// (which clones the child) can't get in the way.
export const UniqueSparkle = React.forwardRef<
  HTMLSpanElement,
  React.ComponentProps<"span"> & { label?: string }
>(function UniqueSparkle({ className, label, ...rest }, ref) {
  return (
    <span
      ref={ref}
      {...rest}
      aria-label={label}
      role={label ? "img" : undefined}
      className={cn("inline-flex flex-none leading-none", className)}
    >
      <Sparkle size="1em" stroke={SPARKLE_GRADIENT} aria-hidden />
    </span>
  );
});

export const AlbumExclusiveSparkles = React.forwardRef<
  HTMLSpanElement,
  React.ComponentProps<"span"> & { label?: string }
>(function AlbumExclusiveSparkles({ className, label, ...rest }, ref) {
  return (
    <span
      ref={ref}
      {...rest}
      aria-label={label}
      role={label ? "img" : undefined}
      className={cn("inline-flex flex-none leading-none", className)}
    >
      <Sparkles size="1em" stroke={SPARKLE_GRADIENT} aria-hidden />
    </span>
  );
});
