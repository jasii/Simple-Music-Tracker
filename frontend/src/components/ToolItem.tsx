import * as React from "react";

import { DropdownMenuItem } from "./ui/dropdown-menu";
import { cn } from "../lib/utils";

/**
 * One row in a Tools menu: an icon, what it's called, and what it does.
 *
 * The bare labels weren't enough -- "Full scan" and "Refresh following" read
 * as the same thing until you know one walks the music folder and the other
 * asks MusicBrainz and Last.fm -- so every tool carries its own one-liner.
 */
export function ToolItem({
  icon,
  title,
  description,
  disabled,
  onSelect,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description: string;
  disabled?: boolean;
  onSelect?: () => void;
  className?: string;
}) {
  return (
    <DropdownMenuItem
      disabled={disabled}
      onSelect={onSelect}
      className={cn("items-start gap-2 py-2", className)}
    >
      {icon && <span className="mt-0.5 flex-none">{icon}</span>}
      <span className="flex flex-col gap-0.5">
        <span className="font-medium leading-none">{title}</span>
        <span className="text-xs leading-snug text-muted-foreground">{description}</span>
      </span>
    </DropdownMenuItem>
  );
}
